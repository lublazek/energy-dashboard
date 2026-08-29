"""Tests for the raw ENTSO-E XML parsers.

These pin down the XML → pandas layer: timestamp reconstruction, the
omitted-position fill, consumption exclusion, and the HTTP-200 acknowledgement
trap. The NormalizedSeries contract is asserted separately in
test_normalize_generation.py / test_normalize_scalar.py.
"""

from datetime import timedelta

import pandas as pd
import pytest

from backend.providers.entsoe.xml_parsers import (
    extract_currency,
    parse_generation_xml,
    parse_scalar_documents,
    parse_scalar_xml,
)
from tests.entsoe_xml import (
    START,
    ack_no_data,
    balancing_document,
    gen_timeseries,
    gl_document,
    imbalance_price_timeseries,
    imbalance_volume_timeseries,
    load_timeseries,
    price_timeseries,
    publication_document,
)

# --- timestamp reconstruction -------------------------------------------------


def test_timestamps_come_from_start_plus_resolution_times_position():
    xml = gl_document(load_timeseries([100.0, 200.0, 300.0], resolution="PT15M"))

    series = parse_scalar_xml(xml, "quantity")

    assert list(series.index) == [
        START,
        START + timedelta(minutes=15),
        START + timedelta(minutes=30),
    ]
    assert list(series) == [100.0, 200.0, 300.0]
    assert str(series.index.tz) == "UTC"


def test_hourly_resolution_spaces_timestamps_by_60_minutes():
    xml = publication_document(price_timeseries([50.0, 60.0], resolution="PT60M"))

    series = parse_scalar_xml(xml, "price.amount")

    assert series.index[1] - series.index[0] == timedelta(minutes=60)
    assert list(series) == [50.0, 60.0]


def test_unknown_resolution_raises():
    xml = gl_document(load_timeseries([100.0]).replace("PT15M", "PT7M"))

    with pytest.raises(ValueError, match="resolution"):
        parse_scalar_xml(xml, "quantity")


# --- omitted positions --------------------------------------------------------


def test_omitted_position_repeats_the_previous_value():
    """Positions with no Point mean "value unchanged", not "value missing"."""
    xml = gl_document(load_timeseries([100.0, None, None, 400.0]))

    series = parse_scalar_xml(xml, "quantity")

    assert list(series) == [100.0, 100.0, 100.0, 400.0]
    assert len(series) == 4


def test_trailing_omission_fills_to_the_period_end():
    xml = gl_document(load_timeseries([100.0, 200.0, None]))

    series = parse_scalar_xml(xml, "quantity")

    assert list(series) == [100.0, 200.0, 200.0]


def test_leading_omission_has_nothing_to_repeat_and_stays_missing():
    xml = gl_document(load_timeseries([None, 200.0, 300.0]))

    series = parse_scalar_xml(xml, "quantity")

    # The first slot is simply absent from the result, not zero.
    assert len(series) == 2
    assert series.index[0] == START + timedelta(minutes=15)


# --- acknowledgement and malformed input --------------------------------------


def test_no_matching_data_acknowledgement_is_empty_not_an_error():
    assert parse_scalar_xml(ack_no_data(), "quantity").empty
    assert parse_generation_xml(ack_no_data()).empty


def test_malformed_xml_raises():
    """Garbage is not the same as confirmed absence — it must raise, so the
    scheduler records the error and the store keeps the last good data."""
    with pytest.raises(Exception):
        parse_scalar_xml("this is not XML", "quantity")


def test_unexpected_document_root_raises():
    with pytest.raises(ValueError, match="Unexpected"):
        parse_generation_xml(publication_document(price_timeseries([50.0])))


# --- generation specifics -----------------------------------------------------


def test_generation_columns_are_psr_codes():
    xml = gl_document(
        gen_timeseries("B14", [2000.0, 2100.0]),
        gen_timeseries("B16", [500.0, 600.0]),
    )

    frame = parse_generation_xml(xml)

    assert sorted(frame.columns) == ["B14", "B16"]
    assert frame["B14"].iloc[0] == 2000.0
    assert frame["B16"].iloc[1] == 600.0


def test_consumption_timeseries_is_excluded():
    """outBiddingZone = pumped storage drawing power = load, not generation."""
    xml = gl_document(
        gen_timeseries("B10", [100.0]),
        gen_timeseries("B10", [400.0], consumption=True),
    )

    frame = parse_generation_xml(xml)

    assert frame["B10"].iloc[0] == 100.0  # not 500.0, not -300.0


