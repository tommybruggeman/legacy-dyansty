from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client


TARGET_TABLE = "player_nfl_context"


def _risk_from_status(nfl_status, nfl_active, injury_status, depth):
    status = str(nfl_status or "").lower()
    injury = str(injury_status or "").lower()
    depth_val = float(depth or 99)

    risk = 0
    flags = []

    if not nfl_active:
        risk += 30
        flags.append("not_active")

    if status and status not in {"active", "none", "nan"}:
        risk += 25
        flags.append(f"status:{nfl_status}")

    if injury and injury not in {"none", "nan", "null"}:
        risk += 20
        flags.append(f"injury:{injury_status}")

    if depth_val >= 3:
        risk += 15
        flags.append("depth_chart_pressure")

    if risk >= 60:
        grade = "HIGH_RISK"
    elif risk >= 35:
        grade = "MEDIUM_RISK"
    elif risk >= 15:
        grade = "LOW_RISK"
    else:
        grade = "STABLE"

    score = max(0, 100 - risk)

    return score, grade, flags


def build_player_nfl_context():
    sb = service_client()

    situation_rows = sb.table("player_situation_context").select("*").execute().data or []
    print(f"Loaded situation rows: {len(situation_rows)}")

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for r in situation_rows:
        score, grade, flags = _risk_from_status(
            r.get("nfl_status"),
            r.get("nfl_active"),
            r.get("injury_status"),
            r.get("depth_chart_order"),
        )

        flags_text = ", ".join(flags) if flags else "no major NFL context flags"

        note = (
            f"NFL context: team={r.get('nfl_team')}, "
            f"status={r.get('nfl_status')}, active={r.get('nfl_active')}, "
            f"injury={r.get('injury_status')}, depth={r.get('depth_chart_order')}. "
            f"Risk flags: {flags_text}."
        )

        rows.append({
            "sleeper_id": r.get("sleeper_id"),
            "owner_team_name": r.get("owner_team_name"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),
            "nfl_team": r.get("nfl_team"),
            "nfl_status": r.get("nfl_status"),
            "nfl_active": r.get("nfl_active"),
            "injury_status": r.get("injury_status"),
            "depth_chart_order": r.get("depth_chart_order"),
            "nfl_context_score": round(score, 2),
            "nfl_context_risk_grade": grade,
            "nfl_context_flags": flags,
            "nfl_context_note": note,
            "updated_at": now,
        })

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    build_player_nfl_context()
