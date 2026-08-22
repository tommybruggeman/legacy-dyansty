from __future__ import annotations

from typing import Any

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question
from gm_assistant.decision.intent import GMIntent
from gm_assistant.engines.trade_scenario_engine import evaluate_trade_scenario


def answer_from_intent(intent: GMIntent) -> dict[str, Any]:
    if intent.intent == "trade_scenario" and intent.primary_player and intent.comparison_player:
        return evaluate_trade_scenario(
            owner_team_name=intent.owner_team_name,
            give_player=intent.primary_player,
            receive_player=intent.comparison_player,
            team_goal=intent.team_goal,
        )

    if intent.intent == "cut_decision" and intent.primary_player:
        prompt = (
            f"Evaluate whether {intent.owner_team_name} should cut/drop {intent.primary_player}. "
            f"Compare HOLD vs SHOP/TRADE vs CUT. Include salary, years, contract efficiency, "
            f"replacement value, dead-cap implications if available, and team goal: {intent.team_goal or 'unspecified'}. "
            f"Do not analyze {intent.comparison_player or 'any comparison player'} unless directly relevant."
        )
        return answer_gm_question(prompt, intent.owner_team_name)

    if intent.intent == "contract_forecast" and intent.primary_player:
        prompt = (
            f"What would need to happen for {intent.primary_player} to become worth his current contract? "
            f"Include required fantasy PPG, positional finish, production tier, contract efficiency improvement, "
            f"risk factors, and confidence/probability. Team goal: {intent.team_goal or 'unspecified'}."
        )
        return answer_gm_question(prompt, intent.owner_team_name)


    if intent.intent == "roster_liability":
        prompt = (
            f"For {intent.owner_team_name}, identify the players hurting the roster most. "
            f"Rank them by contract burden, weak production, poor value, roster clogging, and opportunity cost. "
            f"Give a direct natural-language answer with names and actions: keep, shop, restructure, cut, or monitor. "
            f"Team goal: {intent.team_goal or 'contend'}."
        )
        return answer_gm_question(prompt, intent.owner_team_name)

    if intent.intent == "market_check":
        prompt = (
            f"For {intent.owner_team_name}, identify who to market-check first. "
            f"Prioritize players with name value, expensive contracts, replaceable production, or surplus-position value. "
            f"Give a ranked list and explain what return would make sense."
        )
        return answer_gm_question(prompt, intent.owner_team_name)

    if intent.intent == "next_move":
        prompt = (
            f"For {intent.owner_team_name}, give the single next best GM move. "
            f"Do not repeat a broad roster overview. Be specific: name the player or position, the action, "
            f"and the target outcome. Team goal: {intent.team_goal or 'contend'}."
        )
        return answer_gm_question(prompt, intent.owner_team_name)

    if intent.intent == "explain_previous":
        prompt = (
            f"Explain the previous recommendation in plain natural language. "
            f"Focus on the current topic/player: {intent.primary_player or 'team strategy'}. "
            f"Do not restart the analysis."
        )
        return answer_gm_question(prompt, intent.owner_team_name)


    if intent.intent == "team_strategy":
        prompt = (
            f"Give team strategy for {intent.owner_team_name}. "
            f"Team goal/context: {intent.team_goal or 'unspecified'}. "
            f"Answer at roster level, not as a single-player decision."
        )
        return answer_gm_question(prompt, intent.owner_team_name)

    return answer_gm_question(
        intent.resolved_question or intent.original_question or "",
        intent.owner_team_name,
    )
