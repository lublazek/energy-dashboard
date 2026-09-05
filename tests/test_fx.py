"""Tests for EUR conversion of the price series.

No network: the ECB feed is always a fixture here, and the failure cases are
produced by making the fake request raise. The subject under test is the
promise `backend/fx.py` makes — a value labelled EUR/MWh really is euros, and
a value that could not be converted is never labelled EUR.
"""

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
import requests

from backend import fx as fx_module
from backend.fx import FXConverter, _parse_ecb_xml

# --- fixtures -----------------------------------------------------------------

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-08-28">
      <Cube currency="USD" rate="1.0856"/>
      <Cube currency="CZK" rate="25.000"/>
      <Cube currency="PLN" rate="4.2500"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""

PINNED = {"CZK": 20.0, "PLN": 4.0}
PINNED_DATE = date(2026, 8, 1)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


@pytest.fixture
def live(monkeypatch):
    """A converter whose ECB fetch succeeds, counting the calls it makes."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _FakeResponse(ECB_XML)

    monkeypatch.setattr(fx_module.requests, "get", fake_get)
    converter = FXConverter(fallback_rates=PINNED, fallback_date=PINNED_DATE)
    converter.calls = calls
    return converter


@pytest.fixture
def offline(monkeypatch):
    """A converter whose ECB fetch always fails."""

    def fake_get(url, timeout=None):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(fx_module.requests, "get", fake_get)
    return FXConverter(fallback_rates=PINNED, fallback_date=PINNED_DATE)


# --- feed parsing -------------------------------------------------------------


def test_parses_rates_and_publication_date():
    rates, published = _parse_ecb_xml(ECB_XML)

    assert rates["CZK"] == 25.0
    assert rates["PLN"] == 4.25
    assert published == date(2026, 8, 28)


def test_unparseable_rate_is_skipped_not_fatal():
    broken = ECB_XML.replace('rate="25.000"', 'rate="n/a"')

    rates, _ = _parse_ecb_xml(broken)

    assert "CZK" not in rates
    assert rates["PLN"] == 4.25  # the rest of the feed still lands


# --- conversion ---------------------------------------------------------------


def test_eur_series_passes_through_untouched(live):
    values = pd.Series([10.0, -20.0, 30.5])

    converted, unit = live.to_eur(values, "EUR")

    assert list(converted) == [10.0, -20.0, 30.5]
    assert unit == "EUR/MWh"


def test_czk_is_divided_by_the_live_rate(live):
    """The ECB quotes units per EUR, so EUR = amount / rate."""
    values = pd.Series([2500.0, 500.0])

    converted, unit = live.to_eur(values, "CZK")

    assert list(converted) == [100.0, 20.0]
    assert unit == "EUR/MWh"


def test_negative_prices_keep_their_sign(live):
    """Imbalance prices go negative; conversion must not fold the sign."""
    converted, _ = live.to_eur(pd.Series([-2500.0]), "CZK")

    assert list(converted) == [-100.0]


def test_lowercase_currency_code_is_accepted(live):
    converted, unit = live.to_eur(pd.Series([2500.0]), "czk")

    assert list(converted) == [100.0]
    assert unit == "EUR/MWh"


def test_empty_series_survives_conversion(live):
    converted, unit = live.to_eur(pd.Series(dtype=float), "CZK")

    assert converted.empty
    assert unit == "EUR/MWh"


# --- the fallback path --------------------------------------------------------


def test_offline_uses_pinned_rate_and_names_it_in_the_unit(offline):
    """A pinned rate is still a conversion, but the dashboard has to be able
    to tell that it was not today's."""
    converted, unit = offline.to_eur(pd.Series([2000.0]), "CZK")

    assert list(converted) == [100.0]  # pinned CZK 20.0, not the live 25.0
    assert unit == "EUR/MWh (rate 2026-08-01)"


def test_unknown_currency_is_left_alone_never_relabelled_eur(offline):
    """The bug this module exists to prevent: HUF numbers under an EUR label
    are wrong by ~400x and look exactly like a price spike."""
    values = pd.Series([40000.0])

    converted, unit = offline.to_eur(values, "HUF")

    assert list(converted) == [40000.0]  # untouched
    assert unit == "HUF/MWh"
    assert "EUR" not in unit


def test_no_declared_currency_falls_back_to_eur(live):
    """A44 always declares a currency; if one is ever missing, the series
    keeps its declared unit rather than being converted by guess."""
    converted, unit = live.to_eur(pd.Series([50.0]), None)

    assert list(converted) == [50.0]
    assert unit == "EUR/MWh"


# --- caching ------------------------------------------------------------------


def test_rates_are_fetched_once_not_per_call(live):
    for _ in range(5):
        live.to_eur(pd.Series([100.0]), "CZK")
        live.to_eur(pd.Series([100.0]), "PLN")

    assert len(live.calls) == 1


def test_failed_fetch_is_not_retried_on_every_call(monkeypatch):
    """The ECB publishes on working days only, so on a weekend the refresh is
    permanently 'due'. Unthrottled that would hit the network every fetch."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(fx_module.requests, "get", fake_get)
    converter = FXConverter(fallback_rates=PINNED, fallback_date=PINNED_DATE)

    for _ in range(10):
        converter.to_eur(pd.Series([2000.0]), "CZK")

    assert len(calls) == 1


def test_retry_happens_once_the_interval_has_passed(monkeypatch):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(fx_module.requests, "get", fake_get)
    converter = FXConverter(fallback_rates=PINNED, fallback_date=PINNED_DATE)

    converter.to_eur(pd.Series([2000.0]), "CZK")
    assert len(calls) == 1

    # Pretend the last attempt was longer ago than the throttle window.
    converter._last_attempt = datetime.now(timezone.utc) - (
        fx_module.RETRY_INTERVAL + timedelta(minutes=1)
    )
    converter.to_eur(pd.Series([2000.0]), "CZK")

    assert len(calls) == 2
