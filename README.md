# Energy Dashboard

Real-time electricity market dashboard for the Czech Republic and wider Central Europe
(CZ, DE, AT, PL, SK, HU, SI, HR, RO, CH, FR, NL, BE — edit `config/countries.yaml` to
add or disable one), built on data from the
[ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) — fetched through the raw
REST API, no client library.

Shows five live charts per country: day-ahead prices, total load, generation by source,
imbalance volumes, and imbalance prices. Backend is Python/FastAPI with a background
scheduler; frontend is plain HTML/JS with Chart.js — no database, no build step.

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
   what the rest do. The key travels in a request header, never the URL.
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

No network and no API key needed — the parsers and normalizers are pure functions, so the tests
feed them fixture XML documents and check what comes out. Takes about a second; worth running
before a commit.

Coverage is deliberately narrow: the XML parsers and the normalizers, because that is where the
provider quirks concentrate. See [docs/architecture.md](docs/architecture.md#testing).

## Project layout

```
backend/    FastAPI app, scheduler, raw ENTSO-E REST provider, in-memory storage
frontend/   index.html + app.js (Chart.js) + style.css
config/     countries.yaml — enabled countries and their ENTSO-E EIC area codes
docs/       architecture and ENTSO-E data source reference
tests/      pytest suite for the XML parsers and normalizers
```

## Documentation

- [CLAUDE.md](CLAUDE.md) — architecture overview and the `NormalizedSeries` contract.
- [docs/entsoe.md](docs/entsoe.md) — ENTSO-E series mapping, DataFrame shapes, platform quirks.
- [docs/architecture.md](docs/architecture.md) — configuration, fetch lifecycle, design decisions.
