"""ENTSO-E Transparency Platform provider (raw REST API)."""

import asyncio
import logging
from datetime import datetime

from backend.fx import FXConverter
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

# series -> (client method, Point value element, negate-on-A02-flow-direction,
# is-money). Money series declare their settlement currency in the response and
# are converted to EUR; the others carry no currency at all.
# Generation is dispatched separately: its response is per-fuel, not scalar.
_SCALAR_SERIES = {
    "day_ahead_prices": ("fetch_day_ahead_prices_xml", "price.amount", False, True),
    "load": ("fetch_load_xml", "quantity", False, False),
    "imbalance": ("fetch_imbalance_volumes_xml", "quantity", True, False),
    "imbalance_prices": (
        "fetch_imbalance_prices_xml",
        "imbalance_Price.amount",
        False,
        True,
    ),
}


class ENTSOEProvider:
    """Fetches data from the ENTSO-E Transparency Platform REST API."""

    name = "entsoe"

    def __init__(
        self,
        api_key: str,
        countries_config: dict,
        fx: FXConverter | None = None,
    ) -> None:
        self.client = ENTSOERawClient(api_key)
        # Shared across every country and series so the daily ECB fixing is
        # fetched once, not once per job.
        self.fx = fx if fx is not None else FXConverter()
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
        method_name, value_tag, flow_signed, is_money = _SCALAR_SERIES[series]

        documents = await asyncio.to_thread(
            getattr(self.client, method_name), eic, start, end
        )
        values = parse_scalar_documents(documents, value_tag, flow_signed=flow_signed)

        # Money series are settled in the national currency (CZK for ČEPS, PLN
        # for PSE, …), declared only in the response. Everything is converted
        # to EUR so the countries are comparable on one axis, and the unit is
        # whatever the conversion could actually honour — see backend/fx.py.
        unit_override = None
        if is_money:
            currency = extract_currency(documents)
            # The ECB call is blocking and may hit the network on the first
            # money series of the day; keep it off the event loop.
            values, unit_override = await asyncio.to_thread(
                self.fx.to_eur, values, currency
            )

        return normalize_scalar_series(values, country, series, unit_override=unit_override)
