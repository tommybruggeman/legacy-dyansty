from __future__ import annotations

from pathlib import Path
import pandas as pd

from auth import service_client


ACTIVE_CLASS_YEAR = 2026
OUTPUT_DIR = Path("data/prospects")
OUTPUT_FILE = OUTPUT_DIR / f"prospect_inputs_{ACTIVE_CLASS_YEAR}.csv"

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}


def norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace(".", "").replace("'", "").split())


def load_all_sleeper_players(sb) -> list[dict]:
    all_rows = []
    page_size = 1000
    start = 0

    while True:
        rows = (
            sb.table("sleeper_players")
            .select("*")
            .range(start, start + page_size - 1)
            .execute()
            .data or []
        )

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        start += page_size

    return all_rows


def looks_like_prospect(row: dict) -> bool:
    pos = row.get("position")
    if pos not in FANTASY_POSITIONS:
        return False

    full_name = row.get("full_name")
    if not full_name:
        return False

    status = str(row.get("status") or "").lower()
    team = row.get("team")
    sleeper_id = str(row.get("sleeper_player_id") or "")

    # Sleeper prospect/new-player IDs currently live in the 13000+ range.
    # This is not perfect, but it is a much better automated first pass than
    # treating every active fantasy-position player as a rookie.
    try:
        sid = int(sleeper_id)
    except Exception:
        sid = 0

    if sid < 13000:
        return False

    # Most established NFL players have an NFL team and should not be treated
    # as incoming rookie prospects. Keep only very new player IDs.
    if team and sid < 13200:
        return False

    if status not in {"active", "prospect", ""}:
        return False

    return True


def projected_round(row: dict) -> int:
    pos = row.get("position")

    if pos == "QB":
        return 1
    if pos in {"RB", "WR"}:
        return 2
    if pos == "TE":
        return 3

    return 7


def default_role(position: str) -> str:
    if position == "QB":
        return "superflex quarterback prospect"
    if position == "RB":
        return "rookie running back prospect"
    if position == "WR":
        return "rookie wide receiver prospect"
    if position == "TE":
        return "rookie tight end prospect"
    return "prospect"


def build_rows() -> list[dict]:
    sb = service_client()
    players = load_all_sleeper_players(sb)

    rows = []

    for p in players:
        if not looks_like_prospect(p):
            continue

        pos = p.get("position")
        name = p.get("full_name")
        team = p.get("team")

        rows.append({
            "player_name": name,
            "draft_year": ACTIVE_CLASS_YEAR,
            "position": pos,
            "nfl_team": team,
            "college": None,
            "final_college_season": ACTIVE_CLASS_YEAR - 1,
            "declare_class": "unknown",
            "college_games": 0,
            "college_yards_per_game": 0,
            "college_td_rate": 0,
            "college_receptions_per_game": 0,
            "draft_round": projected_round(p),
            "draft_pick": 0,
            "offensive_line_score": 70,
            "scheme_fit_score": 70,
            "opportunity_score": 70,
            "fantasy_role": default_role(pos),
            "risk_notes": "Auto-detected from Sleeper prospect ingestion; needs enrichment.",
            "upside_notes": "Included by automated prospect ingestion from Sleeper fantasy-position pool.",
        })

    # Avoid obvious junk/duplicates
    df = pd.DataFrame(rows)
    if df.empty:
        return []

    df["name_key"] = df["player_name"].apply(norm)
    df = df.drop_duplicates(subset=["draft_year", "name_key", "position"], keep="first")
    df = df.drop(columns=["name_key"])

    return df.to_dict("records")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = build_rows()
    df = pd.DataFrame(rows)

    df.to_csv(OUTPUT_FILE, index=False)

    print(f"✅ Ingested {len(df)} automated prospects")
    print(f"✅ Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
