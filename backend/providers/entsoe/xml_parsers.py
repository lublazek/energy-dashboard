"""Parsers turning raw ENTSO-E XML documents into pandas objects.

All XML knowledge lives in this module and nowhere else. The functions are
pure (text in, Series/DataFrame out), so they are testable offline against
fixture documents.

Every ENTSO-E response family shares one skeleton:

    *_MarketDocument → TimeSeries → Period → Point{position, <value>}

only the root tag and the value element differ:

- `Publication_MarketDocument` (A44 day-ahead prices) — value `price.amount`
- `GL_MarketDocument` (A65 load, A75 generation) — value `quantity`
- `Balancing_MarketDocument` (A86 imbalance volumes, A85 imbalance prices) —
  value `quantity` / `imbalance_Price.amount`

A `position` is a 1-based index into the Period, not a timestamp:
timestamp = Period start + resolution × (position − 1). **Omitted positions
mean the previous value repeats** (docs/entsoe.md §7) — they are data, not
gaps, so this module fills them in. NaN therefore survives only where a
TimeSeries genuinely ends earlier than others, which is exactly what
`_trim_ragged_tail` in the normalizers needs to see.

"No matching data found" arrives as HTTP 200 with an
`Acknowledgement_MarketDocument` body — that is a confirmed absence of data
and parses to an empty result. Malformed XML or an unexpected root raises:
garbage is not the same as absence, and raising keeps the last good data in
the store while the error surfaces in /api/health.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

# The only resolutions the four series publish today. An unknown one raises —
# silently guessing a resolution would corrupt every reconstructed timestamp.
RESOLUTION_MINUTES = {"PT60M": 60, "PT30M": 30, "PT15M": 15}


# --- namespace-free element access -------------------------------------------
#
# ElementTree qualifies every tag with its namespace: the root of a generation
# document is "{urn:iec62325...:generationloaddocument:3:0}GL_MarketDocument".
# The namespace embeds a schema version that ENTSO-E bumps over time, so
# hardcoding it would break on the next revision. Matching on the local name
# (the part after "}") sidesteps that entirely.

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if _local(child.tag) == name]


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _text(elem: ET.Element, *path: str) -> str | None:
    """Descend a path of local names and return the final element's text."""
    current: ET.Element | None = elem
    for name in path:
        if current is None:
            return None
        current = _child(current, name)
    return current.text if current is not None else None


# --- shared document plumbing -------------------------------------------------

def _parse_root(xml_text: str, expected_roots: tuple[str, ...]) -> ET.Element | None:
    """Parse and validate the document root.

    Returns None for an Acknowledgement_MarketDocument ("no matching data" —
    a normal empty result), the root element for an expected document, and
    raises for anything else.
    """
    root = ET.fromstring(xml_text)  # ET.ParseError propagates on purpose
    name = _local(root.tag)

    if name == "Acknowledgement_MarketDocument":
        reason = _text(root, "Reason", "text") or "no reason given"
        logger.debug(f"ENTSO-E acknowledgement: {reason}")
        return None

    if name not in expected_roots:
        raise ValueError(
            f"Unexpected ENTSO-E document root <{name}>, expected one of {expected_roots}"
        )
    return root


def _parse_interval_start(period: ET.Element) -> datetime:
    raw = _text(period, "timeInterval", "start")
    if raw is None:
        raise ValueError("Period is missing timeInterval/start")
    # ENTSO-E writes minute-precision UTC like "2026-08-24T22:00Z".
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_resolution(period: ET.Element) -> int:
    raw = _text(period, "resolution")
    if raw not in RESOLUTION_MINUTES:
        raise ValueError(f"Unsupported Period resolution {raw!r}")
    return RESOLUTION_MINUTES[raw]


