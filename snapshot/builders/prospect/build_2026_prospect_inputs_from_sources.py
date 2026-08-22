from __future__ import annotations

from pathlib import Path
import pandas as pd


ACTIVE_CLASS_YEAR = 2026
SOURCE_DIR = Path("data/prospects/discovery_sources")
OUTPUT_FILE = Path("data/prospects/prospect_inputs_2026.csv")

FANTASY_POS = {"QB", "RB", "WR", "TE"}


def projected_round(rank: float) -> int:
    if rank <= 32:
        return 1
    if rank <= 64:
        return 2
    if rank <= 100:
        return 3
    if rank <= 140:
        return 4
    if rank <= 180:
        return 5
    return 6


def projected_pick(rank: float) -> int:
    return max(1, int(round(rank)))


def default_role(pos: str, rank: float) -> str:
    if pos == "QB":
        return "High-value superflex QB prospect" if rank <= 32 else "Developmental superflex QB prospect"
    if pos == "RB":
        return "Premium rookie RB prospect" if rank <= 64 else "Depth/upside rookie RB prospect"
    if pos == "WR":
        return "Premium rookie WR prospect" if rank <= 64 else "Developmental rookie WR prospect"
    if pos == "TE":
        return "Premium rookie TE prospect" if rank <= 80 else "Developmental rookie TE prospect"
    return "Rookie prospect"


def estimate_college_yards_per_game(pos: str, rank: float) -> float:
    # Conservative priors only. Real production collector can overwrite later.
    quality = max(0, 1 - (rank / 220))

    if pos == "QB":
        return round(210 + quality * 120, 1)
    if pos == "RB":
        return round(65 + quality * 85, 1)
    if pos == "WR":
        return round(55 + quality * 55, 1)
    if pos == "TE":
        return round(25 + quality * 35, 1)
    return 0.0


def estimate_td_rate(pos: str, rank: float) -> float:
    quality = max(0, 1 - (rank / 220))

    if pos == "QB":
        return round(1.3 + quality * 1.3, 2)
    if pos == "RB":
        return round(0.45 + quality * 0.85, 2)
    if pos == "WR":
        return round(0.25 + quality * 0.55, 2)
    if pos == "TE":
        return round(0.18 + quality * 0.38, 2)
    return 0.0


def estimate_receptions(pos: str, rank: float) -> float:
    quality = max(0, 1 - (rank / 220))

    if pos == "RB":
        return round(1.0 + quality * 2.0, 1)
    if pos == "WR":
        return round(3.5 + quality * 3.5, 1)
    if pos == "TE":
        return round(2.0 + quality * 3.0, 1)
    return 0.0


def context_score(rank: float, base: float = 70) -> float:
    return round(max(45, min(95, base + (80 - rank) * 0.18)), 1)


def load_sources() -> pd.DataFrame:
    files = sorted(SOURCE_DIR.glob("*.csv"))

    if not files:
        raise FileNotFoundError(f"No source files found in {SOURCE_DIR}")

    frames = []
    for file in files:
        df = pd.read_csv(file)
        if df.empty:
            print(f"Skipping empty source: {file}")
            continue
        frames.append(df)

    if not frames:
        raise ValueError("All source files were empty")

    df = pd.concat(frames, ignore_index=True)

    required = {"player_name", "position", "college", "source_rank", "source"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing source columns: {sorted(missing)}")

    df["position"] = df["position"].astype(str).str.upper().str.strip()
    df = df[df["position"].isin(FANTASY_POS)].copy()
    df["source_rank"] = pd.to_numeric(df["source_rank"], errors="coerce")
    df = df.dropna(subset=["player_name", "position", "source_rank"])

    if df.empty:
        raise ValueError("No valid fantasy-position source rows")

    return df


def build_inputs() -> pd.DataFrame:
    raw = load_sources()

    raw["name_key"] = raw["player_name"].astype(str).str.lower().str.replace(r"[^a-z0-9 ]", "", regex=True).str.strip()

    grouped = (
        raw.groupby(["name_key", "position"], as_index=False)
        .agg(
            player_name=("player_name", "first"),
            college=("college", "first"),
            consensus_rank=("source_rank", "mean"),
            best_rank=("source_rank", "min"),
            source_count=("source", "nunique"),
            sources=("source", lambda x: ", ".join(sorted(set(map(str, x))))),
        )
    )

    rows = []

    for _, r in grouped.iterrows():
        pos = r["position"]
        rank = float(r["consensus_rank"])
        rd = projected_round(rank)
        pick = projected_pick(rank)

        rows.append({
            "player_name": r["player_name"],
            "draft_year": ACTIVE_CLASS_YEAR,
            "position": pos,
            "nfl_team": None,
            "college": r["college"],
            "final_college_season": 2025,
            "declare_class": "unknown",
            "draft_round": rd,
            "draft_pick": pick,
            "college_games": 12,
            "college_yards_per_game": estimate_college_yards_per_game(pos, rank),
            "college_td_rate": estimate_td_rate(pos, rank),
            "college_receptions_per_game": estimate_receptions(pos, rank),
            "offensive_line_score": context_score(rank, 68),
            "scheme_fit_score": context_score(rank, 72),
            "opportunity_score": context_score(rank, 72),
            "fantasy_role": default_role(pos, rank),
            "risk_notes": f"Consensus from {int(r['source_count'])} source(s): {r['sources']}. Estimated production priors pending stat enrichment.",
            "upside_notes": f"Consensus rank {rank:.1f}; best source rank {float(r['best_rank']):.1f}.",
        })

    out = pd.DataFrame(rows).sort_values(["draft_round", "draft_pick", "player_name"])
    return out


def main() -> None:
    out = build_inputs()

    if out.empty:
        raise ValueError("Refusing to write empty 2026 prospect input file")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_FILE, index=False)

    print(f"✅ Wrote {len(out)} 2026 prospect input rows to {OUTPUT_FILE}")
    print(out.head(25).to_string(index=False))


if __name__ == "__main__":
    main()
