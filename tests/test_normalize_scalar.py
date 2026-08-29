"""Tests for `normalize_scalar_series` — the prices/load/imbalance shape.

The normalizer's input is a plain pandas Series, so no XML fixtures are
needed here; the XML → Series step has its own tests in test_xml_parsers.py.
"""

import pandas as pd
import pytest

from backend.providers.entsoe.normalizers import SCALAR_SERIES, normalize_scalar_series

NAN = float("nan")


def utc_series(values: list[float], minutes: int = 15) -> pd.Series:
    index = pd.date_range("2026-08-25", periods=len(values), freq=f"{minutes}min", tz="UTC")
    return pd.Series(values, index=index)


def test_happy_path():
    result = normalize_scalar_series(utc_series([100.0, 200.0]), "CZ", "load")

    assert result.country == "CZ"
    assert result.series == "load"
    assert result.unit == "MW"
    assert [p.v for p in result.points] == [100.0, 200.0]
    # A scalar point carries v, never by_source.
    assert result.points[0].by_source is None


@pytest.mark.parametrize(
    ("series", "unit"),
    [
        ("day_ahead_prices", "EUR/MWh"),
        ("load", "MW"),
        ("imbalance", "MW"),
        ("imbalance_prices", "EUR/MWh"),
    ],
)
def test_every_scalar_series_has_its_unit(series, unit):
    result = normalize_scalar_series(utc_series([1.0]), "CZ", series)
    assert result.unit == unit
    assert SCALAR_SERIES[series][0] == unit


def test_latest_and_fetched_at():
    result = normalize_scalar_series(utc_series([1.0, 2.0, 3.0]), "CZ", "day_ahead_prices")

    assert result.latest == result.points[-1]
    assert result.latest.v == 3.0
    assert result.fetched_at is not None
    assert result.fetched_at.tzinfo is not None


def test_nan_values_are_dropped_not_zeroed():
    result = normalize_scalar_series(utc_series([1.0, NAN, 3.0]), "CZ", "load")

    assert [p.v for p in result.points] == [1.0, 3.0]


def test_empty_input_yields_a_valid_empty_series():
    result = normalize_scalar_series(pd.Series(dtype=float), "CZ", "imbalance")

    assert result.points == []
    assert result.latest is None
    assert result.unit == "MW"
    assert result.fetched_at is not None
    assert result.fetched_at.tzinfo is not None


def test_resolution_is_measured_from_the_index():
    quarter = normalize_scalar_series(utc_series([1.0] * 3, minutes=15), "CZ", "load")
    hourly = normalize_scalar_series(utc_series([1.0] * 3, minutes=60), "CZ", "day_ahead_prices")

    assert quarter.resolution_minutes == 15
    assert hourly.resolution_minutes == 60


def test_negative_imbalance_survives():
    """Deficit volumes are negative by design (flowDirection A02); the
    normalizer must not clamp or drop them."""
    result = normalize_scalar_series(utc_series([50.0, -30.0]), "CZ", "imbalance")

    assert [p.v for p in result.points] == [50.0, -30.0]
