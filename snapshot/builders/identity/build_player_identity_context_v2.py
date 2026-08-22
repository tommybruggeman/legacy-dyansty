from __future__ import annotations

from typing import Any, Dict, List

from auth import service_client
from snapshot.builders.rookies.rookie_year import get_active_rookie_class_year


ACTIVE_ROOKIE_CLASS_YEAR = get_active_rookie_class_year()


def norm_name(name: Any) -> str:
    return " ".join(str(name or "").lower().replace(".", "").replace("'", "").split())


def key(row: Dict[str, Any]) -> str | None:
    sid = row.get("sleeper_id")
    if sid:
        return f"sid::{sid}"

    name = row.get("player_name") or row.get("normalized_name") or row.get("search_name")
    pos = row.get("pos") or row.get("position")

    if name and pos:
        return f"name::{norm_name(name)}::{pos}"

    return None


def alt_key(row: Dict[str, Any]) -> str | None:
    name = row.get("player_name") or row.get("normalized_name") or row.get("search_name")
    pos = row.get("pos") or row.get("position")
    if name and pos:
        return f"name::{norm_name(name)}::{pos}"
    return None


def load_table(table: str, select: str = "*") -> List[Dict[str, Any]]:
    sb = service_client()
    try:
        return sb.table(table).select(select).execute().data or []
    except Exception as e:
        print(f"⚠️ Skipping {table}: {e}")
        return []


