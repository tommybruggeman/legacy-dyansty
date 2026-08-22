from __future__ import annotations

from pathlib import Path
import pandas as pd


ACTIVE_CLASS_YEAR = 2026

RAW_SOURCE_DIR = Path("data/prospects/discovery_sources")
OUTPUT_DIR = Path("data/prospects")
OUTPUT_FILE = OUTPUT_DIR / f"prospect_inputs_{ACTIVE_CLASS_YEAR}.csv"

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}


def norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace(".", "").replace("'", "").split())


def load_real_source_boards() -> pd.DataFrame:
    files = [p for p in sorted(RAW_SOURCE_DIR.glob("*.csv")) if p.is_file()]

    if not files:
        raise FileNotFoundError(
            "\n❌ No real prospect source boards found.\n"
            f"Add source CSVs to: {RAW_SOURCE_DIR}\n"
            "Required columns: player_name, position, college, source_rank, source\n"
        )

    frames = []
    for file in files:
        df = pd.read_csv(file)

        if df.empty:
            print(f"⚠️ Skipping empty source file: {file}")
            continue

        df["source_file"] = file.name
        frames.append(df)

    if not frames:
        raise ValueError(
            "\n❌ Source files exist, but all are empty.\n"
            "Add real rows before running discovery.\n"
        )

    return pd.concat(frames, ignore_index=True)


def build_consensus_board(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"player_name", "position", "college", "source_rank", "source"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = raw.copy()
    df["player_name"] = df["player_name"].astype(str).str.strip()
    df["name_key"] = df["player_name"].apply(norm)
    df["position"] = df["position"].astype(str).str.upper().str.strip()
    df["source_rank"] = pd.to_numeric(df["source_rank"], errors="coerce")

    df = df[
        df["name_key"].ne("")
        & df["position"].isin(FANTASY_POSITIONS)
        & df["source_rank"].notna()
    ].copy()

    if df.empty:
        raise ValueError("\n❌ No valid fantasy-position prospects found in sources.\n")

    grouped = (
        df.groupby(["name_key", "position"], as_index=False)
        .agg(
            player_name=("player_name", "first"),
            college=("college", "first"),
            consensus_rank=("source_rank", "mean"),
            best_rank=("source_rank", "min"),
            worst_rank=("source_rank", "max"),
            source_count=("source", "nunique"),
            sources=("source", lambda x: ", ".join(sorted(set(map(str, x))))),
        )
    )

    grouped = grouped.sort_values(["consensus_rank", "source_count"], ascending=[True, False])
    return grouped


def projected_round(consensus_rank: float) -> int:
    if consensus_rank <= 32:
        return 1
    if consensus_rank <= 64:
        return 2
    if consensus_rank <= 100:
        return 3
    if consensus_rank <= 140:
        return 4
    if consensus_rank <= 180:
        return 5
    return 6


def write_prospect_inputs(board: pd.DataFrame) -> None:
    if board.empty:
        raise ValueError("Refusing to write empty prospect input file.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for _, r in board.iterrows():
        rows.append({
            "player_name": r["player_name"],
            "draft_year": ACTIVE_CLASS_YEAR,
            "position": r["position"],
            "nfl_team": None,
            "college": r["college"],
            "final_college_season": ACTIVE_CLASS_YEAR - 1,
            "declare_class": "unknown",
            "college_games": 0,
            "college_yards_per_game": 0,
            "college_td_rate": 0,
            "college_receptions_per_game": 0,
            "draft_round": projected_round(float(r["consensus_rank"])),
            "draft_pick": 0,
            "offensive_line_score": 70,
            "scheme_fit_score": 70,
            "opportunity_score": 70,
            "fantasy_role": f"consensus {ACTIVE_CLASS_YEAR} {r['position']} prospect",
            "risk_notes": f"Discovered from {int(r['source_count'])} source(s): {r['sources']}",
            "upside_notes": f"Consensus rank {float(r['consensus_rank']):.1f}; best rank {float(r['best_rank']):.1f}.",
        })

    out = pd.DataFrame(rows)
    out.to_csv(OUTPUT_FILE, index=False)

    print(f"✅ Discovered {len(out)} prospects from real source boards")
    print(f"✅ Wrote {OUTPUT_FILE}")


def main() -> None:
    raw = load_real_source_boards()
    board = build_consensus_board(raw)
    write_prospect_inputs(board)


if __name__ == "__main__":
    main()
