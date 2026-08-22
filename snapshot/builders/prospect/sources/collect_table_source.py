from __future__ import annotations

from pathlib import Path
import sys
import pandas as pd


OUT_DIR = Path("data/prospects/discovery_sources")


def collect(input_file: str, source_name: str) -> None:
    p = Path(input_file)
    if not p.exists():
        raise FileNotFoundError(p)

    if p.suffix.lower() in {".html", ".htm"}:
        tables = pd.read_html(p)
        if not tables:
            raise ValueError("No tables found in HTML file")
        df = tables[0]
    else:
        df = pd.read_csv(p)

    print("Detected columns:")
    print(list(df.columns))

    # Flexible column mapping
    cols = {str(c).lower().strip(): c for c in df.columns}

    def find(*names):
        for name in names:
            if name in cols:
                return cols[name]
        return None

    rank_col = find("rank", "rk", "source_rank", "#")
    name_col = find("player", "player_name", "name")
    pos_col = find("pos", "position")
    college_col = find("college", "school", "team")

    missing = []
    if rank_col is None: missing.append("rank")
    if name_col is None: missing.append("player_name")
    if pos_col is None: missing.append("position")

    if missing:
        raise ValueError(f"Could not map required columns: {missing}")

    out = pd.DataFrame({
        "player_name": df[name_col],
        "position": df[pos_col],
        "college": df[college_col] if college_col else None,
        "source_rank": pd.to_numeric(df[rank_col], errors="coerce"),
        "source": source_name,
    })

    out = out.dropna(subset=["player_name", "position", "source_rank"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{source_name}.csv"
    out.to_csv(dest, index=False)

    print(f"✅ Collected {len(out)} rows")
    print(f"✅ Wrote {dest}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage:\n"
            "  PYTHONPATH=. python3 snapshot/builders/prospect/sources/collect_table_source.py "
            "<input_csv_or_html> <source_name>"
        )

    collect(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
