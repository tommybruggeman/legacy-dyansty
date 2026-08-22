from __future__ import annotations

from gm_assistant.reasoning.analyzer import analyze_question
from gm_assistant.reasoning.evidence import collect_evidence
from gm_assistant.reasoning.models import EvidenceBundle
from gm_assistant.reasoning.state import get_brain_state, apply_analysis_to_state
from gm_assistant.reasoning.synthesizer import make_decision
from gm_assistant.reasoning.writer import write_decision


def answer_reasoned_question(
    question: str,
    owner_team_name: str,
    evidence: EvidenceBundle | None = None,
    *,
    answer_asset_question=None,
    answer_team_strategy=None,
) -> dict:
    analysis = analyze_question(question)

    state = get_brain_state(owner_team_name)
    state = apply_analysis_to_state(state, analysis)

    if evidence is None:
        evidence = collect_evidence(
            analysis,
            owner_team_name,
            answer_asset_question=answer_asset_question,
            answer_team_strategy=answer_team_strategy,
        )

    decision = make_decision(analysis, state, evidence)
    summary = write_decision(decision)

    return {
        "answer_type": "reasoned_gm_answer",
        "intent": analysis.intent,
        "decision": decision.action,
        "confidence": decision.confidence,
        "summary": summary,
        "analysis": analysis,
        "state": state,
        "evidence": evidence,
    }
