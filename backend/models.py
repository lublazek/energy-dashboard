from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class Point(BaseModel):
    t: datetime
    v: Optional[float] = None
    by_source: Optional[dict[str, float]] = None


class NormalizedSeries(BaseModel):
    country: str
    series: str
    unit: str
    resolution_minutes: int
    points: list[Point]
    latest: Optional[Point] = None
    stale: bool = False
    age_seconds: int = 0
    fetched_at: Optional[datetime] = None
