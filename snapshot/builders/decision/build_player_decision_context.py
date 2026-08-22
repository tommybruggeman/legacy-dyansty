from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client


TARGET_TABLE = "player_decision_context"


def num(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def text(v, default="") -> str:
    return str(v or default).strip()


def merge_by_sleeper(base_rows, extra_rows):
    by_id = {str(r.get("sleeper_id")): r for r in extra_rows if r.get("sleeper_id")}
    merged = []
    for r in base_rows:
        x = dict(r)
        sid = str(r.get("sleeper_id") or "")
        extra = by_id.get(sid) or {}
        for k, v in extra.items():
            if k not in x or x.get(k) in (None, "", 0, 0.0):
                x[k] = v
            else:
                x[f"dev_{k}"] = v
        merged.append(x)
    return merged


def infer_career_arc(row: dict) -> tuple[float, str]:
    pos = text(row.get("pos") or row.get("position"))
    age = num(row.get("age") or row.get("dev_age"))
    decline = num(row.get("decline_probability"))
    retirement = num(row.get("retirement_probability"))
    dev_score = num(row.get("development_score"))

    if age:
        if pos == "RB":
            if age <= 23:
                label, base = "ASCENDING", 88
            elif age <= 26:
                label, base = "PRIME", 78
            elif age <= 28:
                label, base = "LATE_PRIME", 55
            else:
                label, base = "DECLINING", 30
        elif pos in {"WR", "TE"}:
            if age <= 24:
                label, base = "ASCENDING", 88
            elif age <= 28:
                label, base = "PRIME", 80
            elif age <= 30:
                label, base = "LATE_PRIME", 58
            else:
                label, base = "DECLINING", 32
        elif pos == "QB":
            if age <= 25:
                label, base = "ASCENDING", 86
            elif age <= 32:
                label, base = "PRIME", 82
            elif age <= 36:
                label, base = "LATE_PRIME", 62
            else:
                label, base = "DECLINING", 38
        else:
            label, base = "STABLE", 55
    else:
        existing = text(row.get("trajectory") or row.get("career_trajectory") or row.get("career_arc"))
        label = existing or "STABLE"
        base = dev_score or {"ASCENDING": 80, "PRIME": 75, "STABLE": 60, "DECLINING": 35}.get(label.upper(), 55)

    if decline >= 0.35:
        base -= 15
        if label in {"ASCENDING", "PRIME"}:
            label = "RISK_ADJUSTED_" + label

    if retirement >= 0.25:
        base -= 18
        label = "RETIREMENT_RISK"

    return clamp(base), label

def infer_role_score(row: dict) -> tuple[float, str]:
    role = text(
        row.get("team_role")
        or row.get("role")
        or row.get("depth_role")
        or row.get("opportunity_role")
    )

    if role:
        upper = role.upper()
        if "ELITE" in upper or "ANCHOR" in upper:
            return 95.0, role
        if "START" in upper or "LEAD" in upper or "WR1" in upper or "RB1" in upper:
            return 82.0, role
        if "FLEX" in upper or "ROTATION" in upper or "COMMITTEE" in upper:
            return 58.0, role
        if "BENCH" in upper or "DEPTH" in upper:
            return 30.0, role

    ppg = num(row.get("expected_ppg") or row.get("season_ppg") or row.get("projected_ppg"))
    if ppg >= 18:
        return 90.0, "ELITE_WEEKLY_STARTER"
    if ppg >= 14:
        return 78.0, "STARTER"
    if ppg >= 10:
        return 58.0, "FLEX_OR_SPOT_START"
    if ppg >= 6:
        return 35.0, "DEPTH"
    return 20.0, "FRINGE"


def decision_tier(score: float) -> str:
    if score >= 82:
        return "BUILD_AROUND"
    if score >= 68:
        return "STRONG_HOLD"
    if score >= 55:
        return "USEFUL_BUT_PRICE_SENSITIVE"
    if score >= 42:
        return "MARKET_CHECK"
    return "CHURN_OR_SELL"


def build_summary(row: dict, scores: dict) -> str:
    name = row.get("player_name")
    pos = row.get("pos")
    salary = num(row.get("salary"))
    years = num(row.get("years"))
    return (
        f"{name} ({pos}) | ${salary:.0f}/{years:.0f} yrs | "
        f"win-now {scores['win_now_score']:.1f}, long-term {scores['long_term_build_score']:.1f}, "
        f"contract decision {scores['contract_decision_score']:.1f}, sell risk {scores['sell_risk_score']:.1f}."
    )


def score_player(row: dict) -> dict:
    ppg = num(row.get("expected_ppg") or row.get("projected_ppg") or row.get("season_ppg"))
    season_ppg = num(row.get("season_ppg") or ppg)
    salary = num(row.get("salary"))
    years = num(row.get("years"))

    contract = num(
        row.get("contract_efficiency_score")
        or row.get("contract_score")
        or row.get("contract_value_score")
        or row.get("contract_roi_score")
    )

    dynasty = num(row.get("dynasty_asset_score") or row.get("dynasty_score") or row.get("asset_score"))
    trade_value = num(row.get("trade_value_score") or dynasty)
    situation = num(row.get("situation_score") or row.get("player_situation_score"))
    risk = num(row.get("risk_score") or row.get("injury_risk_score") or row.get("situation_risk_score"))

    career_arc, career_label = infer_career_arc(row)
    role_score, role_label = infer_role_score(row)

    past_production = clamp(season_ppg * 4)
    current_production = clamp(ppg * 4.2)

    future_projection = clamp(
        (dynasty * 0.35)
        + (career_arc * 0.25)
        + (situation * 0.20)
        + (role_score * 0.20)
    )

    win_now = clamp(
        (current_production * 0.45)
        + (role_score * 0.25)
        + (situation * 0.15)
        + (contract * 0.10)
        - (risk * 0.10)
    )

    long_term = clamp(
        (future_projection * 0.35)
        + (dynasty * 0.30)
        + (career_arc * 0.20)
        + (contract * 0.10)
        - (risk * 0.10)
    )

    rebuild_core = clamp(
        (long_term * 0.55)
        + (dynasty * 0.25)
        + (career_arc * 0.15)
        - (salary * 0.35)
        - (years * 1.5)
    )

    weekly_start = clamp(
        (current_production * 0.55)
        + (role_score * 0.25)
        + (situation * 0.15)
        - (risk * 0.10)
    )

    contract_decision = clamp(
        (win_now * 0.30)
        + (long_term * 0.30)
        + (contract * 0.25)
        - (salary * 0.45)
        - (years * 2.0)
    )

    sell_risk = clamp(
        (100 - contract_decision) * 0.45
        + (100 - career_arc) * 0.25
        + (risk * 0.20)
        + (salary * 0.30)
    )

    hold_score = clamp(
        (long_term * 0.35)
        + (win_now * 0.25)
        + (contract_decision * 0.25)
        + (trade_value * 0.15)
        - (risk * 0.10)
    )

    scores = {
        "past_production_score": round(past_production, 2),
        "current_production_score": round(current_production, 2),
        "future_projection_score": round(future_projection, 2),
        "role_score": round(role_score, 2),
        "situation_score": round(situation, 2),
        "career_arc_score": round(career_arc, 2),
        "contract_score": round(contract, 2),
        "dynasty_score": round(dynasty, 2),
        "trade_value_score": round(trade_value, 2),
        "risk_score": round(risk, 2),
        "win_now_score": round(win_now, 2),
        "long_term_build_score": round(long_term, 2),
        "rebuild_core_score": round(rebuild_core, 2),
        "weekly_start_score": round(weekly_start, 2),
        "contract_decision_score": round(contract_decision, 2),
        "sell_risk_score": round(sell_risk, 2),
        "hold_score": round(hold_score, 2),
    }

    out = {
        "sleeper_id": row.get("sleeper_id"),
        "player_name": row.get("player_name") or row.get("name"),
        "pos": row.get("pos") or row.get("position"),
        "current_owner": row.get("current_owner"),
        "nfl_team": row.get("nfl_team") or row.get("team"),
        "salary": salary,
        "years": years,
        "career_trajectory": career_label,
        "team_role": role_label,
        "decision_tier": decision_tier(hold_score),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **scores,
    }

    out["decision_summary"] = build_summary(out, scores)

    return out


def build_player_decision_context():
    sb = service_client()

    universe = (
        sb.table("player_universe")
        .select("*")
        .limit(2500)
        .execute()
        .data
        or []
    )

    situation = (
        sb.table("player_situation_context")
        .select("*")
        .limit(2500)
        .execute()
        .data
        or []
    )

    development = (
        sb.table("player_development_features")
        .select("*")
        .limit(2500)
        .execute()
        .data
        or []
    )

    rows = merge_by_sleeper(universe, situation)
    rows = merge_by_sleeper(rows, development)

    output = []
    for r in rows:
        name = r.get("player_name") or r.get("name")
        if not name:
            continue

        market = str(r.get("market_pool") or "").upper()
        if r.get("current_owner") is None and market not in {"FA", "FREE_AGENT", "WAIVERS", "FA_AUCTION"}:
            continue

        output.append(score_player(r))

    if output:
        sb.table(TARGET_TABLE).upsert(
            output,
            on_conflict="sleeper_id,current_owner",
        ).execute()

    print(f"✅ Upserted {len(output)} player_decision_context rows")


if __name__ == "__main__":
    build_player_decision_context()
