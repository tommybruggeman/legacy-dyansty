from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows


TARGET_TABLE = "player_graph_v2"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _norm(v):
    s = str(v or "").strip().lower()
    s = s.replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b$", "", s).strip()
    return " ".join(s.split())


def _bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in {"true", "1", "yes", "active"}


def _name(row):
    return row.get("player_name") or row.get("full_name") or row.get("name")


def _pos(row):
    return row.get("pos") or row.get("position") or row.get("player_position")


def _key(row):
    name = _norm(_name(row))
    pos = str(_pos(row) or "").upper().strip()
    if not name or pos not in {"QB", "RB", "WR", "TE"}:
        return None
    return f"{name}|{pos}"


def _merge(base, key, data):
    obj = base.get(key) or {}
    for k, v in data.items():
        if v in [None, "", [], {}]:
            continue
        if obj.get(k) in [None, "", 0, 0.0, [], {}]:
            obj[k] = v
        elif isinstance(v, (int, float)) and _num(v) > _num(obj.get(k)):
            obj[k] = v
    base[key] = obj


def _touch(graph, row):
    key = _key(row)
    if not key:
        return None

    g = graph.setdefault(key, {
        "canonical_player_id": key,
        "search_name": key.split("|")[0],
        "pos": key.split("|")[1],
    })

    if _name(row):
        g["player_name"] = g.get("player_name") or _name(row)

    for id_key in ["sleeper_id", "sleeper_player_id", "player_id", "gsis_id"]:
        v = row.get(id_key)
        if not v:
            continue
        v = str(v)
        if v.startswith("00-"):
            g["gsis_id"] = g.get("gsis_id") or v
        else:
            g["sleeper_id"] = g.get("sleeper_id") or v

    return g


