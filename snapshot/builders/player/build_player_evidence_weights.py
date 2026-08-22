from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client


TARGET_TABLE = "player_evidence_weights"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _weights(profile):
    if profile == "ROOKIE_PROSPECT":
        return {
            "nfl_output_weight": 0.05,
            "college_prestige_weight": 0.30,
            "draft_capital_weight": 0.25,
            "future_projection_weight": 0.20,
            "nfl_context_weight": 0.15,
            "market_weight": 0.03,
            "risk_weight": 0.02,
        }

    if profile == "EARLY_CAREER":
        return {
            "nfl_output_weight": 0.35,
            "college_prestige_weight": 0.10,
            "draft_capital_weight": 0.10,
            "future_projection_weight": 0.25,
            "nfl_context_weight": 0.12,
            "market_weight": 0.05,
            "risk_weight": 0.03,
        }

    if profile == "ESTABLISHED_NFL":
        return {
            "nfl_output_weight": 0.55,
            "college_prestige_weight": 0.00,
            "draft_capital_weight": 0.00,
            "future_projection_weight": 0.20,
            "nfl_context_weight": 0.12,
            "market_weight": 0.08,
            "risk_weight": 0.05,
        }

    if profile == "AGING_VETERAN":
        return {
            "nfl_output_weight": 0.45,
            "college_prestige_weight": 0.00,
            "draft_capital_weight": 0.00,
            "future_projection_weight": 0.12,
            "nfl_context_weight": 0.15,
            "market_weight": 0.08,
            "risk_weight": 0.20,
        }

    return {
        "nfl_output_weight": 0.40,
        "college_prestige_weight": 0.05,
        "draft_capital_weight": 0.05,
        "future_projection_weight": 0.25,
        "nfl_context_weight": 0.15,
        "market_weight": 0.07,
        "risk_weight": 0.03,
    }


def _profile(career_year, rookie_asset_score, career_stage, age):
    cy = _num(career_year)
    rookie = _num(rookie_asset_score)
    stage = str(career_stage or "").lower()
    age = _num(age)

    if rookie >= 55 or cy <= 1 or "rookie" in stage:
        return "ROOKIE_PROSPECT"
    if cy <= 3:
        return "EARLY_CAREER"
    if age >= 29 or cy >= 8:
        return "AGING_VETERAN"
    return "ESTABLISHED_NFL"


def build_player_evidence_weights():
    sb = service_client()

    dev = sb.table("player_development_features").select("*").execute().data or []
    dynasty = sb.table("player_dynasty_asset_engine").select("*").execute().data or []

    dynasty_by_id = {str(r.get("sleeper_id")): r for r in dynasty}

    # Include dynasty-only players too.
    all_ids = set(str(r.get("sleeper_id")) for r in dev) | set(str(r.get("sleeper_id")) for r in dynasty)

    dev_by_id = {str(r.get("sleeper_id")): r for r in dev}

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for sid in all_ids:
        d = dev_by_id.get(sid, {})
        dy = dynasty_by_id.get(sid, {})

        player = d.get("player_name") or dy.get("player_name")
        pos = d.get("pos") or dy.get("pos")
        career_year = _num(d.get("career_year"))
        age = _num(d.get("age"))
        career_stage = d.get("production_tier") or d.get("role_archetype") or d.get("archetype")
        rookie_asset_score = _num(dy.get("rookie_asset_score"))

        profile = _profile(career_year, rookie_asset_score, career_stage, age)
        w = _weights(profile)

        if profile == "ROOKIE_PROSPECT":
            summary = f"{player}: weigh rookie evidence heavily because NFL output is limited. Draft/college/future context should matter more than historical NFL production."
        elif profile == "ESTABLISHED_NFL":
            summary = f"{player}: weigh NFL production heavily because there is enough pro evidence. College/draft prestige should barely matter now."
        elif profile == "AGING_VETERAN":
            summary = f"{player}: weigh NFL production and decline risk heavily. Prior pedigree matters very little."
        else:
            summary = f"{player}: blend early NFL output with projection and original prospect signal."

        rows.append({
            "sleeper_id": sid,
            "player_name": player,
            "pos": pos,
            "career_stage": career_stage,
            "career_year": career_year,
            "rookie_asset_score": rookie_asset_score,
            **w,
            "evidence_profile": profile,
            "evidence_summary": summary,
            "updated_at": now,
        })

    if rows:
        sb.table(TARGET_TABLE).upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    build_player_evidence_weights()
