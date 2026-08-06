"""APScheduler setup for background data fetching jobs."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.providers.base import Provider
from backend.storage import Storage

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_job_status: dict[str, dict] = {}


def _get_fetch_interval_minutes(series: str) -> int:
    """Get fetch interval for each series in minutes."""
    intervals = {
        "day_ahead_prices": 5,
        "load": 5,
        "generation": 5,
        "imbalance": 5,
    }
    return intervals.get(series, 30)


def _get_lookback_hours(series: str) -> int:
    """Get lookback window for each series in hours."""
    lookbacks = {
        "day_ahead_prices": 48,
        "load": 24,
        "generation": 24,
        "imbalance": 24,
    }
    return lookbacks.get(series, 24)


async def _fetch_job(
    series: str,
    country: str,
    provider: Provider,
    storage: Storage,
) -> None:
    """Scheduled job that fetches data for a (series, country) pair."""
    job_key = f"{series}:{country}"

    if job_key not in _job_status:
        _job_status[job_key] = {
            "last_fetch_attempt_utc": None,
            "last_fetch_success_utc": None,
            "last_error": None,
            "provider_used": None,
        }

    _job_status[job_key]["last_fetch_attempt_utc"] = datetime.utcnow()

    lookback_hours = _get_lookback_hours(series)
    end = datetime.utcnow()
    start = end - timedelta(hours=lookback_hours)

    try:
        logger.debug(f"Fetching {series} for {country} from {provider.name}")
        result = await provider.fetch(series, country, start, end)
        await storage.store(result)
        _job_status[job_key]["last_fetch_success_utc"] = datetime.utcnow()
        _job_status[job_key]["last_error"] = None
        _job_status[job_key]["provider_used"] = provider.name
        logger.info(f"Successfully fetched {series} for {country} from {provider.name}")
    except Exception as e:
        _job_status[job_key]["last_error"] = str(e)
        logger.error(f"Failed to fetch {series} for {country}: {e}")


async def init_scheduler(
    storage: Storage,
    provider: Provider,
    countries_config_path: Path,
) -> AsyncIOScheduler:
    """Initialize and return the APScheduler scheduler."""
    global _scheduler

    _scheduler = AsyncIOScheduler()

    with open(countries_config_path) as f:
        countries_data = yaml.safe_load(f)

    enabled_countries = [
        c["code"]
        for c in countries_data.get("countries", [])
        if c.get("enabled", False)
    ]

    series_list = ["day_ahead_prices", "load", "generation", "imbalance"]

    for series in series_list:
        for country in enabled_countries:
            interval_minutes = _get_fetch_interval_minutes(series)
            job_key = f"{series}:{country}"

            _scheduler.add_job(
                _fetch_job,
                "interval",
                minutes=interval_minutes,
                args=[series, country, provider, storage],
                id=job_key,
                name=f"Fetch {series} for {country}",
                max_instances=1,
                misfire_grace_time=60,
                # Without this, an interval trigger first fires at now + interval,
                # leaving the dashboard empty for up to 30 minutes after startup.
                next_run_time=datetime.now(),
            )

            logger.info(f"Scheduled fetch job for {series}:{country} every {interval_minutes}m (first run now)")

    _scheduler.start()
    logger.info("Scheduler started")
    return _scheduler


async def shutdown_scheduler() -> None:
    """Shutdown the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler shutdown")


def get_scheduler() -> AsyncIOScheduler | None:
    """Get the global scheduler instance."""
    return _scheduler


def get_job_status() -> dict[str, dict]:
    """Get status of all scheduled jobs."""
    return dict(_job_status)