def build_player_graph_v2():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    tables = {
        "players": sb.table("players").select("*").execute().data or [],
        "sleeper_players": sb.table("sleeper_players").select("*").execute().data or [],
        "contracts": load_internal_contract_rows(sb),
        "rosters_current": sb.table("rosters_current").select("*").execute().data or [],
        "dynasty": sb.table("player_dynasty_asset_engine").select("*").execute().data or [],
        "development": sb.table("player_development_features").select("*").execute().data or [],
        "season": sb.table("player_season_stats").select("*").execute().data or [],
        "contract_eff": sb.table("player_contract_efficiency").select("*").execute().data or [],
        "nfl": sb.table("player_nfl_intelligence").select("*").execute().data or [],
        "market": sb.table("player_market_pool").select("*").execute().data or [],
    }

    graph: dict[str, dict[str, Any]] = {}

    for row in tables["players"]:
        g = _touch(graph, row)
        if g:
            g["nfl_team"] = g.get("nfl_team") or row.get("team")

    for row in tables["sleeper_players"]:
        g = _touch(graph, row)
        if g:
            g["nfl_team"] = g.get("nfl_team") or row.get("team")
            _merge(g, "nfl", {
                "status": row.get("status"),
                "is_active": row.get("is_active"),
                "team": row.get("team"),
            })

    for row in tables["contracts"]:
        g = _touch(graph, row)
        if g:
            owner = row.get("owner_name")
            g["owner_team_name"] = g.get("owner_team_name") or owner
            _merge(g, "contract", {
                "owner_team_name": owner,
                "salary": _num(row.get("salary")),
                "years": _num(row.get("contract_years_left")),
                "contract_total_years": _num(row.get("contract_total_years")),
                "is_rookie_contract": row.get("is_rookie"),
            })

    for row in tables["rosters_current"]:
        g = _touch(graph, row)
        if g:
            owner = row.get("team_id")
            g["owner_team_name"] = g.get("owner_team_name") or owner
            _merge(g, "roster", {
                "owner_team_name": owner,
                "status": row.get("status"),
            })

    for row in tables["dynasty"]:
        g = _touch(graph, row)
        if g:
            _merge(g, "dynasty", {
                "dynasty_asset_score": _num(row.get("dynasty_asset_score")),
                "future_projection_score": _num(row.get("future_projection_score")),
                "rookie_asset_score": _num(row.get("rookie_asset_score")),
                "market_consensus_score": _num(row.get("market_consensus_score")),
                "win_now_asset_score": _num(row.get("win_now_asset_score")),
            })

    for row in tables["development"]:
        g = _touch(graph, row)
        if g:
            _merge(g, "development", {
                "expected_ppg_next": _num(row.get("expected_ppg_next")),
                "current_ppg": _num(row.get("current_ppg")),
                "age": _num(row.get("age")),
                "age_curve_score": _num(row.get("age_curve_score")),
                "development_score": _num(row.get("development_score")),
            })

    for row in tables["season"]:
        g = _touch(graph, row)
        if g:
            _merge(g, "production", {
                "season": _num(row.get("season")),
                "season_ppg": _num(row.get("fantasy_ppg_ppr") or row.get("fantasy_ppg")),
                "games": _num(row.get("games")),
            })

    for row in tables["contract_eff"]:
        g = _touch(graph, row)
        if g:
            _merge(g, "contract_efficiency", {
                "contract_efficiency_score": _num(row.get("contract_efficiency_score")),
                "contract_efficiency_grade": row.get("contract_efficiency_grade"),
                "expected_ppg": _num(row.get("expected_ppg")),
                "historical_ppg": _num(row.get("historical_ppg")),
            })

    for row in tables["nfl"]:
        g = _touch(graph, row)
        if g:
            g["nfl_team"] = g.get("nfl_team") or row.get("nfl_team")
            _merge(g, "nfl", {
                "nfl_team": row.get("nfl_team"),
                "nfl_status": row.get("nfl_status"),
                "active": row.get("active"),
                "injury_status": row.get("injury_status"),
                "depth_chart_order": _num(row.get("depth_chart_order")),
                "nfl_intelligence_score": _num(row.get("nfl_intelligence_score")),
                "nfl_intelligence_grade": row.get("nfl_intelligence_grade"),
                "nfl_intelligence_flags": row.get("nfl_intelligence_flags"),
            })

    for row in tables["market"]:
        g = _touch(graph, row)
        if g:
            _merge(g, "market", {
                "market_pool": row.get("market_pool"),
                "estimated_market_value": _num(row.get("estimated_market_value")),
                "recommended_years": _num(row.get("recommended_years")),
                "current_owner": row.get("current_owner"),
            })

    out = []

    for key, g in graph.items():
        contract = g.get("contract") or {}
        dynasty = g.get("dynasty") or {}
        prod = g.get("production") or {}
        dev = g.get("development") or {}
        ce = g.get("contract_efficiency") or {}
        nfl = g.get("nfl") or {}
        market = g.get("market") or {}

        owner = g.get("owner_team_name") or contract.get("owner_team_name") or market.get("current_owner")
        salary = _num(contract.get("salary"))
        years = _num(contract.get("years"))
        expected_ppg = _num(ce.get("expected_ppg") or dev.get("expected_ppg_next") or prod.get("season_ppg"))
        season_ppg = _num(prod.get("season_ppg"))
        dynasty_score = _num(dynasty.get("dynasty_asset_score"))
        contract_score = _num(ce.get("contract_efficiency_score"))

        nfl_status = str(nfl.get("nfl_status") or nfl.get("status") or "").lower()
        nfl_team = g.get("nfl_team") or nfl.get("nfl_team")
        active = nfl.get("active")
        is_active = _bool(active, default=True)

        flags = []

        is_rostered = bool(owner)
        is_retired = "retired" in nfl_status or "inactive" in nfl_status
        if not is_active:
            flags.append("not_active")
        if is_retired:
            flags.append("retired_or_inactive")

        # True rookie detection: do NOT trust rookie_asset_score.
        # Temporary rule until rookie draft board is wired:
        # rookie only if market pool says rookie draft or contract explicitly rookie with no NFL history.
        market_pool = str(market.get("market_pool") or "").upper()
        is_rookie = "ROOKIE" in market_pool or bool(contract.get("is_rookie_contract") and season_ppg <= 0 and expected_ppg <= 10)

        is_free_agent = not is_rostered and not is_retired and is_active

        if not is_active or is_retired:
            availability_status = "inactive"
        elif is_rostered:
            availability_status = "rostered"
        elif is_free_agent:
            availability_status = "free_agent"
        else:
            availability_status = "unknown"

        if expected_ppg > 12 and not is_active:
            flags.append("projection_active_mismatch")
        if is_free_agent and dynasty_score >= 50:
            flags.append("high_value_unowned_check")
        if is_rookie:
            flags.append("rookie_candidate")

        # ------------------------------------------------------------
        # Trust / eligibility layer
        # ------------------------------------------------------------
        projection_confidence = 0.0
        ownership_confidence = 0.0
        data_confidence = 0.0

        if is_rostered and owner:
            ownership_confidence = 1.0
        elif is_free_agent and dynasty_score < 45 and expected_ppg < 12:
            ownership_confidence = 0.75
        elif is_free_agent and (dynasty_score >= 45 or expected_ppg >= 12):
            ownership_confidence = 0.25
            flags.append("ownership_needs_review")
        else:
            ownership_confidence = 0.5

        if is_active and expected_ppg > 0 and dynasty_score > 0:
            projection_confidence = 0.9
        elif is_active and expected_ppg > 0 and dynasty_score <= 0:
            projection_confidence = 0.2
            flags.append("projection_without_value_support")
        elif not is_active and expected_ppg > 0:
            projection_confidence = 0.05
        else:
            projection_confidence = 0.4

        if is_retired:
            player_status = "RETIRED"
        elif not is_active:
            player_status = "INACTIVE"
        elif is_rookie:
            player_status = "ROOKIE"
        elif is_rostered:
            player_status = "ACTIVE_ROSTERED"
        elif is_free_agent and ownership_confidence >= 0.7:
            player_status = "FREE_AGENT_ACTIVE"
        elif is_free_agent:
            player_status = "UNOWNED_REVIEW"
        else:
            player_status = "UNKNOWN"

        reasoning_eligible = player_status in {
            "ACTIVE_ROSTERED",
            "FREE_AGENT_ACTIVE",
            "ROOKIE",
        }

        # Historical/stale leak guard.
        if expected_ppg >= 10 and dynasty_score <= 5:
            reasoning_eligible = False
            player_status = "HISTORICAL_OR_STALE"
            flags.append("excluded_historical_projection")

        if is_free_agent and (dynasty_score >= 45 or expected_ppg >= 12):
            reasoning_eligible = False
            player_status = "UNOWNED_REVIEW"
            flags.append("excluded_until_ownership_verified")

        if not is_active or is_retired:
            reasoning_eligible = False

        data_confidence = round(
            ownership_confidence * 0.4
            + projection_confidence * 0.35
            + (0.25 if dynasty_score > 0 else 0.05),
            2,
        )

        if contract_score <= 0 and salary > 0:
            contract_score = max(
                0,
                min(
                    100,
                    min(expected_ppg * 5, 80)
                    + dynasty_score * 0.35
                    - min(salary * 1.3, 55)
                    - max(years - 1, 0) * 4,
                ),
            )
            flags.append("fallback_contract_score")

        trade_value = round(dynasty_score * 0.55 + expected_ppg * 2.0 + contract_score * 0.15, 2)

        summary = (
            f"{g.get('player_name')} ({g.get('pos')}) — {availability_status}, "
            f"owner={owner or 'FA'}, active={is_active}, rookie={is_rookie}, "
            f"PPG={expected_ppg:.1f}, dynasty={dynasty_score:.1f}, contract={contract_score:.1f}."
        )

        out.append({
            "canonical_player_id": key,
            "sleeper_id": g.get("sleeper_id"),
            "gsis_id": g.get("gsis_id"),
            "player_name": g.get("player_name"),
            "search_name": g.get("search_name") or key.split("|")[0],
            "pos": g.get("pos"),
            "nfl_team": nfl_team,
            "owner_team_name": owner,
            "is_rostered": is_rostered,
            "is_free_agent": is_free_agent,
            "is_active": is_active,
            "is_retired": is_retired,
            "is_rookie": is_rookie,
            "availability_status": availability_status,
            "player_status": player_status,
            "reasoning_eligible": reasoning_eligible,
            "data_confidence": data_confidence,
            "projection_confidence": projection_confidence,
            "ownership_confidence": ownership_confidence,
            "salary": salary,
            "years": years,
            "expected_ppg": expected_ppg if is_active else 0,
            "season_ppg": season_ppg,
            "dynasty_asset_score": dynasty_score,
            "contract_efficiency_score": round(contract_score, 2),
            "trade_value_score": trade_value,
            "player_flags": flags,
            "identity": {"sleeper_id": g.get("sleeper_id"), "gsis_id": g.get("gsis_id")},
            "contract": contract,
            "dynasty": dynasty,
            "production": prod,
            "development": dev,
            "nfl": nfl,
            "market": market,
            "player_graph_summary": summary,
            "updated_at": now,
        })

    if out:
        sb.table(TARGET_TABLE).delete().neq("canonical_player_id", "__never__").execute()
        sb.table(TARGET_TABLE).upsert(out, on_conflict="canonical_player_id").execute()

    print(f"Upserted {len(out)} player_graph_v2 rows")


if __name__ == "__main__":
    build_player_graph_v2()
