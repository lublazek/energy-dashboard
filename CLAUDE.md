# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

**Energy Dashboard** — a real-time electricity market monitor for the Czech Republic and its
neighbours, extended to wider Central Europe (CZ, DE, AT, PL, SK, HU, SI, HR, RO, CH, FR, NL, BE
— the set lives in `config/countries.yaml`, not in code). It fetches five data series per country
from the ENTSO-E
Transparency Platform (day-ahead prices, total load, generation by source, imbalance volumes,
imbalance prices), stores them in memory, and renders them as live charts.

**Stack**: Python 3.13 + FastAPI + APScheduler; the ENTSO-E data comes from the **raw REST API**
(`requests` + stdlib `xml.etree`) — the entsoe-py library was deliberately removed. Chart.js via
CDN. No database, no build step.

**Scope**: Phase 2+ (per-TSO providers, database persistence, statistics, customizable
landing-page graphs) is deliberately out of scope. The `Provider`/`Storage` protocols and the
`NormalizedSeries` contract are the only forward-compatibility kept. Do not add a provider
registry or routing layer — the scheduler calls `ENTSOEProvider` directly.

## Architecture

```
Scheduler (interval job per series×country) → ENTSOEProvider.fetch() → ENTSOERawClient (HTTP)
                                          → xml_parsers → fx (money series) → normalizers
                                                                         → NormalizedSeries
                                                                                       ↓
Frontend (polls 60 s) ← /api/prices|load|generation|imbalance|imbalance_prices ← InMemoryStore
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
- `backend/providers/entsoe/` — `raw_client.py` does HTTP only and returns a **list** of XML
  documents (the platform zips multi-document responses — balancing data routinely arrives as
  `PK…` zip bytes; the token travels only in the `SECURITY_TOKEN` header, never the URL);
  `xml_parsers.py` holds all XML knowledge (namespace handling, position math, omitted-position
  fill, consumption exclusion, resolution merging, dual-pricing category selection, multi-document
  merge); `normalizers.py` turns parsed pandas objects into `NormalizedSeries`; `psr_types.py`
  maps psrType B-codes to canonical categories; `provider.py` dispatches by series name and runs
  the blocking HTTP call via `asyncio.to_thread`.
- `backend/fx.py` — converts the two money series to EUR against the ECB daily reference rates
  (refreshed at most once a day, shared across all jobs). `config/fx_rates.yaml` holds pinned
  fallback rates used only when the ECB is unreachable; a fallback conversion says so in the unit
  (`EUR/MWh (rate 2026-08-01)`). **A currency with no rate is left in its own unit, never
  relabelled EUR** — a CZK number under an EUR label is wrong by ~25x and reads as a price spike.
- `backend/storage.py` — `Storage` protocol + thread-safe `InMemoryStore` (data is lost on
  restart).
- `backend/api/routes.py` — serves the latest stored series and returns a **copy** annotated with
  `age_seconds` / `stale` (stale = older than 1 h), leaving the stored object untouched. Returns
  **404 when the store is empty for that series** — a 404 here means "nothing fetched yet", not a
  routing bug.
- `backend/api/health.py` — `/api/health`, last attempt/success/error per job, with an overall
  status derived from them (`starting` / `degraded` / `ok`). First stop when charts are empty.
- `config/countries.yaml` — one entry per country with the **EIC area code** the raw API
  addresses it by. Germany uses the DE-LU bidding zone EIC (ENTSO-E aggregates the four German
  TSOs into it). An enabled country without an `eic` fails at startup, on purpose.

### Where transformation belongs

`raw_client.py` returns raw XML text; `app.js` renders canonical data. **All provider quirks are
absorbed in the parsers and normalizers.** Never push ENTSO-E-specific naming into the frontend —
`app.js` drives its datasets off the canonical category keys in `GENERATION_COLORS`, and a second
provider must be able to emit the same keys.

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

A point carries **either** `v` (prices, load, imbalance volumes/prices) **or** `by_source`
(generation) — never both. Generation `by_source` keys are the canonical categories, and must
match `GENERATION_COLORS` in `app.js`:

`nuclear`, `lignite`, `hard_coal`, `gas`, `wind`, `solar`, `hydro`, `biomass`, `other`

## Tests

`uv run pytest` — no network, no API key, ~1 s. Scope is the parsers, normalizers and FX
conversion only, and that is deliberate; do not add route/scheduler/frontend tests without being
asked. `tests/test_fx.py` monkeypatches `requests.get` — the ECB feed is always a fixture, never
a real call.

`tests/entsoe_xml.py` builds raw-API-shaped XML fixtures and dies with the upstream format;
`tests/test_normalize_generation.py` / `test_normalize_scalar.py` assert on the
`NormalizedSeries` contract and must survive a provider change. Keep new tests on the right side
of that line — assert on canonical output, not on ENTSO-E shapes, unless the quirk *is* the
subject (those belong in `tests/test_xml_parsers.py`).

Note `by_source` always contains every canonical category (seeded to `0.0`), so it cannot express
"missing" — assert NaN-vs-zero on the parser output instead. See
[docs/architecture.md](docs/architecture.md#testing).

## Further reading

- [docs/entsoe.md](docs/entsoe.md) — the raw ENTSO-E REST API: endpoint catalogue, request
  anatomy, response shapes, and the platform quirks the parsers exist to absorb. **Read this
  before touching anything under `backend/providers/entsoe/`.**
- [docs/architecture.md](docs/architecture.md) — configuration, request/fetch lifecycle, and
  design decisions in more depth.
