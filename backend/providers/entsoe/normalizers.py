"""Normalizers turning raw ENTSO-E frames into NormalizedSeries.

All provider quirks are absorbed here — nothing ENTSO-E-shaped may reach the API
layer or the frontend. Two normalizers live here because they are two genuinely
different shapes:

- `normalize_scalar_series` — one value per timestamp (prices, load, imbalance).
  These three differ only in name, unit and declared resolution, so they share a
  body; that is also why the `fetched_at` convention and the Series/DataFrame
  coercion each exist in exactly one place.
- `normalize_generation` — one dict of canonical categories per timestamp.
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from backend.models import NormalizedSeries, Point
from backend.providers.entsoe.psr_types import normalize_generation_sources

logger = logging.getLogger(__name__)

# Declared unit and fallback resolution per scalar series. The resolution is only
# a fallback — the real one is measured off the index, see _infer_resolution().
SCALAR_SERIES = {
    "day_ahead_prices": ("EUR/MWh", 60),
    "load": ("MW", 15),
    "imbalance": ("MW", 15),
}

GENERATION_UNIT = "MW"
GENERATION_FALLBACK_RESOLUTION = 15


def _to_series(data: pd.Series | pd.DataFrame, series: str) -> pd.Series:
    """Reduce a one-value-per-timestamp result to a Series.

    entsoe-py is inconsistent about this and its type hints cannot be trusted:
    `query_day_ahead_prices` returns a Series, `query_load` returns a one-column
    DataFrame, and `query_imbalance_volumes` is annotated `-> pd.DataFrame` but
    actually returns a Series. Iterating a DataFrame with `.items()` yields
    (column, Series) pairs rather than (timestamp, value), so getting this wrong
    raises "truth value of a Series is ambiguous" on the first row.
    """
    if isinstance(data, pd.Series):
        return data
    if data.shape[1] > 1:
        logger.warning(
            f"{series}: expected one column, got {list(data.columns)}; using the first"
        )
    return data.iloc[:, 0]


def _infer_resolution(index: pd.Index, fallback: int) -> int:
    """Measure the market time unit in minutes from the spacing of the index.

    Declaring it per series would be a guess: ENTSO-E resolutions change (SDAC
    has moved day-ahead prices from 60 to 15 minutes), and the value is part of
    the published NormalizedSeries contract.
    """
    if len(index) < 2:
        return fallback
    median_delta = index.to_series().diff().median()
    if pd.isna(median_delta) or median_delta.total_seconds() <= 0:
        return fallback
    return int(round(median_delta.total_seconds() / 60))


def normalize_scalar_series(
    data: pd.Series | pd.DataFrame,
    country: str,
    series: str,
) -> NormalizedSeries:
    """Convert a raw one-value-per-timestamp ENTSO-E result to NormalizedSeries."""
    unit, fallback_resolution = SCALAR_SERIES[series]
    fetched_at = datetime.now(timezone.utc)

    if data.empty:
        logger.debug(f"Empty {series} data for {country}")
        return NormalizedSeries(
            country=country,
            series=series,
            unit=unit,
            resolution_minutes=fallback_resolution,
            points=[],
            fetched_at=fetched_at,
        )

    values = _to_series(data, series)

    points = [
        Point(t=idx.to_pydatetime(), v=float(val))
        for idx, val in values.items()
        if pd.notna(val)
    ]

    return NormalizedSeries(
        country=country,
        series=series,
        unit=unit,
        resolution_minutes=_infer_resolution(values.index, fallback_resolution),
        points=points,
        latest=points[-1] if points else None,
        fetched_at=fetched_at,
    )


def _generation_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten entsoe-py's generation columns to plain production-type names.

    Columns are a MultiIndex of (production_type, aggregation) when any fuel
    reports consumption, and flat strings when none does. "Actual Consumption"
    is load, not generation — counting it would inflate pumped storage.
    """
    keep, names = [], []
    for col in df.columns:
        if isinstance(col, tuple):
            name, aggregation = col[0], (col[1] if len(col) > 1 else "")
        else:
            name, aggregation = col, ""
        if aggregation == "Actual Consumption":
            continue
        keep.append(col)
        names.append(name)

    gen = df[keep].copy()
    gen.columns = names
    # min_count=1 keeps a fuel NaN when it reported nothing, rather than
    # collapsing it to 0.0 — the distinction matters for _trim_ragged_tail.
    return gen.T.groupby(level=0).sum(min_count=1).T


def _trim_ragged_tail(gen: pd.DataFrame, country: str) -> pd.DataFrame:
    """Drop trailing timestamps where fuels have not all reported yet.

    ENTSO-E fuels are published by different parties and do not land at the same
    instant, so the newest rows are typically partly filled. Those rows would
    otherwise be emitted with the missing fuels as 0.0 — indistinguishable from a
    plant genuinely producing nothing, which silently understates `latest`.
    """
    reported = gen.notna().sum(axis=1)
    if reported.empty:
        return gen

    expected = int(reported.mode().iloc[0])
    last = len(gen)
    while last > 0 and reported.iloc[last - 1] < expected:
        last -= 1

    if last < len(gen):
        logger.info(
            f"Dropping {len(gen) - last} trailing incomplete generation "
            f"row(s) for {country} (expected {expected} fuels reporting)"
        )
    return gen.iloc[:last]


def normalize_generation(df: pd.DataFrame, country: str) -> NormalizedSeries:
    """Convert raw ENTSO-E generation DataFrame to NormalizedSeries."""
    fetched_at = datetime.now(timezone.utc)

    def empty(resolution: int = GENERATION_FALLBACK_RESOLUTION) -> NormalizedSeries:
        return NormalizedSeries(
            country=country,
            series="generation",
            unit=GENERATION_UNIT,
            resolution_minutes=resolution,
            points=[],
            fetched_at=fetched_at,
        )

    if df.empty:
        logger.debug(f"Empty generation data for {country}")
        return empty()

    gen = _trim_ragged_tail(_generation_columns(df), country)
    if gen.empty:
        return empty()

    points = [
        Point(
            t=idx.to_pydatetime(),
            by_source=normalize_generation_sources(row.dropna().to_dict()),
        )
        for idx, row in gen.iterrows()
    ]

    return NormalizedSeries(
        country=country,
        series="generation",
        unit=GENERATION_UNIT,
        resolution_minutes=_infer_resolution(gen.index, GENERATION_FALLBACK_RESOLUTION),
        points=points,
        latest=points[-1] if points else None,
        fetched_at=fetched_at,
    )
