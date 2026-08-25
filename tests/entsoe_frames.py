"""Builders for fake entsoe-py generation DataFrames.

Everything in this file is shaped like `entsoe-py`'s `query_generation` output —
a tz-aware DatetimeIndex, and columns that are either flat production-type names
or a (production_type, aggregation) MultiIndex. See docs/entsoe.md §7.

**This is the file that dies if the project drops entsoe-py for the raw REST
API.** It is kept separate from the tests on purpose: the assertions in
`test_normalize_generation.py` are about `NormalizedSeries`, which is
provider-independent, so a migration means rewriting these builders and leaving
the assertions alone.
"""

import pandas as pd

# Arbitrary but fixed — tests must not depend on the real clock. Europe/Prague
# because that is what entsoe-py localizes CZ data to.
START = "2026-08-25 00:00"
TZ = "Europe/Prague"


def index_at(periods: int, minutes: int = 15) -> pd.DatetimeIndex:
    """A tz-aware index of `periods` timestamps spaced `minutes` apart."""
    return pd.date_range(START, periods=periods, freq=f"{minutes}min", tz=TZ)


def flat_frame(fuels: dict[str, list[float]], minutes: int = 15) -> pd.DataFrame:
    """A generation frame with plain string columns.

    This is the shape entsoe-py returns when no fuel in the window reported any
    consumption. Row count is taken from the first fuel's value list.

        flat_frame({"Nuclear": [2000.0, 2100.0], "Solar": [500.0, 600.0]})
    """
    periods = len(next(iter(fuels.values())))
    return pd.DataFrame(fuels, index=index_at(periods, minutes))


def multi_frame(
    fuels: dict[tuple[str, str], list[float]], minutes: int = 15
) -> pd.DataFrame:
    """A generation frame with (production_type, aggregation) MultiIndex columns.

    entsoe-py switches to this shape as soon as *any* fuel reports consumption,
    so a single pumped-storage entry changes the shape of every other column too.

        multi_frame({
            ("Nuclear", "Actual Aggregated"): [2000.0],
            ("Hydro Pumped Storage", "Actual Consumption"): [400.0],
        })
    """
    periods = len(next(iter(fuels.values())))
    return pd.DataFrame(
        fuels,
        index=index_at(periods, minutes),
        columns=pd.MultiIndex.from_tuples(fuels.keys()),
    )
