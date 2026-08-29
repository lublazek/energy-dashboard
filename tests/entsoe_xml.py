"""Builders for fake ENTSO-E XML documents.

Everything in this file is shaped like the raw REST API's responses — the
document families described in docs/entsoe.md §7. The tests in
`test_xml_parsers.py` and `test_normalize_generation.py` assemble inputs
here and assert on parser output / the NormalizedSeries contract, so a change
in what the API sends means rewriting these builders and leaving the
assertions alone.

Conventions:
- `values` lists define the Period: its length sets the timeInterval, and a
  `None` entry is an **omitted position** (the spec's "value repeats" case).
  To make a fuel's TimeSeries end earlier than others (the ragged tail), pass
  a shorter list — a trailing `None` would be filled, not left missing.
- Timestamps are minute-precision UTC, like the platform writes them.
"""

from datetime import datetime, timedelta, timezone

# Arbitrary but fixed — tests must not depend on the real clock.
START = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)

# Real namespaces per family. The parsers match on local names, so these only
# make the fixtures honest, but that honesty is the point of fixture files.
NS_GL = "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0"
NS_PUBLICATION = "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0"
NS_BALANCING = "urn:iec62325.351:tc57wg16:451-6:balancingdocument:4:1"
NS_ACK = "urn:iec62325.351:tc57wg16:451-1:acknowledgementdocument:8:1"

_RESOLUTION_MINUTES = {"PT60M": 60, "PT30M": 30, "PT15M": 15}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def _period(values: list[float | None], resolution: str, start: datetime) -> str:
    """One <Period>: interval length = len(values); None entries are omitted."""
    minutes = _RESOLUTION_MINUTES[resolution]
    end = start + timedelta(minutes=minutes * len(values))
    points = "".join(
        f"<Point><position>{i}</position><quantity>{v}</quantity></Point>"
        for i, v in enumerate(values, start=1)
        if v is not None
    )
    return (
        "<Period>"
        f"<timeInterval><start>{_fmt(start)}</start><end>{_fmt(end)}</end></timeInterval>"
        f"<resolution>{resolution}</resolution>"
        f"{points}"
        "</Period>"
    )


def gl_document(*timeseries: str) -> str:
    return (
        f'<GL_MarketDocument xmlns="{NS_GL}">'
        + "".join(timeseries)
        + "</GL_MarketDocument>"
    )


def gen_timeseries(
    psr_type: str,
    values: list[float | None],
    resolution: str = "PT15M",
    start: datetime = START,
    consumption: bool = False,
) -> str:
    """An A75 generation TimeSeries for one fuel.

    `consumption=True` marks it with outBiddingZone_Domain — pumped storage
    drawing power, which the parser must exclude.
    """
    domain = (
        '<outBiddingZone_Domain.mRID codingScheme="A01">10YCZ-CEPS-----N</outBiddingZone_Domain.mRID>'
        if consumption
        else '<inBiddingZone_Domain.mRID codingScheme="A01">10YCZ-CEPS-----N</inBiddingZone_Domain.mRID>'
    )
    return (
        "<TimeSeries>"
        f"{domain}"
        f"<MktPSRType><psrType>{psr_type}</psrType></MktPSRType>"
        f"{_period(values, resolution, start)}"
        "</TimeSeries>"
    )


def load_timeseries(
    values: list[float | None],
    resolution: str = "PT15M",
    start: datetime = START,
) -> str:
    """An A65 load TimeSeries (load is always outBiddingZone)."""
    return (
        "<TimeSeries>"
        '<outBiddingZone_Domain.mRID codingScheme="A01">10YCZ-CEPS-----N</outBiddingZone_Domain.mRID>'
        f"{_period(values, resolution, start)}"
        "</TimeSeries>"
    )


def publication_document(*timeseries: str) -> str:
    return (
        f'<Publication_MarketDocument xmlns="{NS_PUBLICATION}">'
        + "".join(timeseries)
        + "</Publication_MarketDocument>"
    )


def price_timeseries(
    values: list[float | None],
    resolution: str = "PT60M",
    start: datetime = START,
) -> str:
    """An A44 day-ahead price TimeSeries. Value element is price.amount."""
    minutes = _RESOLUTION_MINUTES[resolution]
    end = start + timedelta(minutes=minutes * len(values))
    points = "".join(
        f"<Point><position>{i}</position><price.amount>{v}</price.amount></Point>"
        for i, v in enumerate(values, start=1)
        if v is not None
    )
    return (
        "<TimeSeries>"
        "<currency_Unit.name>EUR</currency_Unit.name>"
        "<Period>"
        f"<timeInterval><start>{_fmt(start)}</start><end>{_fmt(end)}</end></timeInterval>"
        f"<resolution>{resolution}</resolution>"
        f"{points}"
        "</Period>"
        "</TimeSeries>"
    )


def balancing_document(*timeseries: str) -> str:
    return (
        f'<Balancing_MarketDocument xmlns="{NS_BALANCING}">'
        + "".join(timeseries)
        + "</Balancing_MarketDocument>"
    )


def imbalance_volume_timeseries(
    values: list[float | None],
    direction: str | None = None,
    resolution: str = "PT15M",
    start: datetime = START,
) -> str:
    """An A86 imbalance-volume TimeSeries.

    Volumes are published unsigned; `direction` carries the sign
    (A01 = surplus, A02 = deficit → negated by the parser).
    """
    flow = (
        f"<flowDirection.direction>{direction}</flowDirection.direction>"
        if direction
        else ""
    )
    return f"<TimeSeries>{flow}{_period(values, resolution, start)}</TimeSeries>"


def imbalance_price_timeseries(
    values: list[float | None],
    resolution: str = "PT15M",
    start: datetime = START,
    currency: str = "EUR",
) -> str:
    """An A85 imbalance-price TimeSeries. Value element is imbalance_Price.amount.

    Imbalance is settled in the national currency, carried in
    currency_Unit.name (CZK for ČEPS, PLN for PSE, EUR elsewhere).
    """
    minutes = _RESOLUTION_MINUTES[resolution]
    end = start + timedelta(minutes=minutes * len(values))
    points = "".join(
        f"<Point><position>{i}</position><imbalance_Price.amount>{v}</imbalance_Price.amount></Point>"
        for i, v in enumerate(values, start=1)
        if v is not None
    )
    return (
        "<TimeSeries>"
        f"<currency_Unit.name>{currency}</currency_Unit.name>"
        "<Period>"
        f"<timeInterval><start>{_fmt(start)}</start><end>{_fmt(end)}</end></timeInterval>"
        f"<resolution>{resolution}</resolution>"
        f"{points}"
        "</Period>"
        "</TimeSeries>"
    )


def ack_no_data() -> str:
    """The HTTP-200 "no matching data" acknowledgement (docs/entsoe.md §6)."""
    return (
        f'<Acknowledgement_MarketDocument xmlns="{NS_ACK}">'
        "<Reason><code>999</code>"
        "<text>No matching data found for Data item Actual Generation per Type</text>"
        "</Reason>"
        "</Acknowledgement_MarketDocument>"
    )
