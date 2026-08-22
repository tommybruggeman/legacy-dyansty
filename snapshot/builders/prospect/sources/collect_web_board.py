from __future__ import annotations

from pathlib import Path
import sys
import pandas as pd


OUT_DIR = Path("data/prospects/discovery_sources")
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}


def find_col(df, names):
    lookup = {str(c).lower().strip(): c for c in df.columns}
    for name in names:
        if name in lookup:
            return lookup[name]
    return None


def collect(url: str, source_name: str) -> None:
    tables = pd.read_html(url)

    best = None
    best_score = -1

    for df in tables:
        rank_col = find_col(df, ["rank", "rk", "#"])
        name_col = find_col(df, ["player", "player_name", "name"])
        pos_col = find_col(df, ["pos", "position"])
        college_col = find_col(df, ["college", "school", "team"])

        score = sum(c is not None for c in [rank_col, name_col, pos_col])
        if score > best_score:
            best = (df, rank_col, name_col, pos_col, college_col)
            best_score = score

    if not best or best_score < 3:
        raise ValueError("Could not find a usable prospect table with rank/player/position columns")

    df, rank_col, name_col, pos_col, college_col = best

    out = pd.DataFrame({
        "player_name": df[name_col],
        "position": df[pos_col],
        "college": df[college_col] if college_col else None,
        "source_rank": pd.to_numeric(df[rank_col], errors="coerce"),
        "source": source_name,
    })

    out["position"] = out["position"].astype(str).str.upper().str.strip()
    out = out[out["position"].isin(FANTASY_POSITIONS)].copy()
    out = out.dropna(subset=["player_name", "source_rank"])

    if out.empty:
        raise ValueError("Parsed table but found zero fantasy-position prospects")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{source_name}.csv"
    out.to_csv(dest, index=False)

    print(f"✅ Collected {len(out)} prospects from {source_name}")
    print(f"✅ Wrote {dest}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: PYTHONPATH=. python3 snapshot/builders/prospect/sources/collect_web_board.py <url> <source_name>"
        )

    collect(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
