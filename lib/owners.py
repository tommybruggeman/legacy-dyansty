# Legacy App/lib/owners.py
# --------------------------------------------------------------------
# Maps Sleeper usernames to readable display names for standings, etc.
# --------------------------------------------------------------------

OWNER_NAME_MAP = {
    "ConnorZar": "Connor Cassidy",
    "RollPads": "Nick Salafia",
    "Chaseseyforth": "Chase Seyforth",
    "Tommybruggeman": "Tommy Bruggeman",
    "Dburruel": "Dylan Burruel",
    "Kevinwells33": "Kevin Wells",
    "Mekel S": "Mekel Sanches",
    "Nandio": "Nando Munoz",
    "Chaychayy": "Chasen Hardy",
    "Gmoney38": "Grady Graham",
}

def display_name_for(username_or_display: str) -> str:
    """
    Returns a consistent display name for a Sleeper username.
    Falls back to the input if not found.
    """
    if not username_or_display:
        return "Unknown"
    return OWNER_NAME_MAP.get(username_or_display.strip(), username_or_display.strip())


# --- Safe import handling (for other libs that may import this) ---
try:
    # Allow lib modules to import this even if "Legacy App" isn't a package
    __all__ = ["display_name_for", "OWNER_NAME_MAP"]
except Exception:
    pass
