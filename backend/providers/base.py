from datetime import datetime
from typing import Protocol

from backend.models import NormalizedSeries


class Provider(Protocol):
    """Interface for data providers (ENTSO-E, ČEPS, SEPS, etc.)."""

    name: str

    async def supports(self, series: str, country: str) -> bool:
        """Check if this provider can fetch the given series for the given country."""
        ...

    async def fetch(
        self,
        series: str,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """
        Fetch data for a series and country within a time range.
        Returns data in the canonical NormalizedSeries format.
        """
        ...
