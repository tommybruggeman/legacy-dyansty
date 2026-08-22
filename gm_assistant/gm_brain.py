from __future__ import annotations


from gm_assistant.cognition.orchestrator import GMBrainOrchestrator
from gm_assistant.reasoning.analyzer import analyze_question
from gm_assistant.reasoning.engine import answer_reasoned_question
from gm_assistant.reasoning.models import EvidenceBundle
from gm_assistant.capabilities.registry import run_capability
from gm_assistant.nlu.parser import parse_gm_question
from gm_assistant.planner.reasoning_planner import build_execution_plan
from gm_assistant.executor.executor import execute_plan
from gm_assistant.composer.target_composer import compose_target_recommendations
from gm_assistant.composer.contract_composer import compose_contract_value_answer

from gm_assistant.gm_orchestrator import orchestrate_gm_answer

from auth import service_client
from gm_assistant.roster_loader import rows_for_owner
from gm_assistant.question_understanding_engine import understand_question
from gm_assistant.rookie_understanding_answer import answer_rookie_understanding_question
from gm_assistant.roster_understanding_answer import answer_roster_understanding_question
from gm_assistant.brain import answer_asset_question
from gm_assistant.gm_reasoning import build_team_reasoning, summarize_reasoning_as_text
import re
import unicodedata
from gm_assistant.engines.conversational_decision_engine import answer_conversational_decision
from gm_assistant.engines.evidence_engine import build_evidence
from gm_assistant.engines.conversation_writer import write_gm_answer
from gm_assistant.engines.conversation_state_engine import resolve_followup_question, update_conversation_state
from gm_assistant.engines.gm_planner_engine import answer_with_planner


DEFAULT_OWNER_TEAM_NAME = "Tommy Bruggeman"


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _rows_for_owner(owner_team_name: str):
    return rows_for_owner(owner_team_name)

def _team_future_for_owner(owner_team_name: str) -> dict:
    sb = service_client()
    rows = (
        sb.table("team_future_context")
        .select("*")
        .eq("owner_team_name", owner_team_name)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else {}


def _team_future_summary(team_future: dict) -> str:
    if not team_future:
        return "No team future context available yet."

    score = team_future.get("future_score")
    grade = team_future.get("future_grade")
    window = team_future.get("team_window")
    core = team_future.get("young_core_count")
    picks = team_future.get("draft_pick_count")
    premium = team_future.get("premium_pick_count")

    return (
        f"Team future context: {window} ({grade}, score {score}). "
        f"Young/core assets: {core}. Draft picks: {picks}, including {premium} premium picks."
    )


def _team_future_directive(team_future: dict) -> str:
    window = str(team_future.get("team_window", "")).upper()
    score = float(team_future.get("future_score") or 0)

    if "CONTENDER" in window:
        return "Directive: prioritize title odds, but do not burn premium picks unless the player is a true weekly difference-maker."
    if "ASCENDING" in window:
        return "Directive: protect young core and picks; buy players who fit a 2-3 year window."
    if "WIN-NOW PRESSURE" in window:
        return "Directive: either push aggressively now or sell aging production before the value cliff."
    if "REBUILD" in window or score < 40:
        return "Directive: prioritize picks, young ascending players, and contract flexibility over short-term points."
    return "Directive: stay balanced; avoid all-in veteran buys unless the deal also preserves future flexibility."


def answer_team_strategy(question: str, owner_team_name: str = DEFAULT_OWNER_TEAM_NAME) -> dict:
    rows = _rows_for_owner(owner_team_name)
    team_future = _team_future_for_owner(owner_team_name)

    if not rows:
        return {"error": "No roster asset values found", "owner_team_name": owner_team_name}

    reasoning = build_team_reasoning(rows)

    base_summary = summarize_reasoning_as_text(reasoning)
    future_summary = _team_future_summary(team_future)
    directive = _team_future_directive(team_future)

    return {
        "answer_type": "team_strategy",
        "owner_team_name": owner_team_name,
        "summary": f"{future_summary}\n\n{directive}\n\n{base_summary}",
        "team_future_context": team_future,
        "reasoning": reasoning,
    }



def _target_recommendation_answer(question: str, owner_team_name: str) -> dict:
    parsed = parse_gm_question(question)
    plan = build_execution_plan(parsed)
    execution = execute_plan(plan, question=question, owner_team_name=owner_team_name)

    targets = execution.get("rank_targets") or []

    if not targets:
        return {
            "answer_type": "reasoned_gm_answer",
            "intent": parsed.intent,
            "decision": "TARGET_SCAN_INCOMPLETE",
            "summary": "I know the right target profile, but I could not rank actual targets yet.",
        }

    position = parsed.positions[0] if parsed.positions else "RB"
    summary = compose_target_recommendations(targets, position=position)

    return {
        "answer_type": "executed_target_recommendation",
        "intent": parsed.intent,
        "decision": "RANK_TARGETS",
        "summary": summary,
        "execution": execution,
    }


def _contract_best_value_answer(question: str, owner_team_name: str) -> dict:
    from gm_assistant.evidence.builders import calculate_points_per_dollar

    res = calculate_points_per_dollar(
        question=question,
        owner_team_name=owner_team_name,
    )

    rows = res.data or []
    rows = sorted(
        rows,
        key=lambda r: (
            float(r.get("points_per_dollar") or 0),
            float(r.get("ppg") or 0),
        ),
        reverse=True,
    )[:8]

    if not rows:
        return {
            "answer_type": "contract_best_value",
            "decision": "CONTRACT_BEST_VALUE",
            "summary": "I could not find enough contract data to rank your best values yet.",
        }

    lines = ["Your best contract values are the players giving you the most usable production per dollar."]

    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. {r.get('player')} ({r.get('pos')}) — "
            f"${r.get('salary')}/{r.get('years')} yrs, "
            f"{round(float(r.get('ppg') or 0), 2)} PPG, "
            f"{round(float(r.get('points_per_dollar') or 0), 2)} pts/$."
        )

    lines.append("\nLean: protect these values unless they are helping you consolidate into a clear weekly upgrade.")

    return {
        "answer_type": "contract_best_value",
        "decision": "CONTRACT_BEST_VALUE",
        "summary": "\n".join(lines),
        "players": rows,
    }



