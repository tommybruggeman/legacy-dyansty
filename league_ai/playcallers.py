"""Who calls the offensive plays for each NFL team, and where he called them before. Public reporting, kept by hand.

nflverse lists head coaches but not coordinators, so a summer change of play-caller (a new OC, or a head coach taking
the calls back) was invisible to the AI. This is the 2026 list (ESPN / ABC "Who calls plays for every NFL team in
2026?"); update it each offseason. The AI uses it as context only: a new play-caller makes last season's team
tendencies a weaker guide, and his old team's tendencies a hint of what's coming.
"""
from __future__ import annotations

from typing import Mapping

SOURCE = "ESPN, Who calls plays for every NFL team in 2026"

# team: (play-caller, role, new to calling plays for this team in 2026, where he called plays before)
PLAYCALLERS: dict[int, dict[str, tuple[str, str, bool, str | None]]] = {2026: {
    "BUF": ("Joe Brady", "HC", False, "CAR"), "MIA": ("Bobby Slowik", "OC", True, "HOU"), "NE": ("Josh McDaniels", "OC", False, None),
    "NYJ": ("Frank Reich", "OC", True, "IND, CAR"), "BAL": ("Declan Doyle", "OC", True, None), "CIN": ("Zac Taylor", "HC", False, None),
    "CLE": ("Todd Monken", "HC", True, "BAL"), "PIT": ("Mike McCarthy", "HC", True, "DAL, GB"), "HOU": ("Nick Caley", "OC", False, None),
    "IND": ("Shane Steichen", "HC", False, "PHI, LAC"), "JAX": ("Liam Coen", "HC", False, "TB"), "TEN": ("Brian Daboll", "OC", True, "NYG"),
    "DEN": ("Davis Webb", "OC", True, None), "KC": ("Andy Reid", "HC", False, None), "LV": ("Klint Kubiak", "HC", True, "SEA, NO, MIN"),
    "LAC": ("Mike McDaniel", "OC", True, "MIA"), "DAL": ("Brian Schottenheimer", "HC", False, "SEA, LAR, NYJ"), "NYG": ("Matt Nagy", "OC", True, "CHI, KC"),
    "PHI": ("Sean Mannion", "OC", True, None), "WAS": ("David Blough", "OC", True, None), "CHI": ("Ben Johnson", "HC", False, "DET"),
    "DET": ("Drew Petzing", "OC", True, "ARI"), "GB": ("Matt LaFleur", "HC", False, None), "MIN": ("Kevin O'Connell", "HC", False, None),
    "ATL": ("Tommy Rees", "OC", True, "CLE"), "CAR": ("Brad Idzik", "OC", True, None), "NO": ("Kellen Moore", "HC", False, "DAL, PHI"),
    "TB": ("Zac Robinson", "OC", True, "ATL"), "ARI": ("Mike LaFleur", "HC", True, "NYJ"), "LAR": ("Sean McVay", "HC", False, None),
    "SF": ("Kyle Shanahan", "HC", False, None), "SEA": ("Brian Fleury", "OC", True, None),
}}


def note(season: int, team: str, tendencies: Mapping[str, Mapping[str, object]] | None = None) -> str | None:
    """'plays called by Mike McDaniel (OC, new in 2026; called plays for MIA, 54% pass rate there last season)'."""
    row = PLAYCALLERS.get(int(season), {}).get(team)
    if not row:
        return None
    name, role, new, before = row
    text = f"plays called by {name} ({role}" + (f", new in {season}" if new else "")
    if new and before:
        prev = before.split(",")[0].strip()
        t = (tendencies or {}).get(prev) or {}
        rate = t.get("pass_rate")
        text += f"; called plays for {before}" + (f", {round(100 * float(rate))}% pass rate at {prev} last season" if rate else "")
    elif new:
        text += "; first time calling plays in the NFL"
    return text + ")"
