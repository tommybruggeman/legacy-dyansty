"""What an injury does to a player's next two seasons, by body part and how long he was out.

From the free nflverse weekly injury reports (2012 on) and league-scoring stats: each player-season gets the body part
he was most often listed Out/Doubtful with, and how many games he missed. For fantasy regulars (8+ PPG the season
before), the effect is his PPG one and two seasons later against healthy players of the same position and age, plus
how often he misses 4+ games the next season. Thin samples are pulled toward "no effect".

The bot uses it two ways: a price tilt on the next two seasons, and a plain-English note ("major knee: about 60% of
normal the next year, about 80% the year after") so an injury is explained, not just a lower number.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

INJURY_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
SHRINK = 20                       # player-seasons of pull toward no effect
MAJOR, MODERATE = 6, 2            # games missed
GROUPS = (
    ("achilles", "Achilles"), ("knee", "Knee"), ("ankle", "Ankle"), ("hamstring", "Hamstring"),
    ("foot|toe|heel", "Foot/toe"), ("concussion|head", "Concussion"),
    ("shoulder|collarbone|pectoral|elbow|arm", "Shoulder/arm"),
    ("groin|hip|quad|thigh|abdomen|oblique|core", "Groin/hip/thigh"), ("calf|shin|fibula|leg", "Calf/lower leg"),
    ("back|neck", "Back/neck"), ("rib|chest", "Ribs/chest"), ("hand|wrist|finger|thumb", "Hand/wrist"),
)


def body_part(text: object) -> str | None:
    t = str(text or "").lower()
    for pattern, name in GROUPS:
        if any(p in t for p in pattern.split("|")):
            return name
    return None


def main_injuries(reports: pd.DataFrame) -> pd.Series:
    """(gsis_id, season) -> the body part he was most often listed Out/Doubtful with in the regular season."""
    r = reports[(reports.game_type == "REG") & reports.report_status.isin(["Out", "Doubtful"])].copy()
    r["part"] = r.report_primary_injury.map(body_part)
    r = r.dropna(subset=["part"])
    return r.groupby(["gsis_id", "season"]).part.agg(lambda s: s.value_counts().index[0]).rename("injury")


def season_lines(table: pd.DataFrame, injuries: pd.Series) -> pd.DataFrame:
    """table: player_id, season, position, ppg, g, age. Adds injury, games missed and the next two seasons' PPG/games."""
    t = table.merge(injuries, left_on=["player_id", "season"], right_index=True, how="left")
    t["missed"] = (np.where(t.season >= 2021, 17, 16) - t.g).clip(0)
    key = t.set_index(["player_id", "season"])
    for col in ("ppg", "g"):
        for off in (-1, 1, 2):
            src = key[col]
            t[f"{col}{off:+d}"] = [src.get((p, s + off), np.nan) for p, s in zip(t.player_id, t.season)]
    t["age_band"] = pd.cut(t.age, [0, 25, 28, 31, 45])
    return t


def severity(missed: float) -> str | None:
    return "major" if missed >= MAJOR else "moderate" if missed >= MODERATE else None


def effects(lines: pd.DataFrame, upto: int) -> dict[str, object]:
    """Fit on injury seasons s with s + 2 <= upto (walk-forward). Returns {"by": {(severity, part): {...}}, "miss4": rate}."""
    b = lines[(lines["ppg-1"] >= 8) & (lines.season + 2 <= upto)].copy()
    b["r1"], b["r2"] = b["ppg+1"] / b["ppg-1"], b["ppg+2"] / b["ppg-1"]
    sched_next = np.where(b.season + 1 >= 2021, 17, 16)
    b["miss4"] = (sched_next - b["g+1"].fillna(0)) >= 4
    healthy = b[b.missed <= 1]
    ref = healthy.groupby(["position", "age_band"], observed=True)[["r1", "r2"]].median()
    idx = pd.MultiIndex.from_arrays([b.position, b.age_band])
    b["e1"] = b.r1 / ref.r1.reindex(idx).to_numpy()
    b["e2"] = b.r2 / ref.r2.reindex(idx).to_numpy()
    base_miss = float(healthy.miss4.mean()) if len(healthy) else 0.38
    by: dict[tuple[str, str], dict[str, float]] = {}
    b["sev"] = b.missed.map(severity)
    for (sev, part), g in b.dropna(subset=["sev", "injury"]).groupby(["sev", "injury"]):
        out: dict[str, float] = {"n": int(len(g))}
        for k in ("e1", "e2"):
            x = g[k].dropna()
            out[k] = round(float(1 + (x.median() - 1) * len(x) / (len(x) + SHRINK)), 3) if len(x) else 1.0
        out["miss4"] = round(float(base_miss + (g.miss4.mean() - base_miss) * len(g) / (len(g) + SHRINK)), 3)
        by[(sev, part)] = out
    return {"by": by, "miss4": round(base_miss, 3)}


def player_effect(fit: dict[str, object], part: str | None, missed: float) -> dict[str, float] | None:
    """Effect for a player who missed `missed` games with `part` last season, or None when it doesn't apply."""
    sev = severity(missed)
    if not part or not sev:
        return None
    return fit["by"].get((sev, part))   # type: ignore[union-attr]


def describe(part: str, missed: float, effect: dict[str, float], base_miss4: float) -> str:
    """One line the bot can say about the injury."""
    return (f"{severity(missed)} {part.lower()} ({int(missed)} games missed): players like this scored about "
            f"{effect['e1']:.0%} of normal the next season and {effect['e2']:.0%} the season after, and "
            f"{effect['miss4']:.0%} missed 4+ games the next year (typical: {base_miss4:.0%}).")