def _ai_intent_adapter(question: str, owner_team_name: str, understanding: dict) -> dict | None:
    from gm_assistant.intent_registry import answer_registered_intent

    q = (question or "").lower()
    if "qb" in q and "rb" in q and "trade" in q:
        understanding = dict(understanding)
        understanding["intent"] = "QB_SURPLUS_TO_RB_STRATEGY"

    return answer_registered_intent(question, owner_team_name, understanding)

def answer_gm_question(
    question: str,
    owner_team_name: str = DEFAULT_OWNER_TEAM_NAME,
    conversation_state: dict | None = None,
) -> dict:
    resolved_question = resolve_followup_question(question, conversation_state)

    q_lower = (resolved_question or "").lower()
    if any(x in q_lower for x in ["best value", "best contract", "best contracts", "value contract", "most efficient", "points per dollar", "ppd"]):
        return _contract_best_value_answer(resolved_question, owner_team_name)

    understanding = understand_question(resolved_question)

    adapted = _ai_intent_adapter(resolved_question, owner_team_name, understanding)
    if adapted:
        return adapted

    if understanding.get("intent") in {
        "ROOKIE_PLAYER_DECISION",
        "ROOKIE_PLAYER_COMPARISON",
        "ROOKIE_POSITION_VALUE",
    }:
        rookie_answer = answer_rookie_understanding_question(
            resolved_question,
            owner_team_name,
            understanding,
        )
        rookie_answer["conversation_state"] = update_conversation_state(
            conversation_state,
            question,
            rookie_answer,
            owner_team_name,
        )
        rookie_answer["resolved_question"] = resolved_question
        return rookie_answer

    if understanding.get("intent") in {
        "ROSTER_EXIT_DECISION",
        "QB_SURPLUS_TO_RB_STRATEGY",
    }:
        roster_answer = answer_roster_understanding_question(
            resolved_question,
            owner_team_name,
            understanding,
        )
        roster_answer["conversation_state"] = update_conversation_state(
            conversation_state,
            question,
            roster_answer,
            owner_team_name,
        )
        roster_answer["resolved_question"] = resolved_question
        return roster_answer

    brain_answer = GMBrainOrchestrator().think(resolved_question, owner_team_name)
    if brain_answer.get("handled"):
        brain_answer["conversation_state"] = update_conversation_state(
            conversation_state,
            question,
            brain_answer,
            owner_team_name,
        )
        brain_answer["resolved_question"] = resolved_question
        return brain_answer

    analysis = analyze_question(resolved_question)

    V2_INTENTS = {
        "change_team_goal",
        "player_decision",
        "contract_audit",
        "team_overview",
        "team_needs",
        "team_strengths",
        "league_context",
        "lineup_decision",
        "target_recommendations",
        "free_agent_targets",
        "rookie_pick_fit",
        "contract_value_ranking",
        "core_player_review",
        "win_now_player_ranking",
        "rb_archetype",
        "te_archetype",
        "pick_strategy",
        "qb_surplus_strategy",
        "trade_partner_search",
        "trade_package",
        "non_trade_paths",
        "safe_path",
        "aggressive_path",
        "first_move",
        "player_contract_fit",
        "strategy_tradeoff",
    }

    if analysis.intent in V2_INTENTS:

        capability_answer = run_capability(analysis.intent, resolved_question, owner_team_name)
        if capability_answer:
            capability_answer["conversation_state"] = update_conversation_state(
                conversation_state,
                question,
                capability_answer,
                owner_team_name,
            )
            capability_answer["resolved_question"] = resolved_question
            return capability_answer

        if analysis.intent in {"target_recommendations"}:
            target_answer = _target_recommendation_answer(resolved_question, owner_team_name)
            target_answer["conversation_state"] = update_conversation_state(
                conversation_state,
                question,
                target_answer,
                owner_team_name,
            )
            target_answer["resolved_question"] = resolved_question
            return target_answer

        if analysis.intent == "contract_value_ranking":
            contract_answer = _contract_value_answer(resolved_question, owner_team_name)
            contract_answer["conversation_state"] = update_conversation_state(
                conversation_state,
                question,
                contract_answer,
                owner_team_name,
            )
            contract_answer["resolved_question"] = resolved_question
            return contract_answer

        v2 = answer_reasoned_question(
            resolved_question,
            owner_team_name,
            evidence=None,
            answer_asset_question=answer_asset_question,
            answer_team_strategy=answer_team_strategy,
        )

        v2["conversation_state"] = update_conversation_state(
            conversation_state,
            question,
            v2,
            owner_team_name,
        )

        v2["resolved_question"] = resolved_question

        return v2

    planned_answer = answer_with_planner(
        resolved_question,
        owner_team_name,
    )

    if planned_answer:
        planned_answer["conversation_state"] = update_conversation_state(
            conversation_state,
            question,
            planned_answer,
            owner_team_name,
        )
        planned_answer["resolved_question"] = resolved_question
        return planned_answer

    evidence = build_evidence(resolved_question, owner_team_name)
    evidence["original_question"] = question
    evidence["conversation_state"] = conversation_state or {}

    if evidence and evidence.get("facts"):
        player = evidence.get("player")
        intent = evidence.get("intent")

        evidence_intents = {
            "cut_decision",
            "trade_decision",
            "hold_decision",
            "roster_liability",
            "next_move",
            "team_overview",
            "team_risk_analysis",
            "gm_takeover_plan",
            "overvalued_players",
            "undervalued_players",
            "contract_cleanup",
            "team_direction",
            "contention_check",
            "blind_spot",
            "trade_ideas",
            "acquire_player_plan",
            "bench_cut",
            "do_nothing_projection",
            "five_year_plan",
            "trade_partner",
            "most_valuable_player",
            "counterargument",
            "general_gm_question",
        }

        if player or intent in evidence_intents:
            answer = write_gm_answer(evidence)
            answer["conversation_state"] = update_conversation_state(
                conversation_state,
                question,
                answer,
                owner_team_name,
            )
            answer["resolved_question"] = resolved_question
            return answer

    decision_answer = answer_conversational_decision(
        question=resolved_question,
        owner_team_name=owner_team_name,
        team_goal=None,
    )

    if decision_answer:
        decision_answer["conversation_state"] = update_conversation_state(
            conversation_state,
            question,
            decision_answer,
            owner_team_name,
        )
        decision_answer["resolved_question"] = resolved_question
        return decision_answer

    team_future = _team_future_for_owner(owner_team_name)

    answer = orchestrate_gm_answer(
        resolved_question,
        owner_team_name,
        answer_asset_question=answer_asset_question,
        answer_team_strategy=answer_team_strategy,
        team_future_summary=_team_future_summary(team_future),
        team_directive=_team_future_directive(team_future),
        team_future=team_future,
    )

    answer["conversation_state"] = update_conversation_state(
        conversation_state,
        question,
        answer,
        owner_team_name,
    )
    answer["resolved_question"] = resolved_question
    return answer
