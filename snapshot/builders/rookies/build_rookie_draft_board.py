from auth import service_client
from snapshot.intelligence.platform.player_dossier import build_player_dossier
import hashlib
import math

TARGET_TABLE = "rookie_draft_board"
ACTIVE_ROOKIE_YEAR = 2026


POS_VALUE = {
    "QB": 86,
    "RB": 82,
    "WR": 81,
    "TE": 64,
}


def _stable_jitter(name: str, scale: float = 4.0) -> float:
    """
    Deterministic tiny variance so default rows do not collapse into ties.
    This is not meant to fake scouting; it prevents unusable flat boards.
    """
    h = hashlib.md5((name or "").encode()).hexdigest()
    n = int(h[:8], 16) / 0xFFFFFFFF
    return round((n - 0.5) * scale, 2)


def _has_team(team):
    return bool(team and str(team).strip() not in ["", "-", "None", "FA"])


def _source_score(source):
    source = source or ""
    if "manual" in source:
        return 95
    if "consensus" in source:
        return 90
    if "rookie" in source:
        return 75
    if "computed_from_player_universe" in source:
        return 45
    return 50


def _infer_source_rank(row, fallback_index):
    for key in ["rookie_rank", "dynasty_rank", "position_rank", "rank"]:
        val = row.get(key)
        if val not in [None, "", 0]:
            try:
                return float(val)
            except Exception:
                pass
    return float(fallback_index)


def _rank_to_grade(rank, total):
    """
    Converts rank into prospect grade.
    Rank 1 becomes elite, mid board becomes playable, deep rows drop.
    """
    if not rank or rank <= 0:
        return 50

    percentile = 1 - ((rank - 1) / max(total - 1, 1))
    grade = 42 + percentile * 48
    return round(max(35, min(92, grade)), 2)


def _future_from_prospect(prospect, pos, age=None):
    pos_bonus = {
        "QB": 2,
        "WR": 1.5,
        "RB": -1,
        "TE": 0,
    }.get(pos, 0)

    return round(max(30, min(92, prospect * 0.88 + 8 + pos_bonus)), 2)


def _team_fit(row):
    team = row.get("nfl_team") or row.get("team")
    pos = row.get("pos")

    if not _has_team(team):
        return 20

    base = {
        "QB": 58,
        "RB": 55,
        "WR": 57,
        "TE": 50,
    }.get(pos, 50)

    return round(base + _stable_jitter((row.get("player_name") or "") + str(team), 5), 2)


def _final_score(row, total, fallback_index):
    name = row.get("player_name") or ""
    pos = row.get("pos") or "-"
    team = row.get("nfl_team") or row.get("team")
    source = row.get("source")

    source_rank = _infer_source_rank(row, fallback_index)
    prospect = _rank_to_grade(source_rank, total)

    # If an existing prospect score has real variance, blend it.
    existing_prospect = row.get("prospect_score")
    try:
        existing_prospect = float(existing_prospect)
    except Exception:
        existing_prospect = None

    if existing_prospect and existing_prospect > 0:
        prospect = round(existing_prospect * 0.55 + prospect * 0.45, 2)

    prospect = round(prospect + _stable_jitter(name, 3), 2)

    future = _future_from_prospect(prospect, pos)
    pos_value = POS_VALUE.get(pos, 50)
    fit = _team_fit(row)
    src = _source_score(source)

    score = (
        prospect * 0.48
        + future * 0.24
        + fit * 0.12
        + pos_value * 0.08
        + src * 0.08
    )

    if not _has_team(team):
        score -= 9

    return {
        "prospect_score": round(prospect, 2),
        "future_score": round(future, 2),
        "positional_value_score": round(pos_value, 2),
        "team_need_fit_score": round(fit, 2),
        "final_rookie_score": round(score, 2),
    }


def build_rookie_draft_board(rookie_year=ACTIVE_ROOKIE_YEAR):
    sb = service_client()

    source_rows = (
        sb.table(TARGET_TABLE)
        .select("*")
        .eq("rookie_class_year", rookie_year)
        .execute()
        .data or []
    )

    if not source_rows:
        print(f"No existing rookie rows found for {rookie_year}. Nothing to rebuild.")
        return []

    total = len(source_rows)
    rebuilt = []

    for i, row in enumerate(source_rows, start=1):
        scores = _final_score(row, total, i)

        rebuilt_row = {
            "id": row.get("id"),
            "sleeper_id": row.get("sleeper_id"),
            "gsis_id": row.get("gsis_id"),
            "player_name": row.get("player_name"),
            "pos": row.get("pos"),
            "nfl_team": row.get("nfl_team") or row.get("team"),
            "rookie_class_year": rookie_year,
            "source": row.get("source") or "computed_from_player_universe",
            "notes": row.get("notes"),
            **scores,
        }

        rebuilt.append(rebuilt_row)

    rebuilt = sorted(
        rebuilt,
        key=lambda r: (
            r["final_rookie_score"],
            r["prospect_score"],
            1 if _has_team(r.get("nfl_team")) else 0,
        ),
        reverse=True,
    )

    for rank, row in enumerate(rebuilt, start=1):
        row["rookie_rank"] = rank

        if rank <= 12:
            row["tier"] = 1
        elif rank <= 30:
            row["tier"] = 2
        elif rank <= 60:
            row["tier"] = 3
        elif rank <= 120:
            row["tier"] = 4
        else:
            row["tier"] = 5

    sb.table(TARGET_TABLE).upsert(rebuilt, on_conflict="id").execute()

    print(f"Rebuilt {len(rebuilt)} rookie_draft_board rows for {rookie_year}")
    return rebuilt


if __name__ == "__main__":
    build_rookie_draft_board()
