"""ENTSO-E Transparency Platform provider."""

import asyncio
import logging
from datetime import datetime

from backend.models import NormalizedSeries
from backend.providers.entsoe.client import ENTSOEClient
from backend.providers.entsoe.normalizers import (
    normalize_generation,
    normalize_scalar_series,
)

logger = logging.getLogger(__name__)


class ENTSOEProvider:
    """Fetches data from ENTSO-E Transparency Platform API."""

    name = "entsoe"

    def __init__(self, api_key: str, countries_config: dict) -> None:
        self.client = ENTSOEClient(api_key)
        self._countries = {
            c["code"]: c["bidding_zone"]
            for c in countries_config.get("countries", [])
            if c.get("enabled", False)
        }

    async def fetch(
        self,
        series: str,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """Fetch data from ENTSO-E and normalize to canonical format."""
        bidding_zone = self._countries.get(country)
        if not bidding_zone:
            raise ValueError(f"Country {country} not supported")

        # entsoe-py is synchronous (requests under the hood). These jobs run on
        # the event loop, so calling it directly would block every API request
        # and every other job for the whole round trip.
        if series == "day_ahead_prices":
            raw = await asyncio.to_thread(
                self.client.query_day_ahead_prices, bidding_zone, start, end
            )
        elif series == "load":
            raw = await asyncio.to_thread(
                self.client.query_load, country, start, end
            )
        elif series == "generation":
            raw = await asyncio.to_thread(
                self.client.query_generation, country, start, end
            )
        elif series == "imbalance":
            raw = await asyncio.to_thread(
                self.client.query_imbalance_volumes, bidding_zone, start, end
            )
        else:
            raise ValueError(f"Unknown series: {series}")

        if series == "generation":
            return normalize_generation(raw, country)
        return normalize_scalar_series(raw, country, series)
