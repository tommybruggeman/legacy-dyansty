from __future__ import annotations

from datetime import datetime, timezone
from auth import service_client


TARGET_TABLE = "player_brain_context"

def fetch_all(sb, table: str, batch_size: int = 1000):
    rows = []
    start = 0

    while True:
        batch = (
            sb.table(table)
            .select("*")
            .range(start, start + batch_size - 1)
            .execute()
            .data
            or []
        )

        rows.extend(batch)

        if len(batch) < batch_size:
            break

        start += batch_size

    return rows


def num(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def build_summary(r):
    return (
        f"{r.get('player_name')} ({r.get('pos')}) | "
        f"owner={r.get('current_owner') or 'FA'}, "
        f"${num(r.get('salary')):.0f}/{num(r.get('years')):.0f} yrs, "
        f"past={num(r.get('past_score')):.1f}, present={num(r.get('present_score')):.1f}, "
        f"future={num(r.get('future_score')):.1f}, situation={num(r.get('situation_score')):.1f}, "
        f"contract={num(r.get('contract_score')):.1f}, brain={num(r.get('brain_score')):.1f}."
    )


def merge(base, extras, prefix):
    by_id = {str(x.get("sleeper_id")): x for x in extras if x.get("sleeper_id")}
    out = []

    for r in base:
        row = dict(r)
        sid = str(row.get("sleeper_id") or "")
        extra = by_id.get(sid) or {}

        for k, v in extra.items():
            if k in {"sleeper_id", "player_name", "pos"}:
                continue
            row[f"{prefix}_{k}"] = v

        out.append(row)

    return out


def build_player_brain_context():
    sb = service_client()

    universe = sb.table("player_universe").select("*").limit(2500).execute().data or []

    situation = sb.table("player_situation_context").select("*").limit(2500).execute().data or []

    development = sb.table("player_development_features").select("*").limit(2500).execute().data or []

    decision = sb.table("player_decision_context").select("*").limit(2500).execute().data or []

    rows = merge(universe, situation, "situation")
    rows = merge(rows, development, "development")
    rows = merge(rows, decision, "decision")

    output = []

    inactive_statuses = {"retired", "inactive", "historical"}
    historical_names = {
        "Tom Brady", "Drew Brees", "Matt Ryan", "Steve Smith", "Tiki Barber",
        "Calvin Johnson", "Arian Foster", "Anquan Boldin", "Larry Fitzgerald",
        "Brandon Marshall", "Wes Welker", "Jordy Nelson", "Demaryius Thomas",
    }

    seen = set()

    for r in rows:
        name = r.get("player_name")
        if not name:
            continue

        status = str(r.get("nfl_status") or "").lower()
        if status in inactive_statuses:
            continue

        if name in historical_names:
            continue

        owner = r.get("current_owner")
        market = str(r.get("market_pool") or "").upper()

        # Keep rostered players and real FA/waiver-market players only.
        if not owner and market not in {"FA", "FREE_AGENT", "WAIVERS", "FA_AUCTION"}:
            continue

        dedupe_key = (str(r.get("sleeper_id") or name), owner or "FA")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        salary = num(r.get("salary"))
        years = num(r.get("years"))

        past = clamp(
            num(r.get("historical_ppg")) * 4
            or num(r.get("season_ppg")) * 4
            or num(r.get("development_current_ppg")) * 4
        )

        present = clamp(
            num(r.get("expected_ppg")) * 4.2
            or num(r.get("season_ppg")) * 4
            or num(r.get("development_current_ppg")) * 4
        )

        future = clamp(
            num(r.get("future_projection_score"))
            or num(r.get("decision_future_projection_score"))
            or num(r.get("development_development_score"))
            or num(r.get("dynasty_asset_score"))
        )

        situation_score = clamp(
            num(r.get("situation_situation_score"))
            or num(r.get("decision_situation_score"))
            or num(r.get("nfl_intelligence_score"))
        )

        role_score = clamp(
            num(r.get("decision_role_score"))
            or num(r.get("situation_role_security_score"))
        )

        contract_score = clamp(
            num(r.get("contract_efficiency_score"))
            or num(r.get("decision_contract_score"))
        )

        dynasty = clamp(num(r.get("dynasty_asset_score")) or num(r.get("decision_dynasty_score")))
        market = clamp(num(r.get("market_consensus_score")) or num(r.get("decision_trade_value_score")))
        risk = clamp(
            num(r.get("situation_situation_risk_score"))
            or num(r.get("decision_risk_score"))
            or num(r.get("development_decline_probability")) * 100
        )

        age_curve = clamp(num(r.get("decision_career_arc_score")) or num(r.get("development_development_score")))

        brain_score = clamp(
            past * 0.10
            + present * 0.20
            + future * 0.22
            + situation_score * 0.12
            + role_score * 0.10
            + contract_score * 0.12
            + dynasty * 0.10
            + market * 0.07
            + age_curve * 0.10
            - risk * 0.13
            - salary * 0.12
            - years * 0.8
        )

        context = {
            "sleeper_id": r.get("sleeper_id"),
            "player_name": name,
            "search_name": r.get("search_name"),
            "pos": r.get("pos"),
            "nfl_team": r.get("nfl_team"),
            "current_owner": r.get("current_owner"),
            "market_pool": r.get("market_pool"),
            "salary": salary,
            "years": years,

            "past_score": round(past, 2),
            "present_score": round(present, 2),
            "future_score": round(future, 2),
            "situation_score": round(situation_score, 2),
            "role_score": round(role_score, 2),
            "contract_score": round(contract_score, 2),
            "dynasty_score": round(dynasty, 2),
            "market_score": round(market, 2),
            "risk_score": round(risk, 2),
            "age_curve_score": round(age_curve, 2),
            "brain_score": round(brain_score, 2),

            "career_trajectory": r.get("decision_career_trajectory") or r.get("development_trajectory"),
            "team_role": r.get("decision_team_role") or r.get("development_role_archetype"),
            "production_tier": r.get("development_production_tier"),
            "situation_grade": r.get("situation_situation_grade"),
            "situation_note": r.get("situation_situation_note"),
            "decision_tier": r.get("decision_decision_tier"),
            "decision_summary": r.get("decision_decision_summary"),

            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        context["brain_summary"] = build_summary(context)
        output.append(context)

    if output:
        sb.table(TARGET_TABLE).upsert(
            output,
            on_conflict="sleeper_id,current_owner",
        ).execute()

    print(f"✅ Upserted {len(output)} player_brain_context rows")


if __name__ == "__main__":
    build_player_brain_context()
