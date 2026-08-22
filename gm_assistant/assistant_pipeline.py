from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from gm_assistant.answer_packet import AnswerPacket, build_answer_packet
from gm_assistant.calculations import CalculationPacket, build_calculation_packet
from gm_assistant.conversation_state import (
    ConversationState,
    create_conversation_state,
    infer_conversation_state_update_from_text,
    update_conversation_state,
)
from gm_assistant.decision import DecisionOutput, build_decision_output
from gm_assistant.evidence import EvidencePacket, EvidenceRetrievalProvider, build_evidence_packet
from gm_assistant.football_intelligence import FootballIntelligenceContext, FootballIntelligenceService, unavailable_football_intelligence_context
from gm_assistant.interpretation import (
    InterpretedQuestion,
    conversation_update_from_interpretation,
    interpret_question,
)
from gm_assistant.league_owner_intelligence import LeagueOwnerIntelligenceContext, LeagueOwnerIntelligenceService, unavailable_league_owner_intelligence_context
from gm_assistant.objective import (
    OwnerObjective,
    build_owner_objective,
    conversation_update_from_objective,
)
from gm_assistant.owner_intelligence import OwnerIntelligenceContext, OwnerIntelligenceService, unavailable_owner_intelligence_context
from gm_assistant.planning import DecisionPlan, build_decision_plan
from gm_assistant.rendered_answer import RenderedAnswerValidation, validate_rendered_answer
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.rules import RulesEvaluation, evaluate_rules
from gm_assistant.validation import RecommendationValidation, validate_recommendation


@dataclass(frozen=True)
class AssistantPipelineResult:
    context: AssistantRequestContext
    conversation_state: ConversationState
    owner_intelligence_context: OwnerIntelligenceContext
    league_owner_intelligence_context: LeagueOwnerIntelligenceContext
    football_intelligence_context: FootballIntelligenceContext
    interpreted_question: InterpretedQuestion
    owner_objective: OwnerObjective
    decision_plan: DecisionPlan
    evidence_packet: EvidencePacket
    rules_evaluation: RulesEvaluation
    calculation_packet: CalculationPacket
    decision_output: DecisionOutput
    recommendation_validation: RecommendationValidation
    answer_packet: AnswerPacket
    rendered_validation: RenderedAnswerValidation
    displayed_answer: str
    prompt_size_audit: dict[str, int]


