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

**`config/countries.yaml`** — one entry per country with `code`, `bidding_zone`, and `enabled`.
Only `enabled: true` entries get scheduled jobs. Parsed **once** in `main.py`'s lifespan and passed
as a dict to routes, provider and scheduler, so the three cannot disagree. Changes need a restart.

**Fetch windows** — `_get_fetch_interval_minutes()`, `_get_lookback_hours()` and
`_get_lookahead_hours()` in `backend/scheduler.py`:

| Series | Interval | Lookback | Lookahead |
|---|---|---|---|
| `day_ahead_prices` | 5 min | 48 h | 24 h |
| `load` | 5 min | `HISTORY_WINDOW_HOURS` | — |
| `generation` | 5 min | `HISTORY_WINDOW_HOURS` | — |
| `imbalance` | 5 min | `HISTORY_WINDOW_HOURS` | — |

Day-ahead prices are the only series with a **forward** window: they are published around midday
for the following day, and entsoe-py truncates the response to the requested range — so an `end`
of "now" would fetch tomorrow's prices and then discard them.

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
   `/api/health` serves. The skeleton for every job is seeded at registration, so health lists all
   four from the first request rather than looking like nothing was scheduled.
5. `provider.fetch()` runs the synchronous entsoe-py call on a worker thread via
   `asyncio.to_thread`, with a 30 s HTTP timeout. Both matter: these jobs execute on the event
   loop, so a direct blocking call would freeze every API request for the whole round trip, and
   entsoe-py's default `timeout=None` means a stalled connection never returns at all.
6. Any exception is caught, logged, and written to `_job_status`. **A failing series never takes
   down the scheduler or the other three series.** `/api/health` reports `degraded` when any job
   has an error or has never succeeded.
7. The frontend polls `/api/{series}` every 60 s and redraws.

## Design decisions

**No database.** Data lives in `InMemoryStore` and is lost on restart, which is fine because
every series is refetched at startup anyway. The consequence worth knowing: restarting the server
is the normal way to pick up backend edits, and it costs nothing.

**`Storage` and `Provider` as protocols.** `typing.Protocol` gives structural typing — a class
satisfies the protocol by having the right methods, without inheriting from it. These two are the
only forward-compatibility kept for Phase 2, so a Postgres store or a second TSO provider can drop
in without touching the scheduler. Everything else is allowed to be Phase-1 simple.

**Normalizers own every provider quirk.** The seam is deliberate: `client.py` returns raw
provider data, `app.js` consumes canonical data, and all the mess between them is concentrated in
`normalizers.py`. This is what makes a second provider possible at all — it only has to emit the
same `NormalizedSeries` shape and the same generation category keys.

Prices, load and imbalance share one `normalize_scalar_series()` body because they differ only in
name and unit. That is not just tidiness: entsoe-py returns a Series for prices, a one-column
DataFrame for load, and a Series for imbalance *despite* annotating it `-> pd.DataFrame`. The
coercion that absorbs this lives in one place, so a library that flip-flops can only break one
function. `resolution_minutes` is likewise measured from the index rather than declared, because
ENTSO-E resolutions change under you.

**Staleness is computed at read time, not fetch time.** Normalizers set `fetched_at` as
timezone-aware UTC; `routes.py` derives `age_seconds` and `stale` (threshold: 1 h) against
`datetime.now(timezone.utc)` when the request comes in, and returns a **copy** so a read never
writes into the object the store holds. Both sides must stay on aware UTC — a naive local
`fetched_at` compared against UTC silently pins `stale` to False, which defeats the whole point of
the flag.

**404 means "nothing fetched yet".** `routes.py` returns 404 when the store holds nothing for a
series. It's a data-availability signal, not a routing error. `/api/health` tells you why.

## Testing

`uv run pytest`. The suite covers `normalize_generation` and nothing else, on purpose: normalizers
are pure functions (DataFrame in, `NormalizedSeries` out) so they need no network, no scheduler and
no FastAPI, and they are where every provider quirk is concentrated. Route, scheduler and frontend
tests are out of scope for Phase 1.

The suite is split along the line that matters for Phase 2:

- `tests/entsoe_frames.py` — builders producing **entsoe-py-shaped** DataFrames (tz-aware index;
  flat or `(production_type, aggregation)` MultiIndex columns). This file is the one that dies if
  the project drops entsoe-py for the raw REST API.
- `tests/test_normalize_generation.py` — assertions on the **`NormalizedSeries` contract**, which
  is provider-independent. These survive a provider swap untouched, which makes them the safety
  net for that migration: swap the implementation, and the tests say whether the canonical output
  still holds.

Two consequences worth knowing before adding tests here:

- **`by_source` cannot distinguish NaN from 0.0.** `normalize_generation_sources` seeds every
  canonical category to `0.0`, so a test asserting "a missing fuel is not zero" passes against
  broken code. The effect of `min_count=1` is only observable on `_generation_columns` directly,
  or indirectly through `_trim_ragged_tail`.
- **A test is not finished until you have watched it fail.** Both invariants above were verified by
  deliberately breaking `normalizers.py` and confirming the suite went red — removing `min_count=1`
  fails two tests, removing the `"Actual Consumption"` skip inflates hydro 5×.
