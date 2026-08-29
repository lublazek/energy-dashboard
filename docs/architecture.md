# Architecture notes

Detail that doesn't fit in [CLAUDE.md](../CLAUDE.md). Start there for the module map and the
`NormalizedSeries` contract; this page covers configuration, the fetch lifecycle, and why things
are shaped the way they are.

## Configuration

**`.env`** — loaded by `backend/settings.py` via `pydantic-settings`. It is gitignored and is the
only place the API key belongs; there is deliberately no committed template, so nothing
key-shaped ever lives in the repo. See [README](../README.md) for the variables to put in it.

| Variable | Default | Purpose |
|---|---|---|
| `ENTSOE_API_KEY` | *(required)* | No default — startup fails without it. |
| `DEFAULT_COUNTRY` | `CZ` | Served by `/api/countries` as `default`; the frontend selects it on load. |
| `HISTORY_WINDOW_HOURS` | `24` | Lookback for every series except day-ahead prices (fixed 48 h). |
| `LOG_LEVEL` | `INFO` | `DEBUG` logs the request URL, which contains the key — see [entsoe.md](entsoe.md). |

The `.env` path is anchored to the project root, not the working directory, so the server can be
started from anywhere.

**`config/countries.yaml`** — one entry per country with `code`, `name`, `eic`, and `enabled`.
The `eic` is the ENTSO-E area code the raw API addresses the country by; one EIC serves all five
series, with Germany using the DE-LU bidding zone (ENTSO-E aggregates the four German TSOs into
it, imbalance included). An enabled country without an `eic` fails at startup — better than a
3 a.m. fetch error. Only `enabled: true` entries get scheduled jobs. Parsed **once** in
`main.py`'s lifespan and passed as a dict to routes, provider and scheduler, so the three cannot
disagree. Changes need a restart.

**Fetch windows** — `_get_fetch_interval_minutes()`, `_get_lookback_hours()` and
`_get_lookahead_hours()` in `backend/scheduler.py`:

| Series | Interval | Lookback | Lookahead |
|---|---|---|---|
| `day_ahead_prices` | 5 min | 48 h | 24 h |
| `load` | 5 min | `HISTORY_WINDOW_HOURS` | — |
| `generation` | 5 min | `HISTORY_WINDOW_HOURS` | — |
| `imbalance` | 5 min | `HISTORY_WINDOW_HOURS` | — |
| `imbalance_prices` | 5 min | `HISTORY_WINDOW_HOURS` | — |

Day-ahead prices are the only series with a **forward** window: they are published around midday
for the following day, and an `end` of "now" would exclude tomorrow's prices from the request
window entirely.

The interval helper falls back to 30 min for an unknown series. The uniform 5-minute interval is a
placeholder, not a considered choice — day-ahead prices are published once a day, so that's ~288
identical fetches daily. Raise it if ENTSO-E starts rate-limiting.

## Fetch lifecycle

1. `main.py`'s lifespan handler builds settings → `InMemoryStore` → `ENTSOEProvider` → scheduler.
2. `start_scheduler()` registers one APScheduler interval job per (series, country), with
   `id = "{series}:{country}"` and `max_instances=1` so a slow fetch can't overlap itself.
3. Each job passes `next_run_time=datetime.now()`, so jobs fire immediately at startup instead of
   waiting out one full interval. Without this the dashboard is empty for the first 5 minutes.
4. `_fetch_job()` computes `start`/`end` from the lookback and lookahead, calls `provider.fetch()`,
   and stores the result. It records `last_fetch_attempt_utc`, `last_fetch_success_utc`,
   `last_error`, and `provider_used` in the module-level `_job_status` dict — which is what
   `/api/health` serves. The skeleton for every job is seeded at registration, so health lists
   every job from the first request rather than looking like nothing was scheduled.
5. `provider.fetch()` runs the synchronous HTTP call (`requests`, 30 s timeout) on a worker
   thread via `asyncio.to_thread`. Both matter: these jobs execute on the event loop, so a direct
   blocking call would freeze every API request for the whole round trip, and without a timeout a
   stalled connection never returns at all. The XML parse itself is milliseconds and runs on the
   loop. The raw client returns a *list* of documents (zip responses are unpacked transparently);
   the `parse_*_documents` wrappers merge them.
