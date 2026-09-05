"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.api.health import router as health_router
from backend.api.routes import init_routes, router as routes_router
from backend.fx import FXConverter
from backend.providers.entsoe.provider import ENTSOEProvider
from backend.scheduler import init_scheduler, shutdown_scheduler
from backend.settings import Settings
from backend.storage import InMemoryStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown."""
    # Before the first log call: with no handler installed, the root lastResort
    # handler applies and drops anything below WARNING.
    settings = Settings()
    logging.basicConfig(level=settings.log_level)

    logger.info("Starting up...")

    countries_config_path = PROJECT_ROOT / "config" / "countries.yaml"
    if not countries_config_path.exists():
        raise FileNotFoundError("Config file not found. Check config/countries.yaml.")

    # Parsed once and passed as a dict, so routes, provider and scheduler cannot
    # disagree about which countries are enabled.
    with open(countries_config_path) as f:
        countries_config = yaml.safe_load(f)

    # Fallback FX rates only: backend/fx.py fetches the ECB daily fixing at
    # runtime and falls back to these when it cannot. A missing file is not
    # fatal — it just means an unreachable ECB leaves prices in their own
    # currency instead of converting at a pinned rate.
    fx_config_path = PROJECT_ROOT / "config" / "fx_rates.yaml"
    fx_rates: dict = {}
    if fx_config_path.exists():
        with open(fx_config_path) as f:
            fx_rates = yaml.safe_load(f) or {}
    else:
        logger.warning("config/fx_rates.yaml not found; no pinned FX fallback")

    storage = InMemoryStore()
    init_routes(storage, countries_config, settings.default_country)

    fx = FXConverter(
        fallback_rates=fx_rates.get("rates"),
        fallback_date=fx_rates.get("date"),
    )
    provider = ENTSOEProvider(settings.entsoe_api_key, countries_config, fx=fx)

    await init_scheduler(
        storage,
        provider,
        countries_config,
        settings.history_window_hours,
    )

    logger.info("App startup complete")

    yield

    logger.info("Shutting down...")
    await shutdown_scheduler()
    logger.info("App shutdown complete")


app = FastAPI(
    title="Energy Dashboard API",
    description="ENTSO-E electricity data dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

# No CORS middleware: the frontend is mounted by this same app at "/", so every
# request the dashboard makes is same-origin. Adding it back with
# allow_origins=["*"] plus allow_credentials would let any site your browser
# visits make credentialed requests here.
app.include_router(routes_router)
app.include_router(health_router)

app.mount("/", StaticFiles(directory=PROJECT_ROOT / "frontend", html=True), name="static")
