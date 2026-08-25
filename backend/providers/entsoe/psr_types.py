"""ENTSO-E generation-type mappings.

entsoe-py's `query_generation` returns human-readable production-type names as
DataFrame column labels (e.g. "Nuclear", "Fossil Brown coal/Lignite"), NOT the
raw PSR B-codes. So we map those names to our canonical frontend categories.
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

# entsoe-py production-type name -> canonical category.
# Anything not listed here falls through to "other".
GENERATION_TYPE_MAP = {
    "Biomass": "biomass",
    "Energy storage": "other",
    "Fossil Brown coal/Lignite": "lignite",
    "Fossil Coal-derived gas": "gas",
    "Fossil Gas": "gas",
    "Fossil Hard coal": "hard_coal",
    "Fossil Oil": "other",
    "Fossil Oil shale": "other",
    "Fossil Peat": "other",
    "Geothermal": "other",
    "Hydro Pumped Storage": "hydro",
    "Hydro Run-of-river and poundage": "hydro",
    "Hydro Water Reservoir": "hydro",
    "Marine": "other",
    "Nuclear": "nuclear",
    "Other": "other",
    "Other renewable": "other",
    "Solar": "solar",
    "Waste": "other",
    "Wind Offshore": "wind",
    "Wind Onshore": "wind",
}


def generation_type_to_category(name: str) -> str:
    """Map an ENTSO-E production-type name to a canonical category."""
    return GENERATION_TYPE_MAP.get(name, "other")


def normalize_generation_sources(raw_sources: dict[str, float]) -> dict[str, float]:
    """Group raw per-production-type values into canonical categories.

    `raw_sources` keys are ENTSO-E production-type names (e.g. "Nuclear");
    values are MW. Returns a dict with every canonical category present.
    """
    normalized = {source: 0.0 for source in CANONICAL_SOURCES}

    for name, value in raw_sources.items():
        category = generation_type_to_category(name)
        normalized[category] += value

    return normalized
