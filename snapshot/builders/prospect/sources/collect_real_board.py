from __future__ import annotations

from pathlib import Path
import sys
import pandas as pd


OUT_DIR = Path("data/prospects/discovery_sources")
REQUIRED = ["player_name", "position", "college", "source_rank", "source"]


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: PYTHONPATH=. python3 snapshot/builders/prospect/sources/collect_real_board.py <csv_path> <source_name>"
        )

    src = Path(sys.argv[1])
    source_name = sys.argv[2]

    if not src.exists():
        raise FileNotFoundError(src)

    df = pd.read_csv(src)

    missing = set(REQUIRED) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}. Required: {REQUIRED}")

    df = df[REQUIRED].copy()
    df["source"] = source_name
    df["position"] = df["position"].astype(str).str.upper().str.strip()
    df["source_rank"] = pd.to_numeric(df["source_rank"], errors="coerce")
    df = df.dropna(subset=["player_name", "position", "source_rank"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{source_name}.csv"
    df.to_csv(out, index=False)

    print(f"✅ Collected {len(df)} real source rows")
    print(f"✅ Wrote {out}")


if __name__ == "__main__":
    main()
