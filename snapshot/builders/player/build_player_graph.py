from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows


TARGET_TABLE = "player_graph"


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


def _pos(row):
    return (
        row.get("pos")
        or row.get("position")
        or row.get("player_position")
    )


def _name(row):
    return (
        row.get("player_name")
        or row.get("full_name")
        or row.get("name")
    )


def _identity_key(row):
    name = _norm(_name(row))
    pos = str(_pos(row) or "").upper().strip()

    if not name or pos not in {"QB", "RB", "WR", "TE"}:
        return None

    return f"{name}|{pos}"


def _better(current, new):
    if current in [None, "", 0, 0.0, [], {}]:
        return new
    return current


def _upsert_nested(base: dict[str, Any], key: str, data: dict[str, Any]):
    existing = base.get(key) or {}

    for k, v in data.items():
        if v in [None, "", [], {}]:
            continue

        if existing.get(k) in [None, "", 0, 0.0, [], {}]:
            existing[k] = v
        elif isinstance(v, (int, float)) and _num(v) > _num(existing.get(k)):
            existing[k] = v

    base[key] = existing


def _touch(graph: dict[str, dict[str, Any]], row: dict[str, Any]):
    key = _identity_key(row)
    if not key:
        return None

    g = graph.setdefault(key, {
        "canonical_player_id": key,
        "search_name": key.split("|")[0],
        "pos": key.split("|")[1],
    })

    name = _name(row)
    if name:
        g["player_name"] = g.get("player_name") or name

    for id_key in ["sleeper_id", "sleeper_player_id", "player_id", "gsis_id"]:
        v = row.get(id_key)
        if not v:
            continue

        if str(v).startswith("00-"):
            g["gsis_id"] = g.get("gsis_id") or str(v)
        else:
            g["sleeper_id"] = g.get("sleeper_id") or str(v)

    return g


