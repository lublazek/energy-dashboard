import threading
from datetime import datetime
from typing import Optional, Protocol

from backend.models import NormalizedSeries


class Storage(Protocol):
    """Interface for storing and retrieving normalized series data."""

    async def store(self, series: NormalizedSeries) -> None:
        """Store a normalized series."""
        ...

    async def get(self, country: str, series_name: str) -> Optional[NormalizedSeries]:
        """Retrieve the latest normalized series."""
        ...

    async def get_all(self) -> dict[tuple[str, str], NormalizedSeries]:
        """Retrieve all stored series as {(country, series): NormalizedSeries}."""
        ...


class InMemoryStore:
    """Thread-safe in-memory storage for normalized series."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], NormalizedSeries] = {}
        self._lock = threading.RLock()

    async def store(self, series: NormalizedSeries) -> None:
        """Store a normalized series in memory."""
        with self._lock:
            key = (series.country, series.series)
            self._data[key] = series

    async def get(self, country: str, series_name: str) -> Optional[NormalizedSeries]:
        """Retrieve a normalized series from memory."""
        with self._lock:
            return self._data.get((country, series_name))

    async def get_all(self) -> dict[tuple[str, str], NormalizedSeries]:
        """Retrieve all stored series."""
        with self._lock:
            return dict(self._data)
