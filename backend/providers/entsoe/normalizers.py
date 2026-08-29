"""Normalizers turning parsed ENTSO-E data into NormalizedSeries.

The XML parsers hand over plain pandas objects (a Series for the scalar
series, a B-code-keyed DataFrame for generation); this module turns them into
the published `NormalizedSeries` contract. Two normalizers, because there are
two genuinely different shapes:

- `normalize_scalar_series` — one value per timestamp (prices, load,
  imbalance volumes, imbalance prices). These differ only in name, unit and
  fallback resolution, so they share a body.
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
    "imbalance_prices": ("EUR/MWh", 15),
}

GENERATION_UNIT = "MW"
GENERATION_FALLBACK_RESOLUTION = 15


def _infer_resolution(index: pd.Index, fallback: int) -> int:
    """Measure the market time unit in minutes from the spacing of the index.

    Declaring it per series would be a guess: ENTSO-E resolutions change (SDAC
    has moved day-ahead prices from 60 to 15 minutes), and the value is part of
    the published NormalizedSeries contract. The parsers fill omitted positions,
    so within a Period the spacing is dense and the median is the true unit.
    """
    if len(index) < 2:
        return fallback
    median_delta = index.to_series().diff().median()
    if pd.isna(median_delta) or median_delta.total_seconds() <= 0:
        return fallback
    return int(round(median_delta.total_seconds() / 60))


def normalize_scalar_series(
    values: pd.Series,
    country: str,
    series: str,
    unit_override: str | None = None,
) -> NormalizedSeries:
    """Convert a parsed one-value-per-timestamp Series to NormalizedSeries.

    `unit_override` replaces the declared unit — imbalance prices are settled
    in the national currency, which only the response itself knows.
    """
    unit, fallback_resolution = SCALAR_SERIES[series]
    if unit_override:
        unit = unit_override
    fetched_at = datetime.now(timezone.utc)

    if values.empty:
        logger.debug(f"Empty {series} data for {country}")
        return NormalizedSeries(
            country=country,
            series=series,
            unit=unit,
            resolution_minutes=fallback_resolution,
            points=[],
            fetched_at=fetched_at,
        )

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


def _trim_ragged_tail(gen: pd.DataFrame, country: str) -> pd.DataFrame:
    """Drop trailing timestamps where fuels have not all reported yet.

    ENTSO-E fuels are published by different parties and do not land at the same
    instant, so the newest rows are typically partly filled. Those rows would
    otherwise be emitted with the missing fuels as 0.0 — indistinguishable from a
    plant genuinely producing nothing, which silently understates `latest`.
    Complete-but-older beats recent-but-partial: an explicit product decision.
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


def normalize_generation(gen: pd.DataFrame, country: str) -> NormalizedSeries:
    """Convert a parsed B-code generation DataFrame to NormalizedSeries."""
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

    if gen.empty:
        logger.debug(f"Empty generation data for {country}")
        return empty()

    gen = _trim_ragged_tail(gen, country)
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
