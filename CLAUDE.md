# CLAUDE.md - Energy Dashboard

Guidance for Claude Code when working in this repository.

## What this is

**Energy Dashboard** — a real-time electricity market monitor for the Czech Republic (Phase 1).
It fetches 4 data series from the ENTSO-E Transparency Platform (day-ahead prices, total load,
generation by source, imbalance volumes), stores them in memory, and displays them as live
charts in a vanilla-JS frontend.

**Tech stack**: Python 3.13 + FastAPI + APScheduler + entsoe-py (backend), Chart.js via CDN (frontend), no database, no build step.

**Scope note**: Phase 2+ (multi-country, per-TSO providers, database persistence, statistics,
customizable landing-page graphs) is deliberately out of scope for now — the `Provider`/`Storage`
protocols and the `NormalizedSeries` contract are the only forward-compatibility we keep.

## Quick start

```bash
uv sync                                     # install dependencies
cp .env.example .env                        # then fill in ENTSOE_API_KEY
uv run uvicorn backend.main:app             # http://127.0.0.1:8000
```

Avoid `--reload` (pandas triggers a file-watch loop). Frontend polls the API every 60 s.

## Architecture

```
Scheduler (APScheduler, interval jobs) → ENTSOEProvider.fetch() → NormalizedSeries → InMemoryStore
                                                                                          ↓
Frontend (poll every 60 s)  ←  /api/prices|load|generation|imbalance  ←  Storage.get()
```

- `backend/main.py` — app lifespan: loads settings + `config/countries.yaml`, creates storage,
  provider, and scheduler; serves `frontend/` as static files.
- `backend/scheduler.py` — one interval job per (series, country): prices 30 min,
  load/generation 5 min, imbalance 15 min. Job status is exposed at `/api/health`
  (key format `"{series}:{country}"`). A failed fetch is logged, never crashes the scheduler.
- `backend/providers/entsoe/` — `client.py` wraps entsoe-py queries; `prices.py`, `load.py`,
  `generation.py`, `imbalance.py` normalize raw DataFrames; `psr_types.py` maps generation
  source types.
- `backend/providers/base.py` — `Provider` protocol (`supports`, `fetch`). There is only one
  provider now and the scheduler calls it directly; no registry/routing layer.
- `backend/storage.py` — `Storage` protocol + thread-safe `InMemoryStore` (data lost on restart).
- `backend/api/routes.py` — serves the latest stored series and computes `age_seconds` / `stale`
  (stale = older than 1 h).
- `frontend/` — `index.html`, `app.js` (Chart.js charts, 60 s polling), `style.css`.

## NormalizedSeries contract

Every provider must return:

```python
{
  "country": "CZ",
  "series": "day_ahead_prices",
  "unit": "EUR/MWh",
  "resolution_minutes": 60,
  "points": [{"t": "ISO-8601 UTC", "v": number}],   # generation uses "by_source": {...} instead of "v"
  "latest": {...},
  "fetched_at": "ISO-8601 UTC"
}
```

## Known symptoms to investigate (unfixed on purpose — learning exercises)

These were observed in earlier development; the code currently on disk has them. Diagnose and
fix them yourself before adding features:

1. **Empty dashboard after startup** — after starting the server, charts stay empty for up to
   30 minutes. Hint: watch the scheduler log line "Next wakeup is due…".
2. **Generation chart is all gray "other"** — no split by nuclear/solar/wind etc.
   Hint: inspect what `entsoe-py`'s `query_generation` actually returns as column names.
3. **Imbalance fetch crashes** — an exception from inside entsoe-py with pandas 3.x.
   Check `/api/health` for the error. (Separately: even when fixed, ENTSO-E publishes CZ
   imbalance sparsely, ~6–8 points/day — that part is authentic, not a bug.)
4. **Wrong `age_seconds` / stale flag** — timestamps in `fetched_at` don't line up with UTC.
   Hint: compare how the normalizers create `fetched_at` with how `routes.py` computes age.

## Configuration

- `.env` (see `.env.example`): `ENTSOE_API_KEY` (required), `DEFAULT_COUNTRY`,
  `HISTORY_WINDOW_HOURS`, `LOG_LEVEL` (set `DEBUG` for fetch details).
- `config/countries.yaml`: enabled countries + ENTSO-E bidding zone codes.
- Fetch intervals/lookbacks: `_get_fetch_interval_minutes()` / `_get_lookback_hours()`
  in `backend/scheduler.py`.

## Development

- Lint: `uv run ruff check backend/ --fix`
- Tests: `uv run pytest tests/` (no tests yet — normalizer unit tests with fixture
  DataFrames are the natural first ones to write)
- Verify data flow: `GET /api/health` shows last attempt/success/error per job.
