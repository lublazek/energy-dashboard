"""ENTSO-E PSR (Power System Resource) type code mappings."""

PSR_TYPE_MAP = {
    "B01": "Biomass",
    "B02": "Lignite",
    "B03": "Coal",
    "B04": "Natural gas",
    "B05": "Nuclear",
    "B06": "Hydro",
    "B07": "Other",
    "B08": "Solar",
    "B09": "Wind",
    "B10": "Waste",
    "B11": "Hard coal",
    "B12": "Other renewable",
}


def psr_code_to_name(code: str) -> str:
    """Convert PSR code to human-readable name."""
    return PSR_TYPE_MAP.get(code, f"Unknown ({code})")


def normalize_generation_sources(raw_sources: dict[str, float]) -> dict[str, float]:
    """
    Normalize generation source names from PSR codes to canonical names.
    Groups sources into the standard categories.
    """
    normalized = {
        "nuclear": 0.0,
        "lignite": 0.0,
        "hard_coal": 0.0,
        "gas": 0.0,
        "wind": 0.0,
        "solar": 0.0,
        "hydro": 0.0,
        "biomass": 0.0,
        "other": 0.0,
    }

    for code, value in raw_sources.items():
        name = psr_code_to_name(code)

        if code == "B05":
            normalized["nuclear"] += value
        elif code == "B02":
            normalized["lignite"] += value
        elif code == "B11" or code == "B03":
            normalized["hard_coal"] += value
        elif code == "B04":
            normalized["gas"] += value
        elif code == "B09":
            normalized["wind"] += value
        elif code == "B08":
            normalized["solar"] += value
        elif code in ["B06", "B12"]:
            normalized["hydro"] += value if code == "B06" else 0.0
        elif code == "B01":
            normalized["biomass"] += value
        else:
            normalized["other"] += value

    return normalized
