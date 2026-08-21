# Energy Dashboard

Real-time electricity market dashboard for the Czech Republic, built on data from the
[ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).

Shows four live charts: day-ahead prices, total load, generation by source, and system
imbalance volumes. Backend is Python/FastAPI with a background scheduler; frontend is
plain HTML/JS with Chart.js — no database, no build step.

## Run it

1. Get a free API key from ENTSO-E (email transparency@entsoe.eu after registering).
2. ```bash
   uv sync
   cp .env.example .env   # fill in ENTSOE_API_KEY
   uv run uvicorn backend.main:app   # don't use --reload, pandas triggers a watch loop
   ```
3. Open http://127.0.0.1:8000 — the dashboard is served by the backend, so don't open
   `frontend/index.html` directly.

## Project layout

```
backend/    FastAPI app, scheduler, ENTSO-E provider, in-memory storage
frontend/   index.html + app.js (Chart.js) + style.css
config/     countries.yaml — enabled countries and bidding zones
docs/       architecture and ENTSO-E data source reference
```

## Documentation

- [CLAUDE.md](CLAUDE.md) — architecture overview and the `NormalizedSeries` contract.
- [docs/entsoe.md](docs/entsoe.md) — ENTSO-E series mapping, DataFrame shapes, platform quirks.
- [docs/architecture.md](docs/architecture.md) — configuration, fetch lifecycle, design decisions.