6. Any exception — HTTP error, malformed XML, unexpected document root — is caught, logged, and
   written to `_job_status`. **A failing series never takes down the scheduler or the other
   series.** A "No matching data found" acknowledgement is *not* an error: it parses to an empty
   series, because confirmed absence and failure must stay distinguishable. `/api/health` reports
   `degraded` when any job has an error or has never succeeded.
7. The frontend polls `/api/{series}` every 60 s and redraws.

## Design decisions

**No database.** Data lives in `InMemoryStore` and is lost on restart, which is fine because
every series is refetched at startup anyway. The consequence worth knowing: restarting the server
is the normal way to pick up backend edits, and it costs nothing.

**`Storage` and `Provider` as protocols.** `typing.Protocol` gives structural typing — a class
satisfies the protocol by having the right methods, without inheriting from it. These two are the
only forward-compatibility kept for Phase 2, so a Postgres store or a second TSO provider can drop
in without touching the scheduler. Everything else is allowed to be Phase-1 simple.

**Parsers and normalizers own every provider quirk.** The seam is deliberate: `raw_client.py`
returns raw XML text, `app.js` consumes canonical data, and all the mess between them is
concentrated in `xml_parsers.py` (XML shape: namespaces, position math, omitted-position fill,
consumption exclusion, zip merging, flow-direction signs, currency) and `normalizers.py`
(canonical shape: ragged-tail trim, category grouping, resolution measurement). This is what
makes a second provider possible at all — it only has to emit the same `NormalizedSeries` shape
and the same generation category keys.

The four scalar series share one `normalize_scalar_series()` body because they differ only in
name and unit — and the unit itself is data for imbalance prices, which are settled in the
national currency (CZK for CZ, PLN for PL) and carry it in the document's `currency_Unit.name`.
`resolution_minutes` is measured from the index rather than declared, because ENTSO-E
resolutions change under you (SDAC moved day-ahead prices from 60 to 15 minutes).

**Staleness is computed at read time, not fetch time.** Normalizers set `fetched_at` as
timezone-aware UTC; `routes.py` derives `age_seconds` and `stale` (threshold: 1 h) against
`datetime.now(timezone.utc)` when the request comes in, and returns a **copy** so a read never
writes into the object the store holds. Both sides must stay on aware UTC — a naive local
`fetched_at` compared against UTC silently pins `stale` to False, which defeats the whole point of
the flag.

**404 means "nothing fetched yet".** `routes.py` returns 404 when the store holds nothing for a
series. It's a data-availability signal, not a routing error. `/api/health` tells you why.

## Testing

`uv run pytest`. The suite covers the parsers and normalizers and nothing else, on purpose: both
are pure functions (XML text / pandas in, pandas / `NormalizedSeries` out) so they need no
network, no scheduler and no FastAPI, and they are where every provider quirk is concentrated.
Route, scheduler and frontend tests are out of scope for Phase 1.

The suite is split along the line that matters:

- `tests/entsoe_xml.py` — builders producing **raw-API-shaped XML** documents (real namespaces,
  Period/Point/position structure, omitted positions as `None` entries). This file dies with the
  upstream format.
- `tests/test_xml_parsers.py` — the XML → pandas layer: timestamp reconstruction, the
  omitted-position fill, consumption exclusion, flow-direction signs, currency extraction,
  multi-document merging, and the HTTP-200 acknowledgement trap.
- `tests/test_normalize_generation.py` / `test_normalize_scalar.py` — assertions on the
  **`NormalizedSeries` contract**, which is provider-independent. These survived the entsoe-py →
  raw-REST migration with their assertion halves untouched — the split doing exactly the job it
  was designed for.

Two things worth knowing before adding tests here:

- **`by_source` cannot distinguish NaN from 0.0.** `normalize_generation_sources` seeds every
  canonical category to `0.0`, so a test asserting "a missing fuel is not zero" passes against
  broken code. Assert NaN-vs-zero on the parser's DataFrame instead.
- **A test is not finished until you have watched it fail.** The key invariants were verified by
  deliberately breaking the parser and confirming the suite went red — removing the consumption
  exclusion inflates hydro 5×, disabling the omitted-position fill fails the fill tests.
