# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Energy Dashboard** — a real-time electricity market monitor for the Czech Republic (Phase 1).
It fetches 4 data series from the ENTSO-E Transparency Platform (day-ahead prices, total load,
generation by source, imbalance volumes), stores them in memory, and displays them as live
charts in a vanilla-JS frontend.

**Tech stack**: Python 3.13 + FastAPI + APScheduler + entsoe-py (backend), Chart.js via CDN
(frontend), no database, no build step.

**Scope note**: Phase 2+ (multi-country, per-TSO providers, database persistence, statistics,
customizable landing-page graphs) is deliberately out of scope — the `Provider`/`Storage`
protocols and the `NormalizedSeries` contract are the only forward-compatibility we keep.
Do not add a provider registry/routing layer; the scheduler calls `ENTSOEProvider` directly.

## Commands

```bash
uv run --no-sync uvicorn backend.main:app    # run at http://127.0.0.1:8000
uv run ruff check backend/ --fix             # lint
uv run pytest tests/                         # tests (tests/ does not exist yet)
uv run pytest tests/test_x.py::test_y        # single test
```

**Use `--no-sync`.** The `.venv` was originally built with pip, so several packages lack a
`RECORD` manifest. Plain `uv run` therefore tries to reinstall them on every launch and fails
with `Access is denied (os error 5)` when VS Code's Python extension holds a file lock. To fix
permanently: close VS Code, `Remove-Item -Recurse -Force .venv`, then `uv sync`.

**Never use `--reload`** — pandas triggers a file-watch loop.

Since data lives only in memory and is refetched on startup, restarting the server is the normal
way to pick up backend edits.

## Architecture

```
Scheduler (APScheduler interval jobs) → ENTSOEProvider.fetch() → normalizer → NormalizedSeries
                                                                                    ↓
Frontend (polls 60 s) ← /api/prices|load|generation|imbalance ← Storage.get() ← InMemoryStore
```

- `backend/main.py` — lifespan: loads settings + `config/countries.yaml`, creates storage,
  provider, scheduler; mounts `frontend/` as static files at `/`. The dashboard is served by
  the backend — never open `index.html` directly.
- `backend/scheduler.py` — one interval job per (series, country), keyed `"{series}:{country}"`.
  Jobs pass `next_run_time=datetime.now()` so they fire at startup instead of after one full
  interval. A failed fetch is recorded in `_job_status` and logged, never crashes the scheduler.
- `backend/providers/entsoe/` — `client.py` wraps entsoe-py queries and does no transformation;
  `prices.py`/`load.py`/`generation.py`/`imbalance.py` normalize raw DataFrames;
  `psr_types.py` maps generation types to canonical categories.
- `backend/storage.py` — `Storage` protocol + thread-safe `InMemoryStore` (data lost on restart).
- `backend/api/routes.py` — serves latest stored series, computes `age_seconds`/`stale`
  (stale = older than 1 h). Returns **404 when the store is empty for that series** — a 404 here
  means "no data fetched yet", not a routing bug.
- `backend/api/health.py` — `/api/health`, last attempt/success/error per job. First stop when
  charts are empty.

### Where transformation belongs

`client.py` returns raw provider data; `app.js` renders canonical data. **All provider quirks are
absorbed in the normalizers.** Do not push ENTSO-E-specific naming into the frontend — `app.js`
drives its datasets off the canonical category keys in `GENERATION_COLORS`, and a second provider
must be able to emit the same keys.

### NormalizedSeries contract

```python
{
  "country": "CZ", "series": "day_ahead_prices", "unit": "EUR/MWh",
  "resolution_minutes": 60,
  "points": [{"t": "ISO-8601 UTC", "v": number}],   # generation uses "by_source": {...}, not "v"
  "latest": {...}, "fetched_at": "ISO-8601 UTC",
}
```

Generation categories (`by_source` keys, must match `GENERATION_COLORS` in `app.js`):
`nuclear`, `lignite`, `hard_coal`, `gas`, `wind`, `solar`, `hydro`, `biomass`, `other`.

## ENTSO-E gotchas

- **`query_generation` returns a MultiIndex**, not PSR codes: columns are tuples of
  `(production_type_name, aggregation)`, e.g. `('Nuclear', 'Actual Aggregated')`. Level 1 may be
  `'Actual Consumption'` (pumped storage) — that is load, not generation; skip it or it inflates
  hydro. `psr_types.GENERATION_TYPE_MAP` is keyed by the level-0 **names** for this reason.
- **Imbalance is genuinely sparse** for CZ (~6–8 points/day). That is authentic data, not a bug.
- **503s happen.** The platform goes down for stretches; fetch errors surface in `/api/health`
  while the scheduler keeps running.
- **`LOG_LEVEL=DEBUG` logs your API key** — entsoe-py logs full request URLs including
  `securityToken`. Redact before sharing logs; prefer `INFO` unless debugging fetches.

## Known symptoms to investigate (unfixed on purpose — learning exercises)

The repo owner is learning; these are left deliberately. **Coach with hints; do not fix them
unless explicitly asked to.**

1. **Imbalance fetch crashes** — an exception from inside entsoe-py with pandas 3.x.
   Check `/api/health` for the error.
2. **Wrong `age_seconds` / stale flag** — all four normalizers build `fetched_at` with
   `datetime.now(tz=None)` (local time, 8 spots — both the empty and populated branches) while
   `routes.py` compares against `datetime.utcnow()`. Ruff flags these as `DTZ005`/`DTZ003`.
   In CEST that skews age by ~2 h, which trips the 1 h stale threshold immediately.

Fixed already (see commit `1cdfab2`): empty dashboard on startup, and generation charting as
all-gray "other".

## Configuration

- `.env` (see `.env.example`): `ENTSOE_API_KEY` (required), `DEFAULT_COUNTRY`,
  `HISTORY_WINDOW_HOURS`, `LOG_LEVEL`.
- `config/countries.yaml` — only `enabled: true` countries get scheduled jobs.
- Fetch intervals/lookbacks: `_get_fetch_interval_minutes()` / `_get_lookback_hours()` in
  `backend/scheduler.py`. All series currently poll every 5 min, which is far more often than
  day-ahead prices change (published once daily) — worth raising if ENTSO-E rate-limits.

## Testing

No `tests/` directory exists yet. Normalizer unit tests with fixture DataFrames are the natural
first ones — especially `normalize_generation`, where a fixture with a MultiIndex including an
`'Actual Consumption'` column would lock in the two gotchas above.