def test_fuel_ending_early_leaves_nan_in_the_tail():
    """Fuels are published by different parties; a shorter TimeSeries leaves
    NaN in the union index — the ragged tail the normalizer trims."""
    xml = gl_document(
        gen_timeseries("B14", [2000.0, 2000.0, 2000.0]),
        gen_timeseries("B04", [300.0, 300.0]),
    )

    frame = parse_generation_xml(xml)

    assert len(frame) == 3
    assert frame["B04"].iloc[1] == 300.0
    assert pd.isna(frame["B04"].iloc[2])


def test_two_timeseries_for_one_fuel_concatenate():
    """A fuel's data can arrive split over several TimeSeries with disjoint
    periods; they merge into one column."""
    xml = gl_document(
        gen_timeseries("B14", [1000.0, 1000.0]),
        gen_timeseries("B14", [2000.0, 2000.0], start=START + timedelta(minutes=30)),
    )

    frame = parse_generation_xml(xml)

    assert len(frame) == 4
    assert list(frame["B14"]) == [1000.0, 1000.0, 2000.0, 2000.0]


# --- imbalance specifics ------------------------------------------------------


def test_deficit_direction_negates_imbalance_volumes():
    """A86 publishes volumes unsigned; flowDirection A02 (deficit) carries the
    minus sign."""
    xml = balancing_document(
        imbalance_volume_timeseries([50.0], direction="A01"),
        imbalance_volume_timeseries(
            [30.0], direction="A02", start=START + timedelta(minutes=15)
        ),
    )

    series = parse_scalar_xml(xml, "quantity", flow_signed=True)

    assert list(series) == [50.0, -30.0]


def test_flow_direction_is_ignored_unless_asked_for():
    xml = balancing_document(imbalance_volume_timeseries([30.0], direction="A02"))

    series = parse_scalar_xml(xml, "quantity")

    assert list(series) == [30.0]


def test_imbalance_price_value_tag():
    xml = balancing_document(imbalance_price_timeseries([85.5, 90.0]))

    series = parse_scalar_xml(xml, "imbalance_Price.amount")

    assert list(series) == [85.5, 90.0]


def test_currency_comes_from_the_document():
    """Imbalance is settled in the national currency — CZ publishes CZK, not
    EUR. The unit must be read, never declared."""
    czk = balancing_document(imbalance_price_timeseries([5000.0], currency="CZK"))

    assert extract_currency([czk]) == "CZK"
    assert extract_currency([ack_no_data()]) is None


# --- multi-document responses -------------------------------------------------
#
# The platform zips the response when it spans several documents (balancing
# data does this routinely); the raw client hands the parsers a list.


def test_documents_merge_into_one_series():
    docs = [
        balancing_document(imbalance_volume_timeseries([10.0, 20.0])),
        balancing_document(
            imbalance_volume_timeseries([30.0], start=START + timedelta(minutes=30))
        ),
    ]

    series = parse_scalar_documents(docs, "quantity")

    assert list(series) == [10.0, 20.0, 30.0]


def test_overlapping_documents_prefer_the_later_one():
    """A later document is the more recent publication of the same instants."""
    docs = [
        balancing_document(imbalance_volume_timeseries([10.0, 20.0])),
        balancing_document(imbalance_volume_timeseries([11.0, 21.0])),
    ]

    series = parse_scalar_documents(docs, "quantity")

    assert list(series) == [11.0, 21.0]


def test_ack_document_among_real_ones_is_ignored():
    docs = [ack_no_data(), balancing_document(imbalance_volume_timeseries([10.0]))]

    series = parse_scalar_documents(docs, "quantity")

    assert list(series) == [10.0]


# --- mixed resolutions --------------------------------------------------------


def test_mixed_resolutions_keep_the_finest():
    """During the SDAC transition a price document can carry both hourly and
    quarter-hourly curves; the finest is the current market time unit."""
    xml = publication_document(
        price_timeseries([50.0, 60.0], resolution="PT60M"),
        price_timeseries([1.0, 2.0, 3.0, 4.0], resolution="PT15M"),
    )

    series = parse_scalar_xml(xml, "price.amount")

    assert list(series) == [1.0, 2.0, 3.0, 4.0]
    assert series.index[1] - series.index[0] == timedelta(minutes=15)
