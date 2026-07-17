"""Imbalance volumes from ENTSO-E."""

import logging
from datetime import datetime

import pandas as pd

from backend.models import NormalizedSeries, Point

logger = logging.getLogger(__name__)


def normalize_imbalance(
    df: pd.DataFrame,
    country: str,
) -> NormalizedSeries:
    """
    Convert raw ENTSO-E imbalance DataFrame to NormalizedSeries.
    Sign convention: positive = system long, negative = system short.
    """
    if df.empty:
        logger.debug(f"Empty imbalance data for {country}")
        return NormalizedSeries(
            country=country,
            series="imbalance",
            unit="MW",
            resolution_minutes=15,
            points=[],
            fetched_at=datetime.now(tz=None).replace(tzinfo=None),
        )

    points = []
    for idx, val in df.items():
        if pd.notna(val):
            points.append(
                Point(
                    t=idx.to_pydatetime(),
                    v=float(val),
                )
            )

    latest = points[-1] if points else None

    return NormalizedSeries(
        country=country,
        series="imbalance",
        unit="MW",
        resolution_minutes=15,
        points=points,
        latest=latest,
        fetched_at=datetime.now(tz=None).replace(tzinfo=None),
    )
