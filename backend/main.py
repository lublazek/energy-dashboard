"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.health import router as health_router
from backend.api.routes import init_routes, router as routes_router
from backend.providers.entsoe.provider import ENTSOEProvider
from backend.scheduler import init_scheduler, shutdown_scheduler
from backend.settings import Settings
from backend.storage import InMemoryStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app startup and shutdown."""
    logger.info("Starting up...")

    settings = Settings()
    logging.basicConfig(level=settings.log_level)

    countries_config_path = PROJECT_ROOT / "config" / "countries.yaml"
    if not countries_config_path.exists():
        raise FileNotFoundError("Config file not found. Check config/countries.yaml.")

    with open(countries_config_path) as f:
        countries_config = yaml.safe_load(f)

    storage = InMemoryStore()
    init_routes(storage, countries_config)

    provider = ENTSOEProvider(settings.entsoe_api_key, countries_config_path)

    await init_scheduler(storage, provider, countries_config_path)

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_router)
app.include_router(health_router)

app.mount("/", StaticFiles(directory=PROJECT_ROOT / "frontend", html=True), name="static")
