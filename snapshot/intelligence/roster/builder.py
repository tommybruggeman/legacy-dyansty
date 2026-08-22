from __future__ import annotations

from auth import service_client
from snapshot.intelligence.decisions.gm_player_decision_engine import evaluate_player
from snapshot.intelligence.player_profile.player_intelligence_profile import build_player_intelligence_profile
from snapshot.intelligence.player_validation.player_validation_engine import validate_player_context


def _key(name: str | None) -> str:
    return str(name or "").strip().lower()


def _num(value, default=None):
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _first_by_player(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for r in rows:
        name = _key(r.get("player_name"))
        if name and name not in out:
            out[name] = r
    return out


def _load_table(table: str, owner_team_name: str | None = None, owner_col: str | None = None, limit: int = 5000) -> list[dict]:
    sb = service_client()

    try:
        q = sb.table(table).select("*").limit(limit)

        if owner_team_name and owner_col:
            q = q.eq(owner_col, owner_team_name)

        return q.execute().data or []
    except Exception as e:
        print(f"WARNING loading {table}: {e}")
        return []


def _build_player_from_maps(player_name: str, maps: dict, owner_team_name: str) -> dict:
    k = _key(player_name)

    universe = maps["player_universe"].get(k, {})
    identity = maps["player_identity_context"].get(k, {})
    rookie = maps["rookie_draft_board"].get(k, {})
    asset = maps["roster_asset_values"].get(k, {})
    rec = maps["player_recommendations"].get(k, {})
    tasks = maps["source_tasks"].get(k, [])

    evidence_confidence = 100
    open_tasks = [t for t in tasks if t.get("status") == "open"]
    if open_tasks:
        evidence_confidence -= min(60, len(open_tasks) * 7)
    if [t for t in open_tasks if int(t.get("priority") or 99) == 1]:
        evidence_confidence -= 15
    evidence_confidence = max(10, evidence_confidence)

    identity_obj = {
        "name": universe.get("player_name") or identity.get("player_name") or rookie.get("player_name") or player_name,
        "pos": universe.get("pos") or identity.get("pos") or rookie.get("pos"),
        "nfl_team": universe.get("nfl_team") or identity.get("nfl_team") or rookie.get("nfl_team"),
        "sleeper_id": universe.get("sleeper_id") or identity.get("sleeper_id") or rookie.get("sleeper_id"),
        "gsis_id": universe.get("gsis_id") or rookie.get("gsis_id"),
        "age": identity.get("age"),
        "years_exp": universe.get("years_exp") or identity.get("years_exp"),
        "current_owner": universe.get("current_owner") or asset.get("owner_team_name") or rec.get("owner_team_name"),
    }

    production = {
        "expected_ppg": _num(universe.get("expected_ppg")),
        "historical_ppg": _num(universe.get("historical_ppg")),
        "season_ppg": _num(universe.get("season_ppg")),
        "season_games": _num(universe.get("season_games")),
        "production_trend_score": _num(identity.get("production_trend_score")),
        "historical_context_score": _num(identity.get("historical_context_score")),
    }

    contract = {
        "salary": _num(universe.get("salary") if universe.get("salary") is not None else asset.get("salary") if asset.get("salary") is not None else rec.get("salary")),
        "years": _num(universe.get("years") if universe.get("years") is not None else asset.get("years") if asset.get("years") is not None else rec.get("years")),
        "has_contract": universe.get("has_contract"),
        "contract_score": _num(identity.get("contract_score")),
        "contract_efficiency_score": _num(universe.get("contract_efficiency_score")),
        "contract_efficiency_grade": universe.get("contract_efficiency_grade"),
        "contract_cost_score": _num(asset.get("contract_cost_score")),
        "contract_value_score": _num(asset.get("contract_value_score")),
        "contract_risk_score": _num(asset.get("contract_risk_score")),
        "term_risk_score": _num(asset.get("term_risk_score")),
    }

    dynasty = {
        "dynasty_asset_score": _num(asset.get("dynasty_asset_score") if asset.get("dynasty_asset_score") is not None else rec.get("dynasty_asset_score") if rec.get("dynasty_asset_score") is not None else universe.get("dynasty_asset_score")),
        "asset_value_score": _num(asset.get("asset_value_score") if asset.get("asset_value_score") is not None else rec.get("asset_value_score")),
        "engine_player_score": _num(asset.get("engine_player_score") if asset.get("engine_player_score") is not None else rec.get("engine_player_score")),
        "future_projection_score": _num(universe.get("future_projection_score")),
        "dynasty_window_score": _num(asset.get("dynasty_window_score") if asset.get("dynasty_window_score") is not None else rec.get("dynasty_window_score")),
        "dynasty_risk_score": _num(asset.get("dynasty_risk_score") if asset.get("dynasty_risk_score") is not None else rec.get("dynasty_risk_score")),
        "upside_score": _num(asset.get("upside_score")),
        "floor_score": _num(asset.get("floor_score")),
        "cornerstone_flag": asset.get("cornerstone_flag"),
        "sell_high_flag": asset.get("sell_high_flag"),
        "buy_low_flag": asset.get("buy_low_flag"),
        "win_now_flag": asset.get("win_now_flag"),
        "rebuild_flag": asset.get("rebuild_flag"),
        "rookie_rank": rookie.get("rookie_rank"),
        "rookie_tier": rookie.get("tier"),
        "final_rookie_score": _num(rookie.get("final_rookie_score")),
    }

    situation = {
        "role_score": _num(identity.get("role_score")),
        "situation_score": _num(identity.get("situation_score")),
        "opportunity_score": _num(identity.get("opportunity_score")),
        "nfl_intelligence_score": _num(universe.get("nfl_intelligence_score")),
        "nfl_intelligence_grade": universe.get("nfl_intelligence_grade"),
        "nfl_intelligence_flags": universe.get("nfl_intelligence_flags") or [],
    }

    opinion = {
        "recommendation": rec.get("recommendation") or asset.get("asset_recommendation"),
        "asset_recommendation": asset.get("asset_recommendation"),
        "confidence": _num(rec.get("confidence")),
        "reasoning": rec.get("reasoning"),
        "career_stage": rec.get("career_stage") or asset.get("career_stage"),
        "engine_tier": asset.get("engine_tier"),
    }

    missing_fields = []
    for t in open_tasks:
        missing_fields.extend(t.get("needs") or [])

    evidence = {
        "confidence": evidence_confidence,
        "open_task_count": len(open_tasks),
        "missing_fields": sorted(set(missing_fields)),
        "source_tasks": tasks,
    }

    overall_score = (
        dynasty.get("engine_player_score")
        or dynasty.get("dynasty_asset_score")
        or dynasty.get("final_rookie_score")
        or 0
    )

    summary = (
        f"{identity_obj.get('name')} ({identity_obj.get('pos')}, {identity_obj.get('nfl_team')}) — "
        f"salary ${contract.get('salary')}, years {contract.get('years')}, "
        f"PPG {production.get('season_ppg') or production.get('expected_ppg') or production.get('historical_ppg')}, "
        f"asset {dynasty.get('dynasty_asset_score') or dynasty.get('asset_value_score') or dynasty.get('final_rookie_score')}, "
        f"recommendation {opinion.get('recommendation') or 'NO_RECOMMENDATION'}, "
        f"evidence confidence {evidence_confidence}."
    )

    return {
        "player_name": identity_obj.get("name"),
        "identity": identity_obj,
        "production": production,
        "contract": contract,
        "dynasty": dynasty,
        "situation": situation,
        "opinion": opinion,
        "evidence": evidence,
        "overall_score": overall_score,
        "summary": summary,
    }



def _move_decision(player: dict) -> dict:
    """
    Explainable GM pressure score.

    Higher score = more willing to shop/move.
    Lower score = protect/hold.
    """
    identity = player.get("identity", {}) or {}
    production = player.get("production", {}) or {}
    contract = player.get("contract", {}) or {}
    dynasty = player.get("dynasty", {}) or {}
    opinion = player.get("opinion", {}) or {}

    name = identity.get("player_name") or "Unknown"
    pos = str(identity.get("pos") or "").upper()

    salary = contract.get("salary") or 0
    years = contract.get("years") or 0
    asset = production.get("asset_score") or 0
    ppg = production.get("season_ppg") or 0
    age = dynasty.get("age")
    contract_risk = contract.get("contract_risk_score") or 0
    efficiency = contract.get("contract_efficiency_score") or 50

    rec = (
        opinion.get("recommendation")
        or opinion.get("asset_recommendation")
        or "NO_RECOMMENDATION"
    )

    score = 30
    reasons = []

    # Recommendation intelligence
    if rec == "CORE HOLD":
        score -= 45
        reasons.append("core hold recommendation protects him from move pressure")
    elif rec == "BUY LOW / HOLD":
        score -= 32
        reasons.append("buy low / hold recommendation strongly lowers move pressure")
    elif rec in {"DEVELOPMENT HOLD", "PROSPECT HOLD"}:
        score -= 24
        reasons.append(f"{rec.lower()} recommendation lowers move pressure")
    elif rec == "DEPTH HOLD":
        score -= 14
        reasons.append("depth hold recommendation lowers move pressure")
    elif rec in {"CHURN / REPLACE", "REPLACE", "CUT"}:
        score += 18
        reasons.append("churn / replace recommendation raises roster spot pressure")
    elif rec in {"SHOP CONTRACT", "SHOP / RESTRUCTURE"}:
        score += 25
        reasons.append("contract shop recommendation raises move pressure")
    elif rec in {"SELL", "SELL HIGH"}:
        score += 30
        reasons.append("sell recommendation raises move pressure")
    elif rec == "NO_RECOMMENDATION":
        score += 5
        reasons.append("no clear hold recommendation creates mild review pressure")

    # Contract pressure
    if salary >= 35:
        score += 10
        reasons.append("premium salary creates cap pressure")
    elif salary >= 20:
        score += 12
        reasons.append("meaningful salary creates contract review pressure")
    elif salary <= 5:
        score -= 6
        reasons.append("cheap contract lowers urgency to move")

    if years >= 3 and salary >= 15:
        score += 5
        reasons.append("multi-year salary commitment adds risk")

    if contract_risk >= 70:
        score += 10
        reasons.append("high contract risk")
    elif contract_risk >= 50:
        score += 5
        reasons.append("moderate contract risk")

    if efficiency <= 25 and salary >= 8:
        score += 14
        reasons.append("weak contract efficiency")
    elif efficiency <= 40 and salary >= 8:
        score += 7
        reasons.append("below-average contract efficiency")
    elif efficiency >= 75:
        score -= 10
        reasons.append("strong contract efficiency lowers move pressure")

    # Asset / production protection
    if asset >= 70:
        score -= 24
        reasons.append("premium asset value protects him")
    elif asset >= 55:
        score -= 18
        reasons.append("solid asset value lowers move pressure")
    elif asset >= 50:
        score -= 10
        reasons.append("usable asset value lowers move pressure")
    elif asset <= 40:
        score += 8
        reasons.append("lower asset value makes him more expendable")

    if ppg >= 18:
        score -= 16
        reasons.append("high weekly production protects him")
    elif ppg >= 12:
        score -= 10
        reasons.append("usable weekly production lowers move pressure")
    elif ppg <= 5 and salary >= 8:
        score += 8
        reasons.append("low production raises move pressure")

    # Dynasty window protection
    if dynasty.get("cornerstone_flag"):
        score -= 35
        reasons.append("cornerstone flag overrides normal move pressure")

    if isinstance(age, (int, float)):
        if age <= 25 and asset >= 50:
            score -= 10
            reasons.append("young asset profile lowers move pressure")
        elif age >= 29 and salary >= 10:
            score += 8
            reasons.append("older paid player carries age/value risk")

    # Cheap players are not true trade candidates; they are churn decisions.
    if salary <= 2 and rec in {"NO_RECOMMENDATION", "CHURN / REPLACE", "REPLACE", "CUT"}:
        score = min(score, 48)
        reasons.append("cheap fringe player is a churn decision, not a trade priority")

    # Strong hold language should cap pressure unless the player is truly distressed.
    if rec == "BUY LOW / HOLD" and asset >= 50:
        score = min(score, 44)
        reasons.append("buy-low asset should be monitored, not actively shopped")

    if rec in {"DEVELOPMENT HOLD", "PROSPECT HOLD"} and salary <= 15:
        score = min(score, 38)
        reasons.append("development asset should not be moved just for contract cleanup")

    # Position nuance
    if pos == "QB" and asset >= 55:
        score -= 8
        reasons.append("superflex QB value lowers move pressure")

    score = max(0, min(100, round(score, 2)))

    if score >= 75:
        tier = "SELL / ACTIVELY SHOP"
    elif score >= 60:
        tier = "SHOP"
    elif score >= 45:
        tier = "MONITOR / PRICE CHECK"
    elif score >= 25:
        tier = "HOLD"
    else:
        tier = "CORE / PROTECT"

    return {
        "move_score": score,
        "move_tier": tier,
        "move_reasons": reasons[:4],
    }

def _avg(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


def _top(players, key, reverse=True, limit=5):
    return sorted(
        players,
        key=lambda p: key(p) if key(p) is not None else -999,
        reverse=reverse,
    )[:limit]


def build_roster_intelligence(owner_team_name: str) -> dict:
    roster_rows = _load_table("player_universe", owner_team_name, "current_owner", limit=100)

    player_names = [r.get("player_name") for r in roster_rows if r.get("player_name")]

    all_universe = roster_rows
    all_identity = _load_table("player_identity_context", limit=5000)
    all_rookies = _load_table("rookie_draft_board", limit=5000)
    all_assets = _load_table("roster_asset_values", owner_team_name, "owner_team_name", limit=500)
    all_recs = _load_table("player_recommendations", owner_team_name, "owner_team_name", limit=500)
    all_tasks = _load_table("legacy_source_task_queue", limit=5000)

    task_map = {}
    for t in all_tasks:
        name = _key(t.get("player_name"))
        if name:
            task_map.setdefault(name, []).append(t)

    maps = {
        "player_universe": _first_by_player(all_universe),
        "player_identity_context": _first_by_player(all_identity),
        "rookie_draft_board": _first_by_player(all_rookies),
        "roster_asset_values": _first_by_player(all_assets),
        "player_recommendations": _first_by_player(all_recs),
        "source_tasks": task_map,
    }

    players = [
        _build_player_from_maps(name, maps, owner_team_name)
        for name in player_names
    ]

    roster_context = {
        "owner": owner_team_name,
        "roster_size": len(players),
    }

    for player in players:
        player["validation"] = validate_player_context(player)
        player["intelligence_profile"] = build_player_intelligence_profile(player)
        player["gm_decision"] = evaluate_player(player, roster_context)
        player["move_decision"] = {
            "move_score": player["gm_decision"]["move_pressure"],
            "move_tier": player["gm_decision"]["decision"],
            "move_reasons": player["gm_decision"]["reasons"],
        }

    strengths = _top(players, lambda p: p["overall_score"], limit=8)

    move_candidates = _top(
        players,
        lambda p: p.get("move_decision", {}).get("move_score", 0),
        limit=8,
    )

    core = [
        p for p in players
        if p["dynasty"].get("cornerstone_flag")
        or p["opinion"].get("recommendation") == "CORE HOLD"
    ]

    qbs = [p for p in players if str(p["identity"].get("pos")).upper() == "QB"]
    rbs = [p for p in players if str(p["identity"].get("pos")).upper() == "RB"]
    wrs = [p for p in players if str(p["identity"].get("pos")).upper() == "WR"]
    tes = [p for p in players if str(p["identity"].get("pos")).upper() == "TE"]

    confidence = _avg([p["evidence"]["confidence"] for p in players])

    return {
        "owner": owner_team_name,
        "confidence": confidence,
        "players": players,
        "core_players": core,
        "strength_players": strengths,
        "move_candidates": move_candidates,
        "position_groups": {
            "QB": qbs,
            "RB": rbs,
            "WR": wrs,
            "TE": tes,
        },
        "summary": {
            "roster_size": len(players),
            "core_count": len(core),
            "qb_count": len(qbs),
            "rb_count": len(rbs),
            "wr_count": len(wrs),
            "te_count": len(tes),
            "average_evidence_confidence": confidence,
        },
    }


if __name__ == "__main__":
    roster = build_roster_intelligence("Tommy Bruggeman")

    print("\nSUMMARY")
    print(roster["summary"])

    print("\nCORE")
    for p in roster["core_players"]:
        print("-", p["summary"])

    print("\nMOVE CANDIDATES")
    for p in roster["move_candidates"][:8]:
        d = p.get("gm_decision", {})
        print("-", p["summary"])
        print(f"  Decision: {d.get('decision')} | Pressure: {d.get('move_pressure')} | Stance: {d.get('stance')}")
        print(f"  Action: {d.get('action')}")

    print("\nBEST PLAYERS")
    for p in roster["strength_players"][:8]:
        print("-", p["summary"])