def index_by_key(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in rows:
        for k in {key(r), alt_key(r)}:
            if k:
                out[k] = r
    return out


def n(*vals, default=0):
    for v in vals:
        try:
            if v is not None and v != "":
                return float(v)
        except Exception:
            pass
    return default


def i(*vals):
    val = n(*vals, default=0)
    return int(val) if val else None


def text(*vals):
    for v in vals:
        if v not in [None, ""]:
            return v
    return None


def age_stage(age, pos):
    if not age:
        return "UNKNOWN"
    age = float(age)

    if pos == "RB":
        if age <= 23: return "ASCENDING"
        if age <= 26: return "PRIME"
        if age <= 28: return "LATE_PRIME"
        return "DECLINE_RISK"

    if pos == "WR":
        if age <= 24: return "ASCENDING"
        if age <= 29: return "PRIME"
        if age <= 31: return "LATE_PRIME"
        return "DECLINE_RISK"

    if pos == "TE":
        if age <= 25: return "ASCENDING"
        if age <= 30: return "PRIME"
        if age <= 32: return "LATE_PRIME"
        return "DECLINE_RISK"

    if pos == "QB":
        if age <= 25: return "ASCENDING"
        if age <= 34: return "PRIME"
        if age <= 37: return "LATE_PRIME"
        return "DECLINE_RISK"

    return "UNKNOWN"


def age_score(stage):
    return {
        "ASCENDING": 80,
        "PRIME": 90,
        "LATE_PRIME": 65,
        "DECLINE_RISK": 35,
        "UNKNOWN": 50,
    }.get(stage, 50)


def build_player_identity_context_v2():
    sb = service_client()

    universe = load_table("player_universe")
    master_identity = index_by_key(load_table("player_master_identity"))
    prospects = index_by_key(load_table("player_prospect_context"))
    graph = index_by_key(load_table("prospect_graph"))
    situation = index_by_key(load_table("player_situation_context"))
    role = index_by_key(load_table("player_role_context"))
    future = index_by_key(load_table("player_future_projection"))
    market = index_by_key(load_table("player_market_pool"))
    dynasty = index_by_key(load_table("player_dynasty_context"))
    kg = index_by_key(load_table("player_knowledge_graph"))
    engine = index_by_key(load_table("player_engine_snapshot"))

    rows = []

    for p in universe:
        k = key(p)
        if not k:
            continue

        ak = alt_key(p)

        mi = master_identity.get(k, {}) or master_identity.get(ak, {})
        pr = prospects.get(k, {}) or prospects.get(ak, {})
        pg = graph.get(k, {}) or graph.get(ak, {})
        sit = situation.get(k, {}) or situation.get(ak, {})
        ro = role.get(k, {}) or role.get(ak, {})
        fu = future.get(k, {}) or future.get(ak, {})
        ma = market.get(k, {}) or market.get(ak, {})
        dy = dynasty.get(k, {}) or dynasty.get(ak, {})
        know = kg.get(k, {}) or kg.get(ak, {})
        eng = engine.get(k, {}) or engine.get(ak, {})

        sleeper_id = p.get("sleeper_id")
        if not sleeper_id:
            continue

        pos = text(mi.get("pos"), p.get("pos"), pr.get("position"), pg.get("pos"), sit.get("pos"), ro.get("pos"), fu.get("pos"), ma.get("pos"), dy.get("pos"), know.get("pos"), eng.get("pos"))

        draft_year = i(mi.get("draft_year"), p.get("draft_year"), pr.get("draft_year"), pg.get("draft_year"), know.get("draft_class"), eng.get("draft_class"))
        rookie_class_year = i(p.get("rookie_class_year"))

        # Master identity owns age. Derived tables are not allowed to invent it.
        age = n(mi.get("age"), p.get("age"), default=0) or None
        stage = age_stage(age, pos)

        prospect_score = n(pr.get("prospect_score"), pg.get("prospect_score"), eng.get("asset_value_score"), default=0)
        draft_capital_score = n(pr.get("draft_capital_score"), pg.get("draft_capital_score"), know.get("draft_capital_score"), eng.get("draft_capital_score"), default=0)
        college_score = n(pg.get("college_production_score"), know.get("college_production_score"), eng.get("college_production_score"), default=0)

        role_score = n(ro.get("role_score"), know.get("role_opportunity_score"), eng.get("role_opportunity_score"), default=0)
        situation_score = n(sit.get("situation_score"), default=0)
        opportunity_score = n(pr.get("opportunity_score"), pg.get("opportunity_score"), ro.get("projected_volume_score"), default=0)

        market_score = n(p.get("market_consensus_score"), dy.get("market_liquidity_score"), know.get("market_value_score"), eng.get("market_value_score"), ma.get("estimated_market_value"), default=0)
        contract_score = n(p.get("contract_efficiency_score"), eng.get("contract_value_score"), default=0)

        future_score = n(p.get("future_projection_score"), fu.get("future_value_score"), fu.get("future_production_score"), default=0)
        historical_score = n(p.get("historical_ppg"), p.get("season_ppg"), fu.get("production_score"), default=0)

        rookie_asset_score = n(
            p.get("rookie_asset_score"),
            prospect_score * 0.45 + draft_capital_score * 0.25 + college_score * 0.15 + opportunity_score * 0.15 if prospect_score else 0,
            default=0,
        )

        rows.append({
            "sleeper_id": sleeper_id,
            "player_name": p.get("player_name"),
            "pos": pos,
            "search_name": p.get("search_name"),

            "nfl_team": text(mi.get("nfl_team"), p.get("nfl_team"), pr.get("nfl_team"), pg.get("nfl_team"), sit.get("nfl_team"), ro.get("nfl_team"), know.get("nfl_team"), eng.get("nfl_team"), ma.get("nfl_team")),
            "age": age,
            "years_exp": i(mi.get("years_exp"), p.get("years_exp")),
            "draft_year": draft_year,
            "draft_round": i(mi.get("draft_round"), p.get("draft_round"), pr.get("draft_round"), pg.get("draft_round"), know.get("draft_round"), eng.get("draft_round")),
            "draft_pick": i(mi.get("draft_pick"), p.get("draft_pick"), pr.get("draft_pick"), pg.get("draft_pick"), know.get("draft_pick"), eng.get("draft_pick")),
            "college": text(mi.get("college"), p.get("college"), pr.get("college"), pg.get("college"), know.get("college"), eng.get("college")),

            "rookie_class_year": rookie_class_year,
            "is_active_rookie": rookie_class_year == ACTIVE_ROOKIE_CLASS_YEAR,

            "age_curve_stage": stage,
            "age_curve_score": n(fu.get("age_curve_score"), dy.get("age_curve_score"), eng.get("age_score"), age_score(stage), default=50),

            "historical_context_score": historical_score,
            "production_trend_score": n(fu.get("future_production_score"), p.get("future_projection_score"), default=0),
            "role_score": role_score,
            "situation_score": situation_score,
            "opportunity_score": opportunity_score,
            "contract_score": contract_score,
            "market_score": market_score,
            "rookie_asset_score": rookie_asset_score,

            "identity_confidence": 90 if draft_year or age or role_score or situation_score else 60,
            "identity_notes": "v2 consolidated from universe, prospect, graph, situation, role, future, market, dynasty, knowledge graph, and engine snapshot.",
        })

    if rows:
        sb.table("player_identity_context").upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(rows)} consolidated player_identity_context rows")


if __name__ == "__main__":
    build_player_identity_context_v2()
