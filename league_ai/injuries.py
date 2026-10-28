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


# Fit of `effects` on the 2012-2025 seasons (run of 2026-10-28): (severity, body part) -> PPG share the next season,
# the season after, and the chance he misses 4+ games next season. Healthy players of the same age miss 4+ games 33%
# of the time. Refit by running `effects` on fresh data; small samples are already pulled toward no effect.
BASE_MISS4 = 0.331
EFFECTS: dict[tuple[str, str], tuple[float, float, float]] = {
    ("major", "Ankle"): (0.759, 0.91, 0.555), ("major", "Back/neck"): (0.995, 0.973, 0.527),
    ("major", "Calf/lower leg"): (0.95, 0.938, 0.487), ("major", "Concussion"): (0.861, 0.919, 0.529),
    ("major", "Foot/toe"): (0.875, 0.95, 0.43), ("major", "Groin/hip/thigh"): (0.888, 0.852, 0.583),
    ("major", "Hamstring"): (0.916, 0.995, 0.44), ("major", "Hand/wrist"): (0.877, 0.953, 0.462),
    ("major", "Knee"): (0.702, 0.876, 0.572), ("major", "Ribs/chest"): (0.858, 0.911, 0.472),
    ("major", "Shoulder/arm"): (0.875, 0.89, 0.542),
    ("moderate", "Ankle"): (0.911, 0.909, 0.472), ("moderate", "Back/neck"): (0.984, 0.962, 0.379),
    ("moderate", "Calf/lower leg"): (1.0, 1.0, 0.332), ("moderate", "Concussion"): (0.997, 0.904, 0.458),
    ("moderate", "Foot/toe"): (0.906, 0.949, 0.372), ("moderate", "Groin/hip/thigh"): (0.925, 0.982, 0.416),
    ("moderate", "Hamstring"): (0.975, 0.946, 0.447), ("moderate", "Hand/wrist"): (0.972, 0.882, 0.418),
    ("moderate", "Knee"): (0.94, 0.963, 0.459), ("moderate", "Ribs/chest"): (0.962, 1.0, 0.53),
    ("moderate", "Shoulder/arm"): (1.0, 0.963, 0.462),
}
VALUE_TILT = 0.5           # share of the effect applied to a dynasty market value (the market already prices some of it)


def recent_injury(episodes: list[dict], season: int) -> tuple[float, str] | None:
    """From a player's injury episodes (nfl_injury_episodes rows), the effect of his worst injury this season or last:
    (multiplier for his dynasty value, one line explaining it). None when nothing recent applies."""
    by_season: dict[int, dict[str, float]] = {}
    for e in episodes:
        s = int(e.get("last_game_season") or 0)
        part = body_part(e.get("injury"))
        if s < season - 1 or not part:
            continue
        parts = by_season.setdefault(s, {})
        parts[part] = parts.get(part, 0.0) + float(e.get("games_missed") or 0)
    best: tuple[float, str] | None = None
    for s, parts in by_season.items():
        part, missed = max(parts.items(), key=lambda kv: kv[1])
        sev = severity(missed)
        if not sev or (sev, part) not in EFFECTS:
            continue
        e1, e2, miss4 = EFFECTS[(sev, part)]
        share = e1 if s == season else e2          # this season's injury hits next year; last season's, the year after
        mult = 1 + VALUE_TILT * (share - 1)
        note = describe(part, missed, {"e1": e1, "e2": e2, "miss4": miss4}, BASE_MISS4)
        if best is None or mult < best[0]:
            best = (mult, f"{s}: {note}")
    return best
