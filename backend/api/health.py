"""Health and status endpoints."""

from fastapi import APIRouter

from backend.scheduler import get_job_status

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict:
    """Get health status of all scheduled jobs."""
    status = get_job_status()

    jobs = {}
    for job_key, info in status.items():
        jobs[job_key] = {
            "last_fetch_attempt_utc": info.get("last_fetch_attempt_utc"),
            "last_fetch_success_utc": info.get("last_fetch_success_utc"),
            "last_error": info.get("last_error"),
            "provider_used": info.get("provider_used"),
        }

    return {
        "status": "ok",
        "jobs": jobs,
    }
