# Energy Dashboard

Real-time electricity market dashboard for the Czech Republic, built on data from the
[ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).

Shows four live charts: day-ahead prices, total load, generation by source, and system
imbalance volumes. Backend is Python/FastAPI with a background scheduler; frontend is
plain HTML/JS with Chart.js — no database, no build step.

## Run it

1. Get a free API key from ENTSO-E (email transparency@entsoe.eu after registering).
2. Create a `.env` in the project root — it is gitignored, and is the only place the key
   belongs:
   ```
   ENTSOE_API_KEY=your-key-here
   DEFAULT_COUNTRY=CZ
   HISTORY_WINDOW_HOURS=24
   LOG_LEVEL=INFO
   ```
   Only `ENTSOE_API_KEY` is required; see [docs/architecture.md](docs/architecture.md) for
   what the rest do. Keep `LOG_LEVEL` at `INFO` — `DEBUG` makes entsoe-py log the full
   request URL, which contains your key.
3. ```bash
   uv sync
   uv run uvicorn backend.main:app   # don't use --reload, pandas triggers a watch loop
   ```
4. Open http://127.0.0.1:8000 — the dashboard is served by the backend, so don't open
   `frontend/index.html` directly.

## Tests

```bash
uv run pytest
```

No network and no API key needed — the normalizers are pure functions, so the tests feed them
fake DataFrames and check the `NormalizedSeries` that comes out. Takes about a second; worth
running before a commit.

Coverage is deliberately narrow: `normalize_generation` only, because that is where the provider
quirks concentrate. See [docs/architecture.md](docs/architecture.md#testing).

## Project layout

```
backend/    FastAPI app, scheduler, ENTSO-E provider, in-memory storage
frontend/   index.html + app.js (Chart.js) + style.css
config/     countries.yaml — enabled countries and bidding zones
docs/       architecture and ENTSO-E data source reference
tests/      pytest suite for the normalizers
```

## Documentation

- [CLAUDE.md](CLAUDE.md) — architecture overview and the `NormalizedSeries` contract.
- [docs/entsoe.md](docs/entsoe.md) — ENTSO-E series mapping, DataFrame shapes, platform quirks.
- [docs/architecture.md](docs/architecture.md) — configuration, fetch lifecycle, design decisions.
