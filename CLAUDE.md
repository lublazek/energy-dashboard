# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

**Energy Dashboard** — a real-time electricity market monitor for the Czech Republic (Phase 1).
It fetches four data series from the ENTSO-E Transparency Platform (day-ahead prices, total load,
generation by source, imbalance volumes), stores them in memory, and renders them as live charts.

**Stack**: Python 3.13 + FastAPI + APScheduler + entsoe-py; Chart.js via CDN. No database, no
build step.

**Scope**: Phase 2+ (multi-country, per-TSO providers, database persistence, statistics,
customizable landing-page graphs) is deliberately out of scope. The `Provider`/`Storage`
protocols and the `NormalizedSeries` contract are the only forward-compatibility kept. Do not
add a provider registry or routing layer — the scheduler calls `ENTSOEProvider` directly.

## Architecture

```
Scheduler (APScheduler interval jobs) → ENTSOEProvider.fetch() → normalizer → NormalizedSeries
                                                                                    ↓
Frontend (polls 60 s) ← /api/prices|load|generation|imbalance ← Storage.get() ← InMemoryStore
```

- `backend/main.py` — lifespan startup: loads settings + `config/countries.yaml` (parsed **once**
  and passed as a dict to routes, provider and scheduler), creates storage, provider, scheduler;
  mounts `frontend/` as static files at `/`. **The dashboard is served by the backend — never open
  `index.html` directly.** No CORS middleware: the frontend is same-origin by construction.
- `backend/scheduler.py` — one interval job per (series, country), keyed `"{series}:{country}"`.
  Jobs pass `next_run_time=datetime.now()` so they fire at startup rather than after one full
  interval. A failed fetch is recorded in `_job_status` and logged — it never crashes the scheduler.
  Day-ahead prices are the only series with a forward window (`_get_lookahead_hours`).
- `backend/providers/base.py` — the `Provider` protocol.
- `backend/providers/entsoe/` — `client.py` wraps entsoe-py queries and does no transformation;
  `provider.py` dispatches by series name and runs the blocking call via `asyncio.to_thread`;
  `normalizers.py` holds `normalize_scalar_series()` (prices/load/imbalance) and
  `normalize_generation()`; `psr_types.py` maps generation types to canonical categories.
- `backend/storage.py` — `Storage` protocol + thread-safe `InMemoryStore` (data is lost on
  restart). The lock is load-bearing: fetches run on a worker thread.
- `backend/api/routes.py` — serves the latest stored series and returns a **copy** annotated with
  `age_seconds` / `stale` (stale = older than 1 h), leaving the stored object untouched. Returns
  **404 when the store is empty for that series** — a 404 here means "nothing fetched yet", not a
  routing bug.
- `backend/api/health.py` — `/api/health`, last attempt/success/error per job, with an overall
  status derived from them (`starting` / `degraded` / `ok`). First stop when charts are empty.

### Where transformation belongs

`client.py` returns raw provider data; `app.js` renders canonical data. **All provider quirks are
absorbed in the normalizers.** Never push ENTSO-E-specific naming into the frontend — `app.js`
drives its datasets off the canonical category keys in `GENERATION_COLORS`, and a second provider
must be able to emit the same keys.

### NormalizedSeries contract

Defined as Pydantic models in `backend/models.py`:

```python
{
  "country": "CZ", "series": "day_ahead_prices", "unit": "EUR/MWh",
  "resolution_minutes": 60,            # measured from the index, not declared
  "points": [{"t": datetime, "v": float | None, "by_source": dict | None}],
  "latest": Point | None,
  "fetched_at": datetime | None,       # timezone-aware UTC
  "stale": bool, "age_seconds": int,   # filled by routes.py, not the normalizers
}
```

A point carries **either** `v` (prices, load, imbalance) **or** `by_source` (generation) — never
both. Generation `by_source` keys are the canonical categories, and must match `GENERATION_COLORS`
in `app.js`:

`nuclear`, `lignite`, `hard_coal`, `gas`, `wind`, `solar`, `hydro`, `biomass`, `other`

## Further reading

- [docs/entsoe.md](docs/entsoe.md) — ENTSO-E data source mapping, DataFrame shapes returned by
  each entsoe-py call, and the provider quirks the normalizers exist to absorb. **Read this
  before touching anything under `backend/providers/entsoe/`.**
- [docs/architecture.md](docs/architecture.md) — configuration, request/fetch lifecycle, and
  design decisions in more depth.
