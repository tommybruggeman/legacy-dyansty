from auth import service_client

TARGET_TABLE = "player_projection_context"


def _num(v, default=0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _position_baseline(pos):
    return {
        "QB": 220,
        "RB": 135,
        "WR": 145,
        "TE": 95,
    }.get(pos, 80)


def _rookie_start_path(pos, prospect_score, situation_score):
    prospect_score = _num(prospect_score, 50)
    situation_score = _num(situation_score, 50)

    base = (prospect_score * 0.55) + (situation_score * 0.45)

    y1 = max(2, min(85, base - 45))
    y2 = max(5, min(90, base - 25))
    y3 = max(8, min(92, base - 12))

    return round(y1, 2), round(y2, 2), round(y3, 2)


def _project_points(pos, baseline, start_probability, year_offset):
    growth = {
        1: 0.75,
        2: 0.95,
        3: 1.05,
    }.get(year_offset, 1.0)

    return round(baseline * growth * (start_probability / 100), 2)


def build_player_projection_context(season=None):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    rookies = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("rookie_class_year", season)
        .execute()
        .data or []
    )

    rows = []

    for r in rookies:
        pos = r.get("pos")
        baseline = _position_baseline(pos)

        prospect = r.get("prospect_score") or r.get("final_rookie_score") or 50
        situation = r.get("team_need_fit_score") or 50

        y1_start, y2_start, y3_start = _rookie_start_path(pos, prospect, situation)

        y1_points = _project_points(pos, baseline, y1_start, 1)
        y2_points = _project_points(pos, baseline, y2_start, 2)
        y3_points = _project_points(pos, baseline, y3_start, 3)

        rows.append({
            "sleeper_id": r.get("sleeper_id"),
            "gsis_id": r.get("gsis_id"),
            "player_name": r.get("player_name"),
            "pos": pos,
            "nfl_team": r.get("nfl_team"),
            "season": season,
            "projection_type": "rookie_v1",
            "year_1_projected_points": y1_points,
            "year_2_projected_points": y2_points,
            "year_3_projected_points": y3_points,
            "year_1_start_probability": y1_start,
            "year_2_start_probability": y2_start,
            "year_3_start_probability": y3_start,
            "projection_confidence": 45,
            "projection_summary": (
                f"Projection v1 based on prospect score {round(_num(prospect),2)}, "
                f"situation score {round(_num(situation),2)}, and position baseline."
            ),
        })

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="player_name,season,projection_type",
        ).execute()

    print(f"Upserted {len(rows)} player_projection_context rows.")
    return rows


if __name__ == "__main__":
    build_player_projection_context()
