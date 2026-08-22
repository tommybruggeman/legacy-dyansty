from __future__ import annotations

from auth import service_client
from snapshot.runtime.season import get_current_season
from gm_assistant.nlu.parser import parse_gm_question
from gm_assistant.production_context import get_production_context


def _first(rows):
    return rows[0] if rows else None


def _safe_rows(table: str, select: str = "*", filters: list[tuple[str, str, object]] | None = None, limit: int = 50):
    sb = service_client()

    try:
        q = sb.table(table).select(select).limit(limit)

        for col, op, val in filters or []:
            if op == "eq":
                q = q.eq(col, val)
            elif op == "ilike":
                q = q.ilike(col, val)

        return q.execute().data or []
    except Exception as e:
        return [{"_error": str(e), "_table": table}]


def _num(row, key):
    try:
        return float(row.get(key)) if row and row.get(key) is not None else None
    except Exception:
        return None


def _score_bundle(rookie_row: dict, universe_row: dict, identity_row: dict, graph_row: dict) -> dict:
    production = get_production_context(graph_row or universe_row or identity_row or {})

    return {
        "final_rookie_score": _num(rookie_row, "final_rookie_score"),
        "prospect_score": _num(rookie_row, "prospect_score"),
        "positional_value_score": _num(rookie_row, "positional_value_score"),
        "future_score": _num(rookie_row, "future_score"),
        "team_need_fit_score": _num(rookie_row, "team_need_fit_score"),
        "asset_score": (
            _num(graph_row, "dynasty_asset_score")
            or _num(universe_row, "dynasty_asset_score")
            or _num(identity_row, "asset_score")
        ),
        "market_value": (
            _num(graph_row, "trade_value_score")
            or _num(universe_row, "trade_value")
            or _num(identity_row, "trade_value")
        ),
        "season_ppg": production["primary_ppg"],
        "primary_ppg": production["primary_ppg"],
        "production_score": production["production_score"],
        "production_trend": production["trend_label"],
        "production_confidence": production["production_confidence"],
        "production_source": production["production_source"],
    }


def build_player_context(player_name: str, season: int | None = None) -> dict:
    season = season or get_current_season()
    graph_rows = _safe_rows(
        "player_graph_v2",
        filters=[
            ("player_name", "ilike", f"%{player_name}%"),
        ],
        limit=5,
    )

    rookie_rows = _safe_rows(
        "rookie_draft_board",
        filters=[
            ("player_name", "ilike", f"%{player_name}%"),
        ],
        limit=5,
    )

    universe_rows = _safe_rows(
        "player_universe",
        filters=[
            ("player_name", "ilike", f"%{player_name}%"),
        ],
        limit=5,
    )

    identity_rows = _safe_rows(
        "player_identity_context",
        filters=[
            ("player_name", "ilike", f"%{player_name}%"),
        ],
        limit=5,
    )

    source_tasks = _safe_rows(
        "legacy_source_task_queue",
        filters=[
            ("player_name", "eq", player_name),
            ("season", "eq", season),
        ],
        limit=25,
    )

    graph = _first([r for r in graph_rows if not r.get("_error")])
    rookie = _first([r for r in rookie_rows if not r.get("_error")])
    universe = _first([r for r in universe_rows if not r.get("_error")])
    identity = _first([r for r in identity_rows if not r.get("_error")])

    warnings = []
    if not graph:
        warnings.append("No player_graph_v2 row found.")
    if not rookie:
        warnings.append("No rookie_draft_board row found.")
    if not universe:
        warnings.append("No player_universe row found.")
    if not identity:
        warnings.append("No player_identity_context row found.")
    if source_tasks:
        warnings.append(f"{len(source_tasks)} open source task group(s).")

    return {
        "player_name": player_name,
        "player_graph_v2": graph,
        "rookie_draft_board": rookie,
        "player_universe": universe,
        "player_identity_context": identity,
        "source_tasks": source_tasks,
        "production": get_production_context(graph or universe or identity or {}),
        "scores": _score_bundle(rookie, universe, identity, graph),
        "warnings": warnings,
    }


def build_team_context(owner_team_name: str, season: int | None = None) -> dict:
    season = season or get_current_season()
    graph_roster = _safe_rows(
        "player_graph_v2",
        filters=[
            ("owner_team_name", "eq", owner_team_name),
        ],
        limit=100,
    )

    fallback_roster = _safe_rows(
        "player_universe",
        filters=[
            ("current_owner", "eq", owner_team_name),
        ],
        limit=100,
    )

    roster = [r for r in graph_roster if not r.get("_error")] or fallback_roster

    future = _first(_safe_rows(
        "team_future_context",
        filters=[
            ("owner_team_name", "eq", owner_team_name),
        ],
        limit=1,
    ))

    source_tasks = _safe_rows(
        "legacy_source_task_queue",
        filters=[
            ("season", "eq", season),
        ],
        limit=200,
    )

    return {
        "owner_team_name": owner_team_name,
        "roster": roster,
        "roster_source": "player_graph_v2" if roster == [r for r in graph_roster if not r.get("_error")] else "player_universe",
        "team_future_context": future,
        "source_tasks": source_tasks,
    }


def build_question_context(question: str, owner_team_name: str, season: int | None = None) -> dict:
    season = season or get_current_season()
    parsed = parse_gm_question(question)
    understanding = {
        "players": getattr(parsed, "players", []) or [],
        "positions": getattr(parsed, "positions", []) or [],
        "intent": getattr(parsed, "intent", None),
    }
    players = understanding.get("players") or []

    player_contexts = {
        player: build_player_context(player, season=season)
        for player in players
    }

    team_context = build_team_context(owner_team_name, season=season)

    return {
        "question": question,
        "season": season,
        "understanding": understanding,
        "players": player_contexts,
        "team_context": team_context,
    }
