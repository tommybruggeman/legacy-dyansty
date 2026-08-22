from __future__ import annotations

from pathlib import Path
import sys
import pandas as pd


OUT_DIR = Path("data/prospects/discovery_sources")
REQUIRED_COLUMNS = ["player_name", "position", "college", "source_rank", "source"]


def normalize_source_csv(input_path: str, source_name: str) -> Path:
    p = Path(input_path)

    if not p.exists():
        raise FileNotFoundError(p)

    df = pd.read_csv(p)

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}\n"
            f"Required: {REQUIRED_COLUMNS}"
        )

    df = df[REQUIRED_COLUMNS].copy()
    df["source"] = source_name

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    out = OUT_DIR / f"{source_name}.csv"
    df.to_csv(out, index=False)

    print(f"✅ Collected {len(df)} rows from {source_name}")
    print(f"✅ Wrote {out}")

    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage:\n"
            "  PYTHONPATH=. python3 snapshot/builders/prospect/collect_prospect_source.py "
            "<input_csv> <source_name>"
        )

    normalize_source_csv(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
