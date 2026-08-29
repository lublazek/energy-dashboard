"""ENTSO-E generation-type mappings.

The raw API identifies production types by `psrType` B-codes (e.g. B14 =
Nuclear) carried on each generation TimeSeries. B-codes are the stable
identifier — the human-readable names entsoe-py used to expose were the
library's own translation table, not API data. See docs/entsoe.md §4 for the
full code table.
"""

# Canonical categories the frontend knows about (see GENERATION_COLORS in app.js).
CANONICAL_SOURCES = (
    "nuclear",
    "lignite",
    "hard_coal",
    "gas",
    "wind",
    "solar",
    "hydro",
    "biomass",
    "other",
)

# psrType B-code -> canonical category (docs/entsoe.md §4).
# Anything not listed here falls through to "other".
PSR_CODE_MAP = {
    "B01": "biomass",    # Biomass
    "B02": "lignite",    # Fossil Brown coal/Lignite
    "B03": "gas",        # Fossil Coal-derived gas
    "B04": "gas",        # Fossil Gas
    "B05": "hard_coal",  # Fossil Hard coal
    "B06": "other",      # Fossil Oil
    "B07": "other",      # Fossil Oil shale
    "B08": "other",      # Fossil Peat
    "B09": "other",      # Geothermal
    "B10": "hydro",      # Hydro Pumped Storage
    "B11": "hydro",      # Hydro Run-of-river and poundage
    "B12": "hydro",      # Hydro Water Reservoir
    "B13": "other",      # Marine
    "B14": "nuclear",    # Nuclear
    "B15": "other",      # Other renewable
    "B16": "solar",      # Solar
    "B17": "other",      # Waste
    "B18": "wind",       # Wind Offshore
    "B19": "wind",       # Wind Onshore
    "B20": "other",      # Other
    "B25": "other",      # Energy storage
}


def psr_code_to_category(code: str) -> str:
    """Map a psrType B-code to a canonical category."""
    return PSR_CODE_MAP.get(code, "other")


def normalize_generation_sources(raw_sources: dict[str, float]) -> dict[str, float]:
    """Group raw per-psrType values into canonical categories.

    `raw_sources` keys are psrType B-codes (e.g. "B14"); values are MW.
    Returns a dict with every canonical category present.
    """
    normalized = {source: 0.0 for source in CANONICAL_SOURCES}

    for code, value in raw_sources.items():
        normalized[psr_code_to_category(code)] += value

    return normalized
