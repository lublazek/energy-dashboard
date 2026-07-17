"""Actual generation per production type from ENTSO-E."""

import logging
from datetime import datetime

import pandas as pd

from backend.models import NormalizedSeries, Point
from backend.providers.entsoe.psr_types import normalize_generation_sources

logger = logging.getLogger(__name__)


def normalize_generation(
    df: pd.DataFrame,
    country: str,
) -> NormalizedSeries:
    """Convert raw ENTSO-E generation DataFrame to NormalizedSeries."""
    if df.empty:
        logger.debug(f"Empty generation data for {country}")
        return NormalizedSeries(
            country=country,
            series="generation",
            unit="MW",
            resolution_minutes=15,
            points=[],
            fetched_at=datetime.now(tz=None).replace(tzinfo=None),
        )

    points = []
    for idx in df.index:
        raw_sources = {}
        for col in df.columns:
            val = df.loc[idx, col]
            if pd.notna(val):
                raw_sources[col] = float(val)

        if raw_sources:
            by_source = normalize_generation_sources(raw_sources)
            points.append(
                Point(
                    t=idx.to_pydatetime(),
                    by_source=by_source,
                )
            )

    latest = points[-1] if points else None

    return NormalizedSeries(
        country=country,
        series="generation",
        unit="MW",
        resolution_minutes=15,
        points=points,
        latest=latest,
        fetched_at=datetime.now(tz=None).replace(tzinfo=None),
    )