def build_player_graph():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    graph: dict[str, dict[str, Any]] = {}

    tables = {
        "players": sb.table("players").select("*").execute().data or [],
        "sleeper_players": sb.table("sleeper_players").select("*").execute().data or [],
        "contracts": load_internal_contract_rows(sb),
        "rosters_current": sb.table("rosters_current").select("*").execute().data or [],
        "player_dynasty_asset_engine": sb.table("player_dynasty_asset_engine").select("*").execute().data or [],
        "player_development_features": sb.table("player_development_features").select("*").execute().data or [],
        "player_season_stats": sb.table("player_season_stats").select("*").execute().data or [],
        "player_contract_efficiency": sb.table("player_contract_efficiency").select("*").execute().data or [],
        "player_nfl_intelligence": sb.table("player_nfl_intelligence").select("*").execute().data or [],
        "player_market_pool": sb.table("player_market_pool").select("*").execute().data or [],
    }

    # Base identity
    for row in tables["players"]:
        g = _touch(graph, row)
        if g:
            g["nfl_team"] = _better(g.get("nfl_team"), row.get("team"))

    for row in tables["sleeper_players"]:
        g = _touch(graph, row)
        if g:
            g["nfl_team"] = _better(g.get("nfl_team"), row.get("team"))
            g["nfl_status"] = _better(g.get("nfl_status"), row.get("status"))
            g["active"] = _better(g.get("active"), row.get("is_active"))

    # Contracts
    for row in tables["contracts"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "contract", {
            "current_owner": row.get("owner_name"),
            "salary": _num(row.get("salary")),
            "years": _num(row.get("contract_years_left")),
            "contract_total_years": _num(row.get("contract_total_years")),
            "is_rookie_contract": row.get("is_rookie"),
        })

        g["current_owner"] = _better(g.get("current_owner"), row.get("owner_name"))

    # Rosters
    for row in tables["rosters_current"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "roster", {
            "current_owner": row.get("team_id"),
            "status": row.get("status"),
        })

        g["current_owner"] = _better(g.get("current_owner"), row.get("team_id"))

    # Dynasty
    for row in tables["player_dynasty_asset_engine"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "dynasty", {
            "dynasty_asset_score": _num(row.get("dynasty_asset_score")),
            "future_projection_score": _num(row.get("future_projection_score")),
            "rookie_asset_score": _num(row.get("rookie_asset_score")),
            "market_consensus_score": _num(row.get("market_consensus_score")),
            "win_now_asset_score": _num(row.get("win_now_asset_score")),
        })

    # Development
    for row in tables["player_development_features"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "development", {
            "expected_ppg_next": _num(row.get("expected_ppg_next")),
            "current_ppg": _num(row.get("current_ppg")),
            "age_curve_score": _num(row.get("age_curve_score")),
            "development_score": _num(row.get("development_score")),
            "age": _num(row.get("age")),
        })

    # Production
    for row in tables["player_season_stats"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "production", {
            "season": _num(row.get("season")),
            "season_ppg": _num(row.get("fantasy_ppg_ppr") or row.get("fantasy_ppg")),
            "games": _num(row.get("games")),
        })

    # Contract efficiency
    for row in tables["player_contract_efficiency"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "contract_efficiency", {
            "contract_efficiency_score": _num(row.get("contract_efficiency_score")),
            "contract_efficiency_grade": row.get("contract_efficiency_grade"),
            "expected_ppg": _num(row.get("expected_ppg")),
            "historical_ppg": _num(row.get("historical_ppg")),
            "position_contract_rank": _num(row.get("position_contract_rank")),
            "position_contract_percentile": _num(row.get("position_contract_percentile")),
        })

    # NFL intelligence
    for row in tables["player_nfl_intelligence"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "nfl", {
            "nfl_team": row.get("nfl_team"),
            "nfl_status": row.get("nfl_status"),
            "active": row.get("active"),
            "injury_status": row.get("injury_status"),
            "depth_chart_order": _num(row.get("depth_chart_order")),
            "nfl_intelligence_score": _num(row.get("nfl_intelligence_score")),
            "nfl_intelligence_grade": row.get("nfl_intelligence_grade"),
            "nfl_intelligence_flags": row.get("nfl_intelligence_flags"),
        })

    # Market
    for row in tables["player_market_pool"]:
        g = _touch(graph, row)
        if not g:
            continue

        _upsert_nested(g, "market", {
            "market_pool": row.get("market_pool"),
            "estimated_market_value": _num(row.get("estimated_market_value")),
            "recommended_years": _num(row.get("recommended_years")),
            "current_owner": row.get("current_owner"),
        })

    out = []

    for key, g in graph.items():
        contract = g.get("contract") or {}
        dynasty = g.get("dynasty") or {}
        production = g.get("production") or {}
        development = g.get("development") or {}
        ce = g.get("contract_efficiency") or {}

        salary = _num(contract.get("salary"))
        years = _num(contract.get("years"))
        ppg = _num(
            ce.get("expected_ppg")
            or development.get("expected_ppg_next")
            or production.get("season_ppg")
        )
        dynasty_score = _num(dynasty.get("dynasty_asset_score"))
        contract_score = _num(ce.get("contract_efficiency_score"))

        if contract_score <= 0 and salary > 0:
            contract_score = max(
                0,
                min(
                    100,
                    min(ppg * 5, 80)
                    + dynasty_score * 0.35
                    - min(salary * 1.3, 55)
                    - max(years - 1, 0) * 4,
                ),
            )

        summary = (
            f"{g.get('player_name')} ({g.get('pos')}) — "
            f"owner={g.get('current_owner') or contract.get('current_owner') or 'FA'}, "
            f"salary=${salary:g}, years={years:g}, "
            f"ppg={ppg:.1f}, dynasty={dynasty_score:.1f}, contract={contract_score:.1f}."
        )

        out.append({
            "canonical_player_id": key,
            "sleeper_id": g.get("sleeper_id"),
            "gsis_id": g.get("gsis_id"),
            "player_name": g.get("player_name"),
            "search_name": g.get("search_name") or _norm(g.get("player_name")),
            "pos": g.get("pos"),
            "nfl_team": g.get("nfl_team") or (g.get("nfl") or {}).get("nfl_team"),
            "current_owner": g.get("current_owner") or contract.get("current_owner"),
            "salary": salary,
            "years": years,
            "expected_ppg": ppg,
            "season_ppg": _num(production.get("season_ppg")),
            "dynasty_asset_score": dynasty_score,
            "contract_efficiency_score": round(contract_score, 2),
            "contract_efficiency_grade": ce.get("contract_efficiency_grade") or ("FALLBACK" if contract_score else None),
            "identity": {
                "sleeper_id": g.get("sleeper_id"),
                "gsis_id": g.get("gsis_id"),
                "search_name": g.get("search_name"),
            },
            "contract": contract,
            "dynasty": dynasty,
            "production": production,
            "development": development,
            "contract_efficiency": ce,
            "nfl": g.get("nfl") or {},
            "market": g.get("market") or {},
            "player_graph_summary": summary,
            "updated_at": now,
        })

    if out:
        sb.table(TARGET_TABLE).delete().neq("canonical_player_id", "__never__").execute()
        sb.table(TARGET_TABLE).upsert(out, on_conflict="canonical_player_id").execute()

    print(f"Upserted {len(out)} player_graph rows")


if __name__ == "__main__":
    build_player_graph()
