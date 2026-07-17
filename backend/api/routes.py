"""API routes for electricity data."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from backend.models import NormalizedSeries
from backend.storage import Storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_storage: Storage | None = None
_countries_config: dict | None = None


def init_routes(storage: Storage, countries_config: dict) -> APIRouter:
    """Initialize routes with storage and config."""
    global _storage, _countries_config
    _storage = storage
    _countries_config = countries_config
    return router


@router.get("/prices")
async def get_prices(country: str = Query("CZ")) -> NormalizedSeries:
    """Get day-ahead electricity prices."""
    if not _storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    data = await _storage.get(country, "day_ahead_prices")
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {country}")

    _calculate_age_and_staleness(data)
    return data


@router.get("/load")
async def get_load(country: str = Query("CZ")) -> NormalizedSeries:
    """Get total load (consumption)."""
    if not _storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    data = await _storage.get(country, "load")
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {country}")

    _calculate_age_and_staleness(data)
    return data


@router.get("/generation")
async def get_generation(country: str = Query("CZ")) -> NormalizedSeries:
    """Get generation per production type."""
    if not _storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    data = await _storage.get(country, "generation")
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {country}")

    _calculate_age_and_staleness(data)
    return data


@router.get("/imbalance")
async def get_imbalance(country: str = Query("CZ")) -> NormalizedSeries:
    """Get imbalance volumes."""
    if not _storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    data = await _storage.get(country, "imbalance")
    if not data:
        raise HTTPException(status_code=404, detail=f"No data for {country}")

    _calculate_age_and_staleness(data)
    return data


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

    return {"countries": enabled}


def _calculate_age_and_staleness(data: NormalizedSeries) -> None:
    """Calculate age_seconds and stale flag based on fetched_at."""
    if data.fetched_at:
        age = (datetime.utcnow() - data.fetched_at.replace(tzinfo=None)).total_seconds()
        data.age_seconds = int(age)
        data.stale = age > 3600
    else:
        data.age_seconds = -1
        data.stale = True