def _walk_period(period: ET.Element, value_tag: str) -> dict[datetime, float]:
    """Reconstruct {timestamp: value} for one Period, filling omitted positions.

    The expected point count comes from the interval length, so the fill also
    extends a trailing run of omitted positions to the Period's end — the spec
    reads those as "value unchanged until the interval closes".
    """
    start = _parse_interval_start(period)
    resolution = _parse_resolution(period)

    end_raw = _text(period, "timeInterval", "end")
    if end_raw is None:
        raise ValueError("Period is missing timeInterval/end")
    end = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    expected = int((end - start).total_seconds() // 60) // resolution

    by_position: dict[int, float] = {}
    for point in _children(period, "Point"):
        pos_text = _text(point, "position")
        val_text = _text(point, value_tag)
        if pos_text is None or val_text is None:
            continue
        by_position[int(pos_text)] = float(val_text)

    values: dict[datetime, float] = {}
    previous: float | None = None
    for position in range(1, expected + 1):
        if position in by_position:
            previous = by_position[position]
        elif previous is None:
            # An omitted position before the first present one has nothing to
            # repeat. Should not occur in practice; leave the slot empty.
            logger.warning(
                f"Omitted position {position} at start of Period beginning {start}"
            )
            continue
        values[start + timedelta(minutes=resolution * (position - 1))] = previous

    return values


# --- public parsers -----------------------------------------------------------

_SCALAR_ROOTS = (
    "Publication_MarketDocument",
    "GL_MarketDocument",
    "Balancing_MarketDocument",
)

# flowDirection.direction on balancing TimeSeries: A01 = surplus (positive),
# A02 = deficit (negative). Imbalance volumes are published unsigned with the
# sign carried here instead.
_FLOW_SIGN = {"A01": 1.0, "A02": -1.0}


def parse_scalar_xml(
    xml_text: str,
    value_tag: str,
    flow_signed: bool = False,
) -> pd.Series:
    """Parse a one-value-per-timestamp document into a UTC-indexed Series.

    `value_tag` is the Point's value element (`quantity`, `price.amount`, or
    `imbalance_Price.amount`). With `flow_signed`, values from a TimeSeries
    whose flowDirection.direction is A02 (deficit) are negated — the raw API
    publishes imbalance volumes unsigned and carries the sign separately.
    """
    root = _parse_root(xml_text, _SCALAR_ROOTS)
    if root is None:
        return pd.Series(dtype=float)

    # Bucketed per resolution: day-ahead prices can carry both PT60M and PT15M
    # curves during the SDAC transition. Mixing them in one index would garble
    # the measured resolution, so the finest wins (it is the current MTU).
    by_resolution: dict[int, dict[datetime, float]] = {}

    for timeseries in _children(root, "TimeSeries"):
        sign = 1.0
        if flow_signed:
            direction = _text(timeseries, "flowDirection.direction")
            if direction is not None:
                sign = _FLOW_SIGN.get(direction, 1.0)

        for period in _children(timeseries, "Period"):
            resolution = _parse_resolution(period)
            bucket = by_resolution.setdefault(resolution, {})
            for timestamp, value in _walk_period(period, value_tag).items():
                # Overlapping periods republish the same instant; the later
                # occurrence is the more recent publication and wins.
                bucket[timestamp] = sign * value

    if not by_resolution:
        return pd.Series(dtype=float)

    if len(by_resolution) > 1:
        logger.warning(
            f"Document mixes resolutions {sorted(by_resolution)} min; keeping the finest"
        )
    values = by_resolution[min(by_resolution)]

    series = pd.Series(values, dtype=float)
    series.index = pd.DatetimeIndex(series.index, tz="UTC")
    return series.sort_index()


def parse_generation_xml(xml_text: str) -> pd.DataFrame:
    """Parse an A75 generation document into a DataFrame.

    UTC tz-aware index; one column per psrType B-code; NaN where a fuel has no
    value at a timestamp (fuels are published by different parties and their
    TimeSeries can end at different instants — the ragged tail the normalizer
    trims).

    A TimeSeries carrying `outBiddingZone_Domain.mRID` is **consumption** —
    pumped storage drawing power, which is load, not generation. Counting it
    would inflate hydro, so it is skipped here. (This is the raw-API
    equivalent of the "Actual Consumption" column entsoe-py used to emit.)
    """
    root = _parse_root(xml_text, ("GL_MarketDocument",))
    if root is None:
        return pd.DataFrame()

    by_code: dict[str, dict[datetime, float]] = {}

    for timeseries in _children(root, "TimeSeries"):
        if _child(timeseries, "outBiddingZone_Domain.mRID") is not None:
            continue  # consumption, not generation

        code = _text(timeseries, "MktPSRType", "psrType")
        if code is None:
            logger.warning("Generation TimeSeries without a psrType; skipping")
            continue

        bucket = by_code.setdefault(code, {})
        for period in _children(timeseries, "Period"):
            for timestamp, value in _walk_period(period, "quantity").items():
                if timestamp in bucket:
                    # Two TimeSeries reporting the same (fuel, instant) —
                    # summed to match how split publications aggregate, and
                    # logged because a republication would double-count here.
                    logger.warning(f"Duplicate generation value for {code} at {timestamp}")
                    bucket[timestamp] += value
                else:
                    bucket[timestamp] = value

    if not by_code:
        return pd.DataFrame()

    frame = pd.DataFrame(by_code)
    frame.index = pd.DatetimeIndex(frame.index, tz="UTC")
    return frame.sort_index()


# --- multi-document wrappers --------------------------------------------------
#
# The raw client hands over a *list* of documents: the platform zips the
# response when it spans several (balancing data does this routinely). Where
# documents overlap on a timestamp, the later document wins — zip members are
# read in name order, and a later document is the more recent publication.

def parse_scalar_documents(
    documents: list[str],
    value_tag: str,
    flow_signed: bool = False,
) -> pd.Series:
    """Parse and merge every document into one UTC-indexed Series."""
    parts = [
        parse_scalar_xml(doc, value_tag, flow_signed=flow_signed)
        for doc in documents
    ]
    parts = [part for part in parts if not part.empty]
    if not parts:
        return pd.Series(dtype=float)
    if len(parts) == 1:
        return parts[0]

    merged = pd.concat(parts)
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def extract_currency(documents: list[str]) -> str | None:
    """Read the currency off the first TimeSeries that declares one.

    Imbalance prices are settled in the national currency (CZK for ČEPS, PLN
    for PSE, EUR elsewhere), so the unit cannot be declared per series — it
    has to come from the document's `currency_Unit.name`.
    """
    for doc in documents:
        try:
            root = ET.fromstring(doc)
        except ET.ParseError:
            continue
        for timeseries in _children(root, "TimeSeries"):
            currency = _text(timeseries, "currency_Unit.name")
            if currency:
                return currency
    return None


def parse_generation_documents(documents: list[str]) -> pd.DataFrame:
    """Parse and merge every document into one B-code DataFrame."""
    parts = [parse_generation_xml(doc) for doc in documents]
    parts = [part for part in parts if not part.empty]
    if not parts:
        return pd.DataFrame()
    if len(parts) == 1:
        return parts[0]

    merged = pd.concat(parts)
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()
