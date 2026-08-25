"""Tests for `normalize_generation`.

These assert on the `NormalizedSeries` contract (canonical category keys,
tz-aware `fetched_at`, `latest`, `by_source` instead of `v`) rather than on
anything entsoe-py-shaped. The entsoe-py-shaped part lives in
`entsoe_frames.py`, so a move to the raw REST API means rewriting the builders
there, not these assertions.

The one exception is `test_actual_consumption_is_not_generation`, which is
about a column label entsoe-py invents. Under the raw API the same rule is
enforced by `inBiddingZone` vs `outBiddingZone` (docs/entsoe.md §7) — the rule
survives, the fixture does not.
"""

import pandas as pd

from backend.providers.entsoe.normalizers import (
    _generation_columns,
    normalize_generation,
)
from backend.providers.entsoe.psr_types import CANONICAL_SOURCES
from tests.entsoe_frames import flat_frame, index_at, multi_frame

NAN = float("nan")


# --- 1. happy path ----------------------------------------------------------


def test_flat_columns_map_to_canonical_categories():
    df = flat_frame({"Nuclear": [2000.0, 2100.0], "Solar": [500.0, 600.0]})

    result = normalize_generation(df, "CZ")

    assert result.country == "CZ"
    assert result.series == "generation"
    assert result.unit == "MW"
    assert len(result.points) == 2

    first = result.points[0]
    assert first.by_source["nuclear"] == 2000.0
    assert first.by_source["solar"] == 500.0
    # Every canonical category is always present, even at zero, so the frontend
    # never has to guard for a missing key.
    assert set(first.by_source) == set(CANONICAL_SOURCES)
    assert first.by_source["wind"] == 0.0
    # A generation point carries by_source, never v.
    assert first.v is None


def test_latest_is_the_last_point_and_fetched_at_is_utc_aware():
    df = flat_frame({"Nuclear": [2000.0, 2100.0]})

    result = normalize_generation(df, "CZ")

    assert result.latest == result.points[-1]
    assert result.latest.by_source["nuclear"] == 2100.0
    # The stale/age_seconds calculation in routes.py subtracts this from an
    # aware utcnow; a naive value here is the bug that made everything stale.
    assert result.fetched_at is not None
    assert result.fetched_at.tzinfo is not None


# --- 2. consumption is not generation ---------------------------------------


def test_actual_consumption_is_not_generation():
    """Pumped storage drawing power is load. Counting it inflates hydro."""
    df = multi_frame(
        {
            ("Nuclear", "Actual Aggregated"): [2000.0],
            ("Hydro Pumped Storage", "Actual Aggregated"): [100.0],
            ("Hydro Pumped Storage", "Actual Consumption"): [400.0],
        }
    )

    result = normalize_generation(df, "CZ")

    assert result.points[0].by_source["hydro"] == 100.0  # not 500.0, not -300.0


# --- 3. many production types, one category ---------------------------------


def test_onshore_and_offshore_wind_are_summed():
    df = flat_frame({"Wind Onshore": [300.0], "Wind Offshore": [200.0]})

    result = normalize_generation(df, "CZ")

    assert result.points[0].by_source["wind"] == 500.0


# --- 4. unknown fuels ---------------------------------------------------------


def test_unknown_production_type_falls_through_to_other():
    """A new ENTSO-E fuel must not crash the fetch, and must not vanish."""
    df = flat_frame({"Nuclear": [2000.0], "Antimatter": [50.0]})

    result = normalize_generation(df, "CZ")

    assert result.points[0].by_source["other"] == 50.0
    assert result.points[0].by_source["nuclear"] == 2000.0


# --- 5. ragged tail ----------------------------------------------------------


def test_trailing_incomplete_row_is_dropped():
    """Prefer complete-but-older over recent-but-partial.

    Fuels are published by different parties, so the newest rows are often
    partly filled. Emitting them would show gas at 0.0 — indistinguishable from
    a plant genuinely off, and it silently understates `latest`.
    """
    df = flat_frame(
        {
            "Nuclear": [2000.0, 2000.0, 2000.0],
            "Solar": [500.0, 500.0, 500.0],
            "Fossil Gas": [300.0, 300.0, NAN],
        }
    )

    result = normalize_generation(df, "CZ")

    assert len(result.points) == 2
    assert result.latest.t == index_at(3)[1].to_pydatetime()
    assert result.latest.by_source["gas"] == 300.0


def test_permanently_absent_fuel_does_not_trigger_trimming():
    """`expected` is the mode of fuels-reporting, so a fuel that never reports
    is simply never expected — it must not eat the whole dataset."""
    df = flat_frame(
        {
            "Nuclear": [2000.0, 2000.0, 2000.0],
            "Marine": [NAN, NAN, NAN],
        }
    )

    result = normalize_generation(df, "CZ")

    assert len(result.points) == 3


# --- 6. NaN must survive the column flattening -------------------------------


def test_missing_fuel_stays_nan_rather_than_zero():
    """This is what `min_count=1` in `_generation_columns` protects.

    Asserted on the helper, not on `normalize_generation`: by the time a point
    is built, `normalize_generation_sources` has seeded every category to 0.0,
    so NaN and 0.0 are indistinguishable in `by_source`. The difference is only
    visible here — and it is what lets `_trim_ragged_tail` count correctly.
    """
    df = flat_frame({"Nuclear": [2000.0, 2000.0], "Fossil Gas": [300.0, NAN]})

    gen = _generation_columns(df)

    assert pd.isna(gen["Fossil Gas"].iloc[1])
    assert gen["Fossil Gas"].iloc[0] == 300.0


# --- 7. empty input ----------------------------------------------------------


def test_empty_frame_yields_a_valid_empty_series():
    """An empty fetch is normal (nothing published yet), not an error."""
    result = normalize_generation(pd.DataFrame(), "CZ")

    assert result.points == []
    assert result.latest is None
    assert result.unit == "MW"
    assert result.resolution_minutes == 15
    assert result.fetched_at is not None
    assert result.fetched_at.tzinfo is not None


# --- 8. resolution ------------------------------------------------------------


def test_resolution_is_measured_from_the_index():
    """Declared resolutions drift (SDAC moved day-ahead to 15 min), so the
    index is the source of truth while entsoe-py is in the way.

    NOTE: this becomes wrong under the raw REST API, which declares
    <resolution>PT15M</resolution> and omits empty positions — measuring the
    spacing would then read a gap as a longer resolution. See docs/entsoe.md.
    """
    quarter_hourly = flat_frame({"Nuclear": [2000.0, 2000.0, 2000.0]}, minutes=15)
    hourly = flat_frame({"Nuclear": [2000.0, 2000.0, 2000.0]}, minutes=60)

    assert normalize_generation(quarter_hourly, "CZ").resolution_minutes == 15
    assert normalize_generation(hourly, "CZ").resolution_minutes == 60
