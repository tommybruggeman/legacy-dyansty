from __future__ import annotations

from gm_assistant.nlu.schema import ParsedGMQuestion
from gm_assistant.planner.models import ExecutionPlan, ExecutionStep


def build_execution_plan(parsed: ParsedGMQuestion) -> ExecutionPlan:
    intent = parsed.intent

    if intent == "contract_value_ranking":
        return ExecutionPlan(
            intent=intent,
            objective="Rank league/player contract value using points per dollar.",
            steps=[
                ExecutionStep("load_league_players"),
                ExecutionStep("load_contracts"),
                ExecutionStep("calculate_points_per_dollar"),
                ExecutionStep("rank_contract_values"),
            ],
            expected_output="contract_value_rankings",
            confidence=0.9,
        )

    if intent in {"target_recommendations", "free_agent_targets"}:
        return ExecutionPlan(
            intent=intent,
            objective="Find add targets that fit the user's roster and goal.",
            steps=[
                ExecutionStep("load_user_roster"),
                ExecutionStep("identify_team_needs"),
                ExecutionStep("load_available_or_trade_targets"),
                ExecutionStep("score_fit"),
                ExecutionStep("rank_targets"),
            ],
            expected_output="target_list",
            confidence=0.88,
        )

    if intent == "rookie_pick_fit":
        return ExecutionPlan(
            intent=intent,
            objective="Evaluate rookie pick fit versus trading the pick.",
            steps=[
                ExecutionStep("load_user_roster"),
                ExecutionStep("identify_team_needs"),
                ExecutionStep("load_rookie_board"),
                ExecutionStep("rank_rookie_fit"),
                ExecutionStep("compare_pick_trade_value"),
            ],
            expected_output="rookie_pick_recommendation",
            confidence=0.86,
        )

    if intent in {"trade_package", "trade_partner_search"}:
        return ExecutionPlan(
            intent=intent,
            objective="Build trade partner/package logic from roster fit and market needs.",
            steps=[
                ExecutionStep("load_user_roster"),
                ExecutionStep("load_league_rosters"),
                ExecutionStep("identify_partner_needs"),
                ExecutionStep("match_surplus_to_need"),
                ExecutionStep("construct_trade_framework"),
            ],
            expected_output="trade_plan",
            confidence=0.84,
        )

    if intent in {"team_overview", "team_needs", "team_strengths", "core_player_review", "win_now_player_ranking"}:
        return ExecutionPlan(
            intent=intent,
            objective="Evaluate the user's roster through the active team goal.",
            steps=[
                ExecutionStep("load_user_roster"),
                ExecutionStep("load_team_scores"),
                ExecutionStep("identify_strengths_and_weaknesses"),
                ExecutionStep("rank_actionable_moves"),
            ],
            expected_output="team_review",
            confidence=0.82,
        )

    if intent in {"player_trade_decision", "player_drop_decision", "player_contract_fit"}:
        return ExecutionPlan(
            intent=intent,
            objective="Make a player-specific decision using contract, production, market, and team fit.",
            steps=[
                ExecutionStep("load_player_context"),
                ExecutionStep("load_contract_context"),
                ExecutionStep("load_team_fit"),
                ExecutionStep("make_player_decision"),
            ],
            expected_output="player_decision",
            confidence=0.86,
        )

    return ExecutionPlan(
        intent=intent,
        objective="Answer the GM question directly after identifying needed evidence.",
        steps=[ExecutionStep("load_relevant_context", required=False)],
        expected_output="direct_answer",
        confidence=0.6,
    )
