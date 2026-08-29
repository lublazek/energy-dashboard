"""API routes for electricity data."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from backend.models import NormalizedSeries
from backend.storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

STALE_AFTER_SECONDS = 3600

_storage: Storage | None = None
_countries_config: dict | None = None
_default_country: str = "CZ"


def init_routes(storage: Storage, countries_config: dict, default_country: str) -> APIRouter:
    """Initialize routes with storage and config."""
    global _storage, _countries_config, _default_country
    _storage = storage
    _countries_config = countries_config
    _default_country = default_country
    return router


async def _serve(country: str, series_name: str) -> NormalizedSeries:
    """Return a stored series, annotated with its age at read time."""
    if not _storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    data = await _storage.get(country, series_name)
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {country}")

    return _with_age_and_staleness(data)


@router.get("/prices")
async def get_prices(country: str = Query("CZ")) -> NormalizedSeries:
    """Get day-ahead electricity prices."""
    return await _serve(country, "day_ahead_prices")


@router.get("/load")
async def get_load(country: str = Query("CZ")) -> NormalizedSeries:
    """Get total load (consumption)."""
    return await _serve(country, "load")


@router.get("/generation")
async def get_generation(country: str = Query("CZ")) -> NormalizedSeries:
    """Get generation per production type."""
    return await _serve(country, "generation")


@router.get("/imbalance")
async def get_imbalance(country: str = Query("CZ")) -> NormalizedSeries:
    """Get imbalance volumes."""
    return await _serve(country, "imbalance")


@router.get("/imbalance_prices")
async def get_imbalance_prices(country: str = Query("CZ")) -> NormalizedSeries:
    """Get imbalance prices."""
    return await _serve(country, "imbalance_prices")


@router.get("/countries")
async def get_countries() -> dict:
    """Get list of enabled countries."""
    if not _countries_config:
        raise HTTPException(status_code=500, detail="Config not initialized")

    enabled = [
        {
            "code": c["code"],
            "name": c["name"],
        }
        for c in _countries_config.get("countries", [])
        if c.get("enabled", False)
    ]

    return {"countries": enabled, "default": _default_country}


def _with_age_and_staleness(data: NormalizedSeries) -> NormalizedSeries:
    """Return a copy of `data` with age_seconds and stale filled in.

    A copy, not an in-place edit: `Storage.get` hands back the object it holds,
    so annotating it here would let a request write into state the scheduler
    owns, outside the store's lock.
    """
    if not data.fetched_at:
        return data.model_copy(update={"age_seconds": -1, "stale": True})

    fetched_at = data.fetched_at
    if fetched_at.tzinfo is None:
        # Defensive: everything we store is tz-aware UTC. Assuming UTC for a
        # naive value beats raising "can't subtract offset-naive and aware".
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)

    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    return data.model_copy(
        update={"age_seconds": int(age), "stale": age > STALE_AFTER_SECONDS}
    )