def run_assistant_pipeline(
    *,
    context: AssistantRequestContext,
    question: str,
    retrieval_provider: EvidenceRetrievalProvider,
    conversation_state: ConversationState | None = None,
    rendered_text: str | None = None,
    owner_preferences: dict[str, Any] | None = None,
    team_context: dict[str, Any] | None = None,
    interpreter_sb: Any | None = None,
) -> AssistantPipelineResult:
    """Run the production Stage 1-12 assistant path without Streamlit or network rendering."""
    state = conversation_state or create_conversation_state(context, context.conversation_id)
    message_id = _message_id(context, question)

    state = update_conversation_state(
        state,
        infer_conversation_state_update_from_text(question, message_id=message_id),
    )
    interpreted_question = interpret_question(question, context, state, sb=interpreter_sb)
    state = update_conversation_state(
        state,
        conversation_update_from_interpretation(interpreted_question, message_id=message_id),
    )
    if context.user_id and context.league_id and context.league_team_id:
        owner_intelligence_context = OwnerIntelligenceService().get_context(
            context=context,
            conversation_state=state,
            owner_preferences=owner_preferences or {},
            current_message=question,
        )
        try:
            league_owner_intelligence_context = LeagueOwnerIntelligenceService(retrieval_provider.sb).get_context(context=context)
        except Exception as exc:
            league_owner_intelligence_context = unavailable_league_owner_intelligence_context(context, f"League Owner Intelligence unavailable: {type(exc).__name__}")
        try:
            football_intelligence_context = FootballIntelligenceService(retrieval_provider.sb).get_context(
                context=context,
                owner_goal=owner_intelligence_context.strategy_state.strategic_goal.value if owner_intelligence_context.strategy_state.strategic_goal else None,
            )
        except Exception as exc:
            football_intelligence_context = unavailable_football_intelligence_context(context, f"Football Intelligence unavailable: {type(exc).__name__}")
    else:
        owner_intelligence_context = unavailable_owner_intelligence_context(context, "Owner Intelligence requires authenticated user, league, and team scope.")
        league_owner_intelligence_context = unavailable_league_owner_intelligence_context(context, "League Owner Intelligence requires authenticated user, league, and team scope.")
        football_intelligence_context = unavailable_football_intelligence_context(context, "Football Intelligence requires authenticated user, league, and team scope.")
    owner_preferences_for_objective = {
        **(owner_preferences or {}),
        **owner_intelligence_context.to_legacy_owner_preferences(),
    }

    owner_objective = build_owner_objective(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted_question,
        owner_preferences=owner_preferences_for_objective,
        team_context=team_context or {},
    )
    state = update_conversation_state(
        state,
        conversation_update_from_objective(owner_objective, message_id=message_id),
    )

    decision_plan = build_decision_plan(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
    )
    evidence_packet = build_evidence_packet(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        retrieval_provider=retrieval_provider,
    )
    rules_evaluation = evaluate_rules(
        context=context,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
    )
    calculation_packet = build_calculation_packet(
        context=context,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        rules_evaluation=rules_evaluation,
    )
    decision_output = build_decision_output(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        rules_evaluation=rules_evaluation,
        calculation_packet=calculation_packet,
    )
    recommendation_validation = validate_recommendation(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        rules_evaluation=rules_evaluation,
        calculation_packet=calculation_packet,
        decision_output=decision_output,
    )
    answer_packet = build_answer_packet(
        context=context,
        conversation_state=state,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        rules_evaluation=rules_evaluation,
        calculation_packet=calculation_packet,
        decision_output=decision_output,
        recommendation_validation=recommendation_validation,
        league_owner_intelligence_context=league_owner_intelligence_context,
        football_intelligence_context=football_intelligence_context,
    )
    rendered_validation = validate_rendered_answer(answer_packet, rendered_text)
    return AssistantPipelineResult(
        context=context,
        conversation_state=state,
        owner_intelligence_context=owner_intelligence_context,
        league_owner_intelligence_context=league_owner_intelligence_context,
        football_intelligence_context=football_intelligence_context,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        rules_evaluation=rules_evaluation,
        calculation_packet=calculation_packet,
        decision_output=decision_output,
        recommendation_validation=recommendation_validation,
        answer_packet=answer_packet,
        rendered_validation=rendered_validation,
        displayed_answer=rendered_validation.approved_text,
        prompt_size_audit=_prompt_size_audit(
            interpreted_question,
            owner_objective,
            owner_intelligence_context,
            league_owner_intelligence_context,
            football_intelligence_context,
            decision_plan,
            evidence_packet,
            rules_evaluation,
            calculation_packet,
            decision_output,
            recommendation_validation,
            answer_packet,
        ),
    )


def _message_id(context: AssistantRequestContext, question: str) -> str:
    if context.message_id:
        return context.message_id
    raw = f"{context.user_id}:{context.league_id}:{context.league_team_id}:{context.conversation_id}:{question}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _prompt_size_audit(*packets: Any) -> dict[str, int]:
    sizes = {}
    total_stage_1_to_10 = 0
    for packet in packets[:-1]:
        size = len(repr(packet).encode("utf-8"))
        sizes[type(packet).__name__] = size
        total_stage_1_to_10 += size
    answer_packet_size = len(repr(packets[-1]).encode("utf-8"))
    sizes["stage_1_to_10_combined"] = total_stage_1_to_10
    sizes["AnswerPacket"] = answer_packet_size
    sizes["approx_final_context_with_answer_packet"] = total_stage_1_to_10 + answer_packet_size
    return sizes
