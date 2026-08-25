import logging
from datetime import datetime

import pandas as pd
from entsoe import EntsoePandasClient

logger = logging.getLogger(__name__)


# entsoe-py defaults to timeout=None, which requests reads as "wait forever".
# A stalled connection would then hang the fetch permanently; with a timeout it
# becomes an ordinary error that the scheduler records and retries.
REQUEST_TIMEOUT_SECONDS = 30


def _utc(dt: datetime) -> pd.Timestamp:
    """Convert a datetime to a UTC pandas Timestamp.

    entsoe-py wants tz-aware Timestamps. `pd.Timestamp(dt, tz="UTC")` raises if
    `dt` already has a tzinfo, so localize or convert depending on which we got.
    """
    ts = pd.Timestamp(dt)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


class ENTSOEClient:
    """Thin wrapper around EntsoePandasClient."""

    def __init__(self, api_key: str) -> None:
        self.client = EntsoePandasClient(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def query_day_ahead_prices(
        self,
        bidding_zone: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch day-ahead electricity prices."""
        try:
            return self.client.query_day_ahead_prices(
                bidding_zone,
                start=_utc(start),
                end=_utc(end),
            )
        except Exception as e:
            logger.error(f"Error fetching day-ahead prices: {e}")
            raise

    def query_load(
        self,
        country_code: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch total load (consumption)."""
        try:
            return self.client.query_load(
                country_code,
                start=_utc(start),
                end=_utc(end),
            )
        except Exception as e:
            logger.error(f"Error fetching load: {e}")
            raise

    def query_generation(
        self,
        country_code: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch actual generation by PSR type."""
        try:
            return self.client.query_generation(
                country_code,
                start=_utc(start),
                end=_utc(end),
                psr_type=None,
            )
        except Exception as e:
            logger.error(f"Error fetching generation: {e}")
            raise

    def query_imbalance_volumes(
        self,
        bidding_zone: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch imbalance volumes."""
        try:
            return self.client.query_imbalance_volumes(
                bidding_zone,
                start=_utc(start),
                end=_utc(end),
            )
        except Exception as e:
            logger.error(f"Error fetching imbalance volumes: {e}")
            raise
