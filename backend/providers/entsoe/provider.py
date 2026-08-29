"""ENTSO-E Transparency Platform provider (raw REST API)."""

import asyncio
import logging
from datetime import datetime

from backend.models import NormalizedSeries
from backend.providers.entsoe.normalizers import (
    normalize_generation,
    normalize_scalar_series,
)
from backend.providers.entsoe.raw_client import ENTSOERawClient
from backend.providers.entsoe.xml_parsers import (
    extract_currency,
    parse_generation_documents,
    parse_scalar_documents,
)

logger = logging.getLogger(__name__)

# series -> (client method, Point value element, negate-on-A02-flow-direction).
# Generation is dispatched separately: its response is per-fuel, not scalar.
_SCALAR_SERIES = {
    "day_ahead_prices": ("fetch_day_ahead_prices_xml", "price.amount", False),
    "load": ("fetch_load_xml", "quantity", False),
    "imbalance": ("fetch_imbalance_volumes_xml", "quantity", True),
    "imbalance_prices": ("fetch_imbalance_prices_xml", "imbalance_Price.amount", False),
}


class ENTSOEProvider:
    """Fetches data from the ENTSO-E Transparency Platform REST API."""

    name = "entsoe"

    def __init__(self, api_key: str, countries_config: dict) -> None:
        self.client = ENTSOERawClient(api_key)
        self._countries = {
            c["code"]: c
            for c in countries_config.get("countries", [])
            if c.get("enabled", False)
        }
        # Fail at startup, not at the first 3 a.m. fetch: every enabled country
        # must carry the EIC area code the raw API addresses it by.
        for code, country in self._countries.items():
            if not country.get("eic"):
                raise ValueError(
                    f"Country {code} is enabled but has no 'eic' in countries.yaml"
                )

    async def fetch(
        self,
        series: str,
        country: str,
        start: datetime,
        end: datetime,
    ) -> NormalizedSeries:
        """Fetch data from ENTSO-E and normalize to canonical format."""
        config = self._countries.get(country)
        if not config:
            raise ValueError(f"Country {country} not supported")

        # Imbalance is published per control area, and for Germany the DE-LU
        # bidding zone holds no imbalance data at all — countries.yaml can
        # point the two imbalance series at a control area instead.
        if series in ("imbalance", "imbalance_prices"):
            eic = config.get("imbalance_eic") or config["eic"]
        else:
            eic = config["eic"]

        # The HTTP round trip is blocking (requests). These jobs run on the
        # event loop, so calling it directly would block every API request and
        # every other job for the whole round trip. Parsing a <48 h window of
        # documents is milliseconds — fine on the loop.
        if series == "generation":
            documents = await asyncio.to_thread(
                self.client.fetch_generation_xml, eic, start, end
            )
            return normalize_generation(parse_generation_documents(documents), country)

        if series not in _SCALAR_SERIES:
            raise ValueError(f"Unknown series: {series}")
        method_name, value_tag, flow_signed = _SCALAR_SERIES[series]

        documents = await asyncio.to_thread(
            getattr(self.client, method_name), eic, start, end
        )
        values = parse_scalar_documents(documents, value_tag, flow_signed=flow_signed)

        # Imbalance prices are settled in the national currency (CZK, PLN, …),
        # so the unit comes from the response instead of a declaration.
        unit_override = None
        if series == "imbalance_prices":
            currency = extract_currency(documents)
            if currency:
                unit_override = f"{currency}/MWh"

        return normalize_scalar_series(values, country, series, unit_override=unit_override)
