"""Currency conversion to EUR for the price series.

ENTSO-E settles imbalance in the **national** currency and says so only in the
response's `currency_Unit.name` — CZK for ČEPS, PLN for PSE, HUF for MAVIR, and
so on. It publishes no exchange rate, so converting to a single comparable unit
means bringing a rate in from outside.

Rates come from the ECB euro foreign exchange reference rates, the same daily
fixing the market quotes against. They are published once per working day
around 16:00 CET, so this module refreshes at most once a day and holds the
result in memory.

**A missing rate must never silently pass through as EUR.** A CZK number
labelled EUR/MWh is wrong by roughly 25x and looks exactly like a price spike,
which is the failure this module exists to prevent. So when the ECB cannot be
reached the pinned rate from `config/fx_rates.yaml` is used *and named in the
unit* ("EUR/MWh (rate 2026-08-20)"), and when no rate can be found at all the
values stay in their original currency with the original unit — visibly odd
rather than invisibly wrong.
"""

import logging
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

logger = logging.getLogger(__name__)

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

REQUEST_TIMEOUT_SECONDS = 15

# The ECB publishes on working days only, so on a weekend "today's" rate never
# arrives and an unthrottled refresh would retry on every single fetch. One
# attempt per half hour is plenty for a daily fixing.
RETRY_INTERVAL = timedelta(minutes=30)


@dataclass(frozen=True)
class Rate:
    """One currency's value against the euro: `value` units per 1 EUR."""

    currency: str
    value: float
    as_of: date | None
    is_fallback: bool


def _parse_ecb_xml(xml_text: str) -> tuple[dict[str, float], date | None]:
    """Pull {currency: units-per-EUR} and the publication date out of the feed.

    The document nests three levels of <Cube>: an outer wrapper, one carrying
    the date, and one per currency. Matching on the local tag name keeps this
    independent of the ECB's namespace.
    """
    root = ET.fromstring(xml_text)
    rates: dict[str, float] = {}
    published: date | None = None

    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "Cube":
            continue
        if "time" in elem.attrib:
            try:
                published = date.fromisoformat(elem.attrib["time"])
            except ValueError:
                logger.warning(f"Unparseable ECB date {elem.attrib['time']!r}")
        currency = elem.attrib.get("currency")
        raw_rate = elem.attrib.get("rate")
        if currency and raw_rate:
            try:
                rates[currency.upper()] = float(raw_rate)
            except ValueError:
                logger.warning(f"Unparseable ECB rate for {currency}: {raw_rate!r}")

    return rates, published


class FXConverter:
    """Converts money series to EUR, with a pinned fallback and a stale marker.

    Safe to share across threads: scheduler jobs run concurrently on worker
    threads and would otherwise all miss the cache and fetch at once.
    """

    def __init__(
        self,
        fallback_rates: dict[str, float] | None = None,
        fallback_date: date | None = None,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._fallback = {k.upper(): float(v) for k, v in (fallback_rates or {}).items()}
        self._fallback_date = fallback_date
        self._timeout = timeout

        self._lock = threading.Lock()
        self._rates: dict[str, float] = {}
        self._rates_date: date | None = None
        self._last_attempt: datetime | None = None

    # --- rate lookup ----------------------------------------------------------

    def _refresh_due(self, now: datetime) -> bool:
        if self._rates_date == now.date():
            return False  # today's fixing already in hand
        if self._last_attempt is not None and now - self._last_attempt < RETRY_INTERVAL:
            return False
        return True

    def _refresh(self) -> None:
        """Fetch the daily fixing. Never raises: a failure falls back instead."""
        now = datetime.now(timezone.utc)
        if not self._refresh_due(now):
            return
        self._last_attempt = now

        try:
            response = requests.get(ECB_DAILY_URL, timeout=self._timeout)
            response.raise_for_status()
            rates, published = _parse_ecb_xml(response.text)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning(f"ECB rate refresh failed ({exc}); using pinned rates")
            return

        if not rates:
            logger.warning("ECB feed carried no rates; using pinned rates")
            return

        self._rates = rates
        self._rates_date = published
        logger.info(f"Loaded {len(rates)} ECB reference rates published {published}")

    def rate(self, currency: str) -> Rate | None:
        """Units of `currency` per 1 EUR, live if possible, pinned if not."""
        code = currency.upper()
        if code == "EUR":
            return Rate(currency="EUR", value=1.0, as_of=None, is_fallback=False)

        with self._lock:
            self._refresh()
            live = self._rates.get(code)
            live_date = self._rates_date

        if live:
            return Rate(currency=code, value=live, as_of=live_date, is_fallback=False)

        pinned = self._fallback.get(code)
        if pinned:
            logger.warning(
                f"No live ECB rate for {code}; using pinned rate from "
                f"{self._fallback_date}"
            )
            return Rate(
                currency=code,
                value=pinned,
                as_of=self._fallback_date,
                is_fallback=True,
            )

        return None

    # --- conversion -----------------------------------------------------------

    def to_eur(
        self,
        values: pd.Series,
        currency: str | None,
        quantity_unit: str = "MWh",
    ) -> tuple[pd.Series, str]:
        """Convert a money series to EUR and return it with its unit label.

        Returns the values untouched, still labelled in their own currency,
        when no rate can be found — an obviously foreign unit beats a wrong
        EUR one.
        """
        if not currency:
            # No declaration: the series is already in the declared unit its
            # normalizer will apply. Nothing to do and nothing to claim.
            return values, f"EUR/{quantity_unit}"

        code = currency.upper()
        if code == "EUR":
            return values, f"EUR/{quantity_unit}"

        found = self.rate(code)
        if found is None:
            logger.error(
                f"No ECB or pinned rate for {code}; leaving values in {code} "
                f"rather than mislabelling them as EUR"
            )
            return values, f"{code}/{quantity_unit}"

        converted = values / found.value
        if found.is_fallback:
            return converted, f"EUR/{quantity_unit} (rate {found.as_of})"
        return converted, f"EUR/{quantity_unit}"
