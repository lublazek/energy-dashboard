"""APScheduler setup for background data fetching jobs."""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.providers.base import Provider
from backend.storage import Storage

logger = logging.getLogger(__name__)

SERIES_LIST = ("day_ahead_prices", "load", "generation", "imbalance", "imbalance_prices")

_scheduler: AsyncIOScheduler | None = None
_job_status: dict[str, dict] = {}


def _get_fetch_interval_minutes(series: str) -> int:
    """Get fetch interval for each series in minutes."""
    intervals = {
        "day_ahead_prices": 5,
        "load": 5,
        "generation": 5,
        "imbalance": 5,
        "imbalance_prices": 5,
    }
    return intervals.get(series, 30)


def _get_lookback_hours(series: str, default_hours: int) -> int:
    """Get lookback window for each series in hours."""
    lookbacks = {
        "day_ahead_prices": 48,
    }
    return lookbacks.get(series, default_hours)


def _get_lookahead_hours(series: str) -> int:
    """Get forward window for each series in hours.

    Day-ahead prices are published for tomorrow, around midday. With an end of
    "now" the request window would exclude those future prices entirely —
    leaving the day-ahead chart showing only history, which is the one thing
    it is not for.
    """
    return 24 if series == "day_ahead_prices" else 0


def _blank_status() -> dict:
    return {
        "last_fetch_attempt_utc": None,
        "last_fetch_success_utc": None,
        "last_error": None,
        "provider_used": None,
    }


async def _fetch_job(
    series: str,
    country: str,
    provider: Provider,
    storage: Storage,
    history_window_hours: int,
) -> None:
    """Scheduled job that fetches data for a (series, country) pair."""
    job_key = f"{series}:{country}"
    status = _job_status.setdefault(job_key, _blank_status())

    now = datetime.now(timezone.utc)
    status["last_fetch_attempt_utc"] = now

    start = now - timedelta(hours=_get_lookback_hours(series, history_window_hours))
    end = now + timedelta(hours=_get_lookahead_hours(series))

    try:
        logger.debug(f"Fetching {series} for {country} from {provider.name}")
        result = await provider.fetch(series, country, start, end)
        await storage.store(result)
        status["last_fetch_success_utc"] = datetime.now(timezone.utc)
        status["last_error"] = None
        status["provider_used"] = provider.name
        logger.info(f"Successfully fetched {series} for {country} from {provider.name}")
    except Exception as e:
        status["last_error"] = str(e)
        logger.error(f"Failed to fetch {series} for {country}: {e}")


async def init_scheduler(
    storage: Storage,
    provider: Provider,
    countries_config: dict,
    history_window_hours: int,
) -> AsyncIOScheduler:
    """Initialize and return the APScheduler scheduler."""
    global _scheduler

    _scheduler = AsyncIOScheduler()

    enabled_countries = [
        c["code"]
        for c in countries_config.get("countries", [])
        if c.get("enabled", False)
    ]

    job_index = 0
    for series in SERIES_LIST:
        for country in enabled_countries:
            interval_minutes = _get_fetch_interval_minutes(series)
            job_key = f"{series}:{country}"

            # Registered up front so /api/health lists every job from the first
            # request, instead of looking like nothing was ever scheduled.
            _job_status[job_key] = _blank_status()

            _scheduler.add_job(
                _fetch_job,
                "interval",
                minutes=interval_minutes,
                args=[series, country, provider, storage, history_window_hours],
                id=job_key,
                name=f"Fetch {series} for {country}",
                max_instances=1,
                misfire_grace_time=60,
                # Fire at startup rather than after one full interval — but
                # staggered: every series×country job firing in the same
                # instant makes ENTSO-E respond slowly enough to trip the 30 s
                # read timeout on some of them.
                next_run_time=datetime.now() + timedelta(seconds=2 * job_index),
            )
            job_index += 1

            logger.info(f"Scheduled fetch job for {series}:{country} every {interval_minutes}m (first run now)")

    _scheduler.start()
    logger.info("Scheduler started")
    return _scheduler


async def shutdown_scheduler() -> None:
    """Shutdown the scheduler."""
    global _scheduler
    if _scheduler:
        # AsyncIOExecutor ignores wait= entirely: it cancels pending futures and
        # returns. Passing wait=True would only imply a guarantee we don't get.
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown")


def get_job_status() -> dict[str, dict]:
    """Get status of all scheduled jobs."""
    return {job_key: dict(status) for job_key, status in _job_status.items()}
