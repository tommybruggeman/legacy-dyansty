from __future__ import annotations

from pathlib import Path
import re
import sys
import pandas as pd
import requests
from bs4 import BeautifulSoup


OUT_DIR = Path("data/prospects/discovery_sources")
FANTASY_POS = {"QB", "RB", "WR", "TE"}


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def parse_candidate(text: str):
    text = clean(text)

    # Examples:
    # 1. Fernando Mendoza, QB, Indiana
    # 1 Fernando Mendoza QB Indiana
    m = re.match(r"^(\d+)[\.\)]?\s+(.+)$", text)
    if not m:
        return None

    rank = int(m.group(1))
    rest = m.group(2)

    if "," in rest:
        parts = [clean(p) for p in rest.split(",")]
        if len(parts) >= 2:
            name = parts[0]
            pos = parts[1].upper()
            college = parts[2] if len(parts) >= 3 else None
            if pos in FANTASY_POS:
                return name, pos, college, rank

    tokens = rest.split()
    for i, tok in enumerate(tokens):
        if tok.upper() in FANTASY_POS and i > 0:
            name = " ".join(tokens[:i])
            pos = tok.upper()
            college = " ".join(tokens[i + 1:]) or None
            return name, pos, college, rank

    return None


def collect(url: str, source_name: str):
    html = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
    soup = BeautifulSoup(html, "html.parser")

    candidates = []

    for tag in soup.find_all(["p", "li", "tr", "h2", "h3", "div"]):
        text = clean(tag.get_text(" ", strip=True))
        parsed = parse_candidate(text)
        if parsed:
            candidates.append(parsed)

    rows = []
    seen = set()

    for name, pos, college, rank in candidates:
        key = (name.lower(), pos)
        if key in seen:
            continue
        seen.add(key)

        rows.append({
            "player_name": name,
            "position": pos,
            "college": college,
            "source_rank": rank,
            "source": source_name,
        })

    if not rows:
        raise ValueError("No ranked fantasy-position prospects parsed from article/page.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{source_name}.csv"
    pd.DataFrame(rows).sort_values("source_rank").to_csv(out, index=False)

    print(f"✅ Parsed {len(rows)} prospects from {source_name}")
    print(f"✅ Wrote {out}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: PYTHONPATH=. python3 snapshot/builders/prospect/sources/collect_ranked_article_board.py <url> <source_name>"
        )

    collect(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
