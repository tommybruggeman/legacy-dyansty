from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client


TARGET_TABLE = "player_nfl_intelligence"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _text(v):
    return str(v or "").strip()


def _grade(score):
    if score >= 80:
        return "ELITE_CONTEXT"
    if score >= 65:
        return "GOOD_CONTEXT"
    if score >= 50:
        return "NEUTRAL_CONTEXT"
    if score >= 35:
        return "RISKY_CONTEXT"
    return "BAD_CONTEXT"


def _availability_score(active, status, injury_status, injury_notes):
    status_l = _text(status).lower()
    injury_l = _text(injury_status).lower()
    notes_l = _text(injury_notes).lower()

    score = 100
    flags = []

    if active is False:
        score -= 35
        flags.append("not_active")

    if status_l and status_l not in {"active", "none", "null", "nan"}:
        score -= 20
        flags.append(f"status:{status}")

    if injury_l and injury_l not in {"none", "null", "nan"}:
        score -= 25
        flags.append(f"injury:{injury_status}")

    if any(x in notes_l for x in ["surgery", "acl", "mcl", "achilles", "pup", "ir"]):
        score -= 20
        flags.append("major_injury_note")

    return max(0, min(100, score)), flags


def _depth_risk(depth):
    d = _num(depth, 99)
    if d <= 1:
        return 5, []
    if d == 2:
        return 25, ["depth_competition"]
    if d == 3:
        return 45, ["depth_pressure"]
    return 70, ["buried_depth_chart"]


def _role_stability(role_security_score, depth_risk, availability):
    role = _num(role_security_score, 50)
    return max(0, min(100, role * 0.55 + availability * 0.30 + (100 - depth_risk) * 0.15))


def _opportunity_score(situation_score, role_stability, depth_risk):
    sit = _num(situation_score, 50)
    return max(0, min(100, sit * 0.45 + role_stability * 0.40 + (100 - depth_risk) * 0.15))


def build_player_nfl_intelligence():
    sb = service_client()

    situation = sb.table("player_situation_context").select("*").execute().data or []
    nfl_rows = sb.table("player_nfl_context").select("*").execute().data or []

    nfl_by_id = {str(r.get("sleeper_id")): r for r in nfl_rows}

    print(f"Loaded situation rows: {len(situation)}")
    print(f"Loaded player_nfl_context rows: {len(nfl_rows)}")

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for s in situation:
        sleeper_id = str(s.get("sleeper_id"))
        n = nfl_by_id.get(sleeper_id, {})

        status = n.get("status") or s.get("nfl_status")
        active = n.get("active")
        if active is None:
            active = s.get("nfl_active")

        injury_status = n.get("injury_status") or s.get("injury_status")
        injury_notes = n.get("injury_notes")
        depth = n.get("depth_chart_order") or s.get("depth_chart_order")
        nfl_team = n.get("nfl_team") or n.get("team") or s.get("nfl_team")

        availability, availability_flags = _availability_score(
            active,
            status,
            injury_status,
            injury_notes,
        )

        depth_risk, depth_flags = _depth_risk(depth)

        role_stability = _role_stability(
            s.get("role_security_score"),
            depth_risk,
            availability,
        )

        opportunity = _opportunity_score(
            s.get("situation_score"),
            role_stability,
            depth_risk,
        )

        injury_risk = max(0, 100 - availability)

        situation_score = _num(s.get("situation_score"), 50)
        situation_risk = _num(s.get("situation_risk_score"), 50)

        nfl_score = (
            availability * 0.30
            + role_stability * 0.25
            + opportunity * 0.25
            + situation_score * 0.10
            + (100 - situation_risk) * 0.10
        )

        flags = availability_flags + depth_flags

        grade = _grade(nfl_score)

        player_name = s.get("player_name") or n.get("player_name") or n.get("full_name")
        pos = s.get("pos") or n.get("pos") or n.get("position")

        if flags:
            flag_text = ", ".join(flags)
        else:
            flag_text = "no major NFL context flags"

        summary = (
            f"{player_name} NFL context: {grade}, score {nfl_score:.1f}. "
            f"Team {nfl_team}, status {status}, active={active}, injury={injury_status}, "
            f"injury notes={injury_notes}, depth={depth}. Flags: {flag_text}."
        )

        rows.append({
            "sleeper_id": sleeper_id,
            "owner_team_name": s.get("owner_team_name"),
            "player_name": player_name,
            "pos": pos,
            "nfl_team": nfl_team,
            "nfl_status": status,
            "active": active,
            "injury_status": injury_status,
            "injury_notes": injury_notes,
            "depth_chart_order": depth,
            "situation_grade": s.get("situation_grade"),
            "situation_score": round(situation_score, 2),
            "situation_risk_score": round(situation_risk, 2),
            "role_security_score": round(_num(s.get("role_security_score"), 50), 2),
            "opportunity_score": round(opportunity, 2),
            "availability_score": round(availability, 2),
            "role_stability_score": round(role_stability, 2),
            "injury_risk_score": round(injury_risk, 2),
            "depth_chart_risk_score": round(depth_risk, 2),
            "nfl_intelligence_score": round(nfl_score, 2),
            "nfl_intelligence_grade": grade,
            "nfl_intelligence_flags": flags,
            "nfl_intelligence_summary": summary,
            "updated_at": now,
        })

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    build_player_nfl_intelligence()
