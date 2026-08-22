from __future__ import annotations

from pathlib import Path
import re
import sys
import pandas as pd


OUT_DIR = Path("data/prospects/discovery_sources")
RAW_DIR = Path("data/prospects/raw_sources")


FANTASY_POS = {"QB", "RB", "WR", "TE"}


def parse_line(line: str, source_name: str) -> dict | None:
    line = line.strip()
    if not line:
        return None

    # Supports:
    # 1. Fernando Mendoza QB Indiana
    # 1 Fernando Mendoza QB Indiana
    # Fernando Mendoza, QB, Indiana
    line = re.sub(r"^\s*\d+[\.\)]?\s+", "", line)

    if "," in line:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            name = parts[0]
            pos = parts[1].upper()
            college = parts[2] if len(parts) >= 3 else None
            return name, pos, college

    tokens = line.split()
    pos_idx = None
    for i, tok in enumerate(tokens):
        if tok.upper() in FANTASY_POS:
            pos_idx = i
            break

    if pos_idx is None or pos_idx == 0:
        return None

    name = " ".join(tokens[:pos_idx])
    pos = tokens[pos_idx].upper()
    college = " ".join(tokens[pos_idx + 1:]) or None

    return name, pos, college


def collect(input_txt: str, source_name: str) -> None:
    p = Path(input_txt)
    if not p.exists():
        raise FileNotFoundError(p)

    rows = []
    rank = 1

    for line in p.read_text().splitlines():
        parsed = parse_line(line, source_name)
        if not parsed:
            continue

        name, pos, college = parsed
        if pos not in FANTASY_POS:
            continue

        rows.append({
            "player_name": name,
            "position": pos,
            "college": college,
            "source_rank": rank,
            "source": source_name,
        })
        rank += 1

    if not rows:
        raise ValueError("No prospect rows parsed from pasted board.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{source_name}.csv"

    pd.DataFrame(rows).to_csv(out, index=False)

    print(f"✅ Parsed {len(rows)} prospects from pasted board")
    print(f"✅ Wrote {out}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage:\n"
            "  PYTHONPATH=. python3 snapshot/builders/prospect/sources/collect_pasted_board.py "
            "<raw_text_file> <source_name>"
        )

    collect(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
