from __future__ import annotations

from typing import Callable

GMHandler = Callable[[str, str, dict], dict]


def _contract_best_value(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.gm_brain import _contract_best_value_answer
    return _contract_best_value_answer(question, owner_team_name)


def _position_review(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_position_review
    return answer_position_review(question, owner_team_name, understanding)


def _data_quality(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_data_quality_review
    return answer_data_quality_review(question, owner_team_name, understanding)


def _team_review(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_team_review
    return answer_team_review(question, owner_team_name, understanding)


def _trade_strategy(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_trade_strategy
    return answer_trade_strategy(question, owner_team_name, understanding)


def _player_trade(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_player_trade_decision
    return answer_player_trade_decision(question, owner_team_name, understanding)


def _roster_exit(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_roster_exit_decision
    return answer_roster_exit_decision(question, owner_team_name, understanding)


def _qb_to_rb(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_qb_surplus_to_rb_strategy
    return answer_qb_surplus_to_rb_strategy(question, owner_team_name, understanding)


def _trade_return_value(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.skills.gm_core_skills import answer_trade_return_value
    return answer_trade_return_value(question, owner_team_name, understanding)


def _sell_high(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.skills.gm_core_skills import answer_sell_high
    return answer_sell_high(question, owner_team_name, understanding)


def _contract_audit(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.skills.gm_core_skills import answer_contract_audit
    return answer_contract_audit(question, owner_team_name, understanding)


def _question_recommendation(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.skills.gm_core_skills import answer_question_recommendation
    return answer_question_recommendation(question, owner_team_name, understanding)



def _expansion_skill(name: str):
    def _handler(question: str, owner_team_name: str, understanding: dict) -> dict:
        import gm_assistant.skills.gm_expansion_skills as skills
        return getattr(skills, name)(question, owner_team_name, understanding)
    return _handler



INTENT_REGISTRY: dict[str, GMHandler] = {
    "MOVE_CANDIDATES": _expansion_skill("answer_move_candidates"),
    "TRADE_CANDIDATES": _expansion_skill("answer_trade_candidates"),
    "CUT_RECOMMENDATIONS": _expansion_skill("answer_cut_recommendations"),
    "ROSTER_CLOGGERS": _expansion_skill("answer_roster_cloggers"),
    "PROTECT_PLAYERS": _expansion_skill("answer_protect_players"),
    "TRADE_PACKAGE": _expansion_skill("answer_trade_package"),
    "TRADE_PICKS_STRATEGY": _expansion_skill("answer_trade_picks"),
    "ALL_IN_STRATEGY": _expansion_skill("answer_all_in_strategy"),
    "POSITION_UPGRADES": _expansion_skill("answer_position_upgrades"),
    "DEPTH_REVIEW": _expansion_skill("answer_depth_review"),
    "LEAST_VALUABLE_PLAYER": _expansion_skill("answer_move_candidates"),
    "BEST_PRODUCTION": _expansion_skill("answer_production_review"),
    "WORST_PRODUCTION": _expansion_skill("answer_production_review"),
    "OUTPRODUCING_CONTRACT": _expansion_skill("answer_production_review"),
    "UNDERPRODUCING_CONTRACT": _expansion_skill("answer_production_review"),

    "CONTRACT_BEST_VALUE": _contract_best_value,
    "CONTRACT_AUDIT": _contract_audit,

    "POSITION_REVIEW": _position_review,
    "POSITION_RANKING": _position_review,

    "DATA_QUALITY_REVIEW": _data_quality,
    "PRODUCTION_REVIEW": _data_quality,

    "TEAM_REVIEW": _team_review,
    "TEAM_WINDOW": _team_review,
    "GM_PLAN": _team_review,
    "TEAM_STRENGTHS": _team_review,
    "TEAM_WEAKNESSES": _team_review,
    "GM_SUMMARY": _team_review,

    "TRADE_PACKAGE": _trade_strategy,
    "TRADE_STRATEGY": _trade_strategy,
    "TRADE_RETURN_VALUE": _trade_return_value,

    "PLAYER_TRADE_DECISION": _player_trade,
    "PLAYER_HOLD_DECISION": _player_trade,

    "CUT_OR_CHURN": _roster_exit,
    "ROSTER_EXIT_DECISION": _roster_exit,
    "SELL_HIGH": _sell_high,
    "SNEAKY_HOLD": _roster_exit,
    "QUESTION_RECOMMENDATION": _question_recommendation,

    "LINEUP_DECISION": _position_review,
    "TAXI_DECISION": _position_review,

    "QB_SURPLUS_TO_RB_STRATEGY": _qb_to_rb,
}


def answer_registered_intent(question: str, owner_team_name: str, understanding: dict) -> dict | None:
    from gm_assistant.planner.planner import build_plan
    from gm_assistant.planner.executor import execute_plan

    intent = str(understanding.get("intent") or "").upper()
    handler = INTENT_REGISTRY.get(intent)
    if not handler:
        return None

    plan = build_plan(question, understanding)
    plan_context = execute_plan(plan, owner_team_name, understanding)

    enriched_understanding = dict(understanding)
    enriched_understanding["plan"] = plan.to_dict()
    enriched_understanding["plan_context"] = plan_context

    answer = handler(question, owner_team_name, enriched_understanding)

    if isinstance(answer, dict):
        if not answer.get("decision"):
            answer["decision"] = intent
        answer["plan"] = plan.to_dict()
        answer["plan_context_summary"] = {
            "loaded_keys": list((plan_context.get("loaded") or {}).keys()),
            "warnings": plan_context.get("warnings") or [],
        }

    return answer
