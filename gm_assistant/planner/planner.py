from __future__ import annotations

from gm_assistant.planner.models import GMPlan, PlanTask


def build_plan(question: str, understanding: dict) -> GMPlan:
    intent = str(understanding.get("intent") or "UNKNOWN").upper()
    players = understanding.get("players") or []
    positions = understanding.get("positions") or []

    tasks: list[PlanTask] = [
        PlanTask("load_roster"),
        PlanTask("load_team_context"),
    ]

    if players:
        tasks.append(PlanTask("load_player", {"player_name": players[0]}))
        tasks.append(PlanTask("load_contract", {"player_name": players[0]}))
        tasks.append(PlanTask("load_production", {"player_name": players[0]}))

    if positions:
        tasks.append(PlanTask("load_position_group", {"position": positions[0]}))

    if intent in {
        "TRADE_RETURN_VALUE",
        "PLAYER_TRADE_DECISION",
        "TRADE_PACKAGE",
        "TRADE_CANDIDATES",
        "SELL_HIGH",
        "TRADE_STRATEGY",
    }:
        tasks.extend([
            PlanTask("load_trade_leverage"),
            PlanTask("analyze_trade_fit"),
        ])

    if intent in {
        "CONTRACT_AUDIT",
        "CONTRACT_BEST_VALUE",
        "PROTECT_PLAYERS",
        "OUTPRODUCING_CONTRACT",
        "UNDERPRODUCING_CONTRACT",
    }:
        tasks.extend([
            PlanTask("load_contract_board"),
            PlanTask("analyze_contract_efficiency"),
        ])

    if intent in {
        "GM_PLAN",
        "TEAM_REVIEW",
        "POSITION_UPGRADES",
        "ALL_IN_STRATEGY",
        "TRADE_DEADLINE_PLAN",
        "QUESTION_RECOMMENDATION",
    }:
        tasks.extend([
            PlanTask("load_team_needs"),
            PlanTask("analyze_contention_window"),
            PlanTask("build_action_plan"),
        ])

    if intent in {
        "DATA_QUALITY_REVIEW",
        "BEST_PRODUCTION",
        "WORST_PRODUCTION",
    }:
        tasks.extend([
            PlanTask("load_production_board"),
            PlanTask("analyze_data_quality"),
        ])

    return GMPlan(
        intent=intent,
        question=question,
        tasks=tasks,
        route_hint=understanding.get("route_hint"),
        confidence=float(understanding.get("confidence") or 0.75),
    )
