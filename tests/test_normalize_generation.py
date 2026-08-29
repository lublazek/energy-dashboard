"""Tests for the generation pipeline: XML → parse → NormalizedSeries.

These assert on the `NormalizedSeries` contract (canonical category keys,
tz-aware `fetched_at`, `latest`, `by_source` instead of `v`) — the shape a
second provider would have to reproduce. The ENTSO-E-shaped half lives in
`entsoe_xml.py`, so a change in the upstream format means rewriting the
builders there, not these assertions.

The assertions here survived the entsoe-py → raw REST migration unchanged;
only the fixture half was swapped. That is the split working as intended.
"""

from datetime import timedelta

from backend.providers.entsoe.normalizers import normalize_generation
from backend.providers.entsoe.psr_types import CANONICAL_SOURCES
from backend.providers.entsoe.xml_parsers import parse_generation_xml
from tests.entsoe_xml import START, ack_no_data, gen_timeseries, gl_document


def from_xml(*timeseries: str):
    """Run the real pipeline: fixture XML → parser → normalizer."""
    return normalize_generation(parse_generation_xml(gl_document(*timeseries)), "CZ")


# --- 1. happy path ----------------------------------------------------------


def test_psr_codes_map_to_canonical_categories():
    result = from_xml(
        gen_timeseries("B14", [2000.0, 2100.0]),  # Nuclear
        gen_timeseries("B16", [500.0, 600.0]),    # Solar
    )

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
    result = from_xml(gen_timeseries("B14", [2000.0, 2100.0]))

    assert result.latest == result.points[-1]
    assert result.latest.by_source["nuclear"] == 2100.0
    # The stale/age_seconds calculation in routes.py subtracts this from an
    # aware utcnow; a naive value here is the bug that made everything stale.
    assert result.fetched_at is not None
    assert result.fetched_at.tzinfo is not None


# --- 2. consumption is not generation ---------------------------------------


def test_consumption_is_not_generation():
    """Pumped storage drawing power is load. Counting it inflates hydro."""
    result = from_xml(
        gen_timeseries("B14", [2000.0]),
        gen_timeseries("B10", [100.0]),                     # Hydro Pumped Storage, generating
        gen_timeseries("B10", [400.0], consumption=True),   # …and drawing power
    )

    assert result.points[0].by_source["hydro"] == 100.0  # not 500.0, not -300.0


# --- 3. many production types, one category ---------------------------------


def test_offshore_and_onshore_wind_are_summed():
    result = from_xml(
        gen_timeseries("B18", [200.0]),  # Wind Offshore
        gen_timeseries("B19", [300.0]),  # Wind Onshore
    )

    assert result.points[0].by_source["wind"] == 500.0


# --- 4. unknown fuels ---------------------------------------------------------


def test_unknown_psr_code_falls_through_to_other():
    """A new ENTSO-E fuel must not crash the fetch, and must not vanish."""
    result = from_xml(
        gen_timeseries("B14", [2000.0]),
        gen_timeseries("B23", [50.0]),  # not in PSR_CODE_MAP
    )

    assert result.points[0].by_source["other"] == 50.0
    assert result.points[0].by_source["nuclear"] == 2000.0


# --- 5. ragged tail ----------------------------------------------------------


def test_trailing_incomplete_row_is_dropped():
    """Prefer complete-but-older over recent-but-partial.

    Fuels are published by different parties, so the newest rows are often
    partly filled. Emitting them would show gas at 0.0 — indistinguishable from
    a plant genuinely off, and it silently understates `latest`.
    """
    result = from_xml(
        gen_timeseries("B14", [2000.0, 2000.0, 2000.0]),
        gen_timeseries("B16", [500.0, 500.0, 500.0]),
        gen_timeseries("B04", [300.0, 300.0]),  # gas ends one interval early
    )

    assert len(result.points) == 2
    assert result.latest.t == START + timedelta(minutes=15)
    assert result.latest.by_source["gas"] == 300.0


def test_mostly_absent_fuel_does_not_trigger_trimming():
    """`expected` is the mode of fuels-reporting, so a fuel present for only a
    sliver of the window must not eat the whole dataset."""
    result = from_xml(
        gen_timeseries("B14", [2000.0, 2000.0, 2000.0]),
        gen_timeseries("B13", [5.0]),  # Marine, reports the first interval only
    )

    assert len(result.points) == 3


# --- 6. empty input ----------------------------------------------------------


def test_no_matching_data_yields_a_valid_empty_series():
    """An empty fetch is normal (nothing published yet), not an error."""
    result = normalize_generation(parse_generation_xml(ack_no_data()), "CZ")

    assert result.points == []
    assert result.latest is None
    assert result.unit == "MW"
    assert result.resolution_minutes == 15
    assert result.fetched_at is not None
    assert result.fetched_at.tzinfo is not None


# --- 7. resolution ------------------------------------------------------------


def test_resolution_is_measured_from_the_index():
    """Declared resolutions drift (SDAC moved day-ahead to 15 min), so the
    measured spacing is the source of truth. The parser fills omitted
    positions, so within a Period the spacing is dense and honest."""
    quarter_hourly = from_xml(gen_timeseries("B14", [2000.0] * 3, resolution="PT15M"))
    hourly = from_xml(gen_timeseries("B14", [2000.0] * 3, resolution="PT60M"))

    assert quarter_hourly.resolution_minutes == 15
    assert hourly.resolution_minutes == 60
