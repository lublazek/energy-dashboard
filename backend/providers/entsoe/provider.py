"""ENTSO-E Transparency Platform provider."""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from backend.models import NormalizedSeries
from backend.providers.entsoe.client import ENTSOEClient
from backend.providers.entsoe.generation import normalize_generation
from backend.providers.entsoe.imbalance import normalize_imbalance
from backend.providers.entsoe.load import normalize_load
from backend.providers.entsoe.prices import normalize_prices

logger = logging.getLogger(__name__)


class ENTSOEProvider:
    """Fetches data from ENTSO-E Transparency Platform API."""

    name = "entsoe"

    def __init__(self, api_key: str, countries_config_path: Path) -> None:
        self.client = ENTSOEClient(api_key)
        self._countries = self._load_countries(countries_config_path)

    def _load_countries(self, config_path: Path) -> dict[str, str]:
        """Load bidding zone mappings from countries.yaml."""
        if not config_path.exists():
            raise FileNotFoundError(f"Countries config not found: {config_path}")
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return {
            c["code"]: c["bidding_zone"]
            for c in data.get("countries", [])
            if c.get("enabled", False)
        }

    async def supports(self, series: str, country: str) -> bool:
        """Check if this provider supports the series and country."""
        if country not in self._countries:
            return False
        return series in ["day_ahead_prices", "load", "generation", "imbalance"]

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

        if series == "day_ahead_prices":
            return await self._fetch_prices(bidding_zone, country, start, end)
        elif series == "load":
            return await self._fetch_load(country, start, end)
        elif series == "generation":
            return await self._fetch_generation(country, start, end)
        elif series == "imbalance":
            return await self._fetch_imbalance(bidding_zone, country, start, end)
        else:
            raise ValueError(f"Unknown series: {series}")

    async def _fetch_prices(
        self,
        bidding_zone: str,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """Fetch day-ahead prices."""
        df = self.client.query_day_ahead_prices(bidding_zone, start, end)
        return normalize_prices(df, country)

    async def _fetch_load(
        self,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """Fetch total load."""
        df = self.client.query_load(country, start, end)
        return normalize_load(df, country)

    async def _fetch_generation(
        self,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """Fetch generation per type."""
        df = self.client.query_generation(country, start, end)
        return normalize_generation(df, country)

    async def _fetch_imbalance(
        self,
        bidding_zone: str,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """Fetch imbalance volumes."""
        df = self.client.query_imbalance_volumes(bidding_zone, start, end)
        return normalize_imbalance(df, country)
