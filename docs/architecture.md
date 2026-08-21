# Architecture notes

Detail that doesn't fit in [CLAUDE.md](../CLAUDE.md). Start there for the module map and the
`NormalizedSeries` contract; this page covers configuration, the fetch lifecycle, and why things
are shaped the way they are.

## Configuration

**`.env`** (see `.env.example`) — loaded by `backend/settings.py` via `pydantic-settings`:

| Variable | Default | Purpose |
|---|---|---|
| `ENTSOE_API_KEY` | *(required)* | No default — startup fails without it. |
| `DEFAULT_COUNTRY` | `CZ` | Country the frontend requests by default. |
| `HISTORY_WINDOW_HOURS` | `24` | How much history the API returns. |
| `LOG_LEVEL` | `INFO` | See the API-key warning in [entsoe.md](entsoe.md). |

**`config/countries.yaml`** — one entry per country with `code`, `bidding_zone`, and `enabled`.
Only `enabled: true` entries get scheduled jobs. Read once at startup, so changes need a restart.

**Fetch intervals and lookbacks** — `_get_fetch_interval_minutes()` and `_get_lookback_hours()`
in `backend/scheduler.py`:

| Series | Interval | Lookback |
|---|---|---|
| `day_ahead_prices` | 5 min | 48 h |
| `load` | 5 min | 24 h |
| `generation` | 5 min | 24 h |
| `imbalance` | 5 min | 24 h |

Both helpers fall back to 30 min / 24 h for an unknown series. The uniform 5-minute interval is a
placeholder, not a considered choice — day-ahead prices are published once a day, so that's ~288
identical fetches daily. Raise it if ENTSO-E starts rate-limiting.

## Fetch lifecycle

1. `main.py`'s lifespan handler builds settings → `InMemoryStore` → `ENTSOEProvider` → scheduler.
2. `start_scheduler()` registers one APScheduler interval job per (series, country), with
   `id = "{series}:{country}"` and `max_instances=1` so a slow fetch can't overlap itself.
3. Each job passes `next_run_time=datetime.now()`, so jobs fire immediately at startup instead of
   waiting out one full interval. Without this the dashboard is empty for the first 5 minutes.
4. `_fetch_job()` computes `start`/`end` from the lookback, calls `provider.fetch()`, and stores
   the result. It records `last_fetch_attempt_utc`, `last_fetch_success_utc`, `last_error`, and
   `provider_used` in the module-level `_job_status` dict — which is what `/api/health` serves.
5. Any exception is caught, logged, and written to `_job_status`. **A failing series never takes
   down the scheduler or the other three series.**
6. The frontend polls `/api/{series}` every 60 s and redraws.

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
four normalizer modules. This is what makes a second provider possible at all — it only has to
emit the same `NormalizedSeries` shape and the same generation category keys.

**Staleness is computed at read time, not fetch time.** Normalizers set `fetched_at`;
`routes.py` derives `age_seconds` and `stale` (threshold: 1 h) when the request comes in. That
means the two sides have to agree on a timezone convention — a real source of subtle bugs.

**404 means "nothing fetched yet".** `routes.py` returns 404 when the store holds nothing for a
series. It's a data-availability signal, not a routing error. `/api/health` tells you why.
