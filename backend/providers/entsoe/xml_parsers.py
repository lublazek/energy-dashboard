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


def _merge_resolution_buckets(
    by_resolution: dict[int, dict[datetime, float]],
) -> dict[datetime, float]:
    """Collapse per-resolution buckets into one {timestamp: value} mapping.

    A document can carry the same quantity at two resolutions — day-ahead
    prices publish both a PT60M and a PT15M curve across the SDAC transition,
    and generation fuels are reported at whatever MTU their party uses.

    Where the curves cover the same instant the finer one wins: it is the
    current market time unit, and mixing both into one index would garble the
    resolution `_infer_resolution` measures off the spacing.

    Where they do **not** overlap the coarse curve is kept. This is the shape
    the transition actually takes — the switch happens on a date boundary, so
    a 48 h window straddling it holds PT60M for day one and PT15M for day two.
    Keeping only the finest bucket silently dropped day one entirely.
    """
    if len(by_resolution) == 1:
        return next(iter(by_resolution.values()))

    logger.info(
        f"Document mixes resolutions {sorted(by_resolution)} min; the finer curve "
        f"wins where they overlap, the coarser is kept where it does not"
    )

    # Coarsest first, so finer buckets overwrite it on shared timestamps while
    # instants only the coarse curve covers survive.
    merged: dict[datetime, float] = {}
    for resolution in sorted(by_resolution, reverse=True):
        merged.update(by_resolution[resolution])
    return merged


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

# Under a dual-pricing regime an A85 document publishes TWO prices for the same
# instant, told apart by imbalance_Price.category (ENTSO-E PriceCategory
# codelist: A04 = excess balance, A06 = insufficient balance). Reading the tag
# is what makes the choice deterministic — without it the parser kept whichever
# TimeSeries the platform happened to serialize first, so the published curve
# could flip between two different prices from one poll to the next with
# nothing in the data to show it had.
_PRICE_CATEGORY_TAG = "imbalance_Price.category"

# Shortage is the headline number operators watch, so A06 wins when both are
# present. Single-price regimes emit no category at all and are unaffected.
_PRICE_CATEGORY_PREFERENCE = ("A06", "A04")


def _select_price_category(
    by_category: dict[str | None, dict[int, dict[datetime, float]]],
) -> dict[int, dict[datetime, float]]:
    """Pick one imbalance-price category's buckets, deterministically."""
    if len(by_category) == 1:
        return next(iter(by_category.values()))

    for preferred in _PRICE_CATEGORY_PREFERENCE:
        if preferred in by_category:
            logger.info(
                f"Dual imbalance pricing: categories {sorted(map(str, by_category))} "
                f"present, publishing {preferred}"
            )
            return by_category[preferred]

    # Unknown categories only: sorted() keeps the pick stable across polls
    # even though we cannot say which one is meant.
    chosen = sorted(by_category, key=lambda c: (c is None, str(c)))[0]
    logger.warning(
        f"Unrecognised imbalance price categories {sorted(map(str, by_category))}; "
        f"publishing {chosen!r}"
    )
    return by_category[chosen]


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

    # Bucketed per price category, then per resolution: dual pricing splits one
    # instant across two categories, and day-ahead prices can carry both PT60M
    # and PT15M curves during the SDAC transition. See _select_price_category
    # and _merge_resolution_buckets for how the buckets collapse to one curve.
    by_category: dict[str | None, dict[int, dict[datetime, float]]] = {}

    for timeseries in _children(root, "TimeSeries"):
        sign = 1.0
        if flow_signed:
            direction = _text(timeseries, "flowDirection.direction")
            if direction is not None:
                sign = _FLOW_SIGN.get(direction, 1.0)

        category = _text(timeseries, _PRICE_CATEGORY_TAG)
        by_resolution = by_category.setdefault(category, {})

        for period in _children(timeseries, "Period"):
            resolution = _parse_resolution(period)
            bucket = by_resolution.setdefault(resolution, {})
            for timestamp, value in _walk_period(period, value_tag).items():
                # Overlapping periods republish the same instant; the later
                # occurrence is the more recent publication and wins.
                bucket[timestamp] = sign * value

    by_category = {cat: res for cat, res in by_category.items() if res}
    if not by_category:
        return pd.Series(dtype=float)

    values = _merge_resolution_buckets(_select_price_category(by_category))

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

    # Per fuel, then per resolution. Fuels are published by different parties
    # and need not share a market time unit: a PT60M fuel unioned onto a PT15M
    # index lands on one timestamp in four and reads as 2000, 0, 0, 0, 2000 —
    # a sawtooth that `_trim_ragged_tail` cannot catch, because its mode-based
    # "expected fuel count" is itself computed from the garbled frame.
    by_code: dict[str, dict[int, dict[datetime, float]]] = {}

    for timeseries in _children(root, "TimeSeries"):
        if _child(timeseries, "outBiddingZone_Domain.mRID") is not None:
            continue  # consumption, not generation

        code = _text(timeseries, "MktPSRType", "psrType")
        if code is None:
            logger.warning("Generation TimeSeries without a psrType; skipping")
            continue

        buckets = by_code.setdefault(code, {})
        for period in _children(timeseries, "Period"):
            resolution = _parse_resolution(period)
            bucket = buckets.setdefault(resolution, {})
            for timestamp, value in _walk_period(period, "quantity").items():
                if timestamp in bucket:
                    # Two TimeSeries reporting the same (fuel, instant, MTU) —
                    # summed to match how split publications aggregate, and
                    # logged because a republication would double-count here.
                    logger.warning(f"Duplicate generation value for {code} at {timestamp}")
                    bucket[timestamp] += value
                else:
                    bucket[timestamp] = value

    if not by_code:
        return pd.DataFrame()

    columns = {
        code: _merge_resolution_buckets(buckets)
        for code, buckets in by_code.items()
        if buckets
    }
    frame = pd.DataFrame(columns)
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
    """Parse and merge every document into one B-code DataFrame.

    Merged **cell by cell**, not row by row. A zipped A75 response splits fuels
    across members, so two documents routinely cover the same timestamps with
    different psrTypes. Dropping duplicate rows (`index.duplicated`) discards
    every fuel the earlier document carried — and because `normalize_generation`
    seeds all canonical categories to 0.0, the loss is published as a genuine
    zero rather than a gap. That is silent, plausible, wrong data.

    `combine_first` keeps the later document's value wherever it has one and
    falls back to the earlier document otherwise, so the union of fuels and
    timestamps survives and a NaN never overwrites a real reading.
    """
    parts = [parse_generation_xml(doc) for doc in documents]
    parts = [part for part in parts if not part.empty]
    if not parts:
        return pd.DataFrame()
    if len(parts) == 1:
        return parts[0]

    # Documents arrive in zip-name order; later is the more recent publication,
    # so it is the one that must win on a genuine overlap.
    merged = parts[0]
    for part in parts[1:]:
        merged = part.combine_first(merged)
    return merged.sort_index()
