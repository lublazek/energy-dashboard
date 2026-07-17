import logging
from datetime import datetime

import pandas as pd
from entsoe import EntsoePandasClient

logger = logging.getLogger(__name__)


class ENTSOEClient:
    """Thin wrapper around EntsoePandasClient."""

    def __init__(self, api_key: str) -> None:
        self.client = EntsoePandasClient(api_key=api_key)

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
                start=pd.Timestamp(start, tz="UTC"),
                end=pd.Timestamp(end, tz="UTC"),
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
                start=pd.Timestamp(start, tz="UTC"),
                end=pd.Timestamp(end, tz="UTC"),
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
                start=pd.Timestamp(start, tz="UTC"),
                end=pd.Timestamp(end, tz="UTC"),
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
                start=pd.Timestamp(start, tz="UTC"),
                end=pd.Timestamp(end, tz="UTC"),
            )
        except Exception as e:
            logger.error(f"Error fetching imbalance volumes: {e}")
            raise
