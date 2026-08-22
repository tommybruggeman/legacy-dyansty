from __future__ import annotations

from gm_assistant.skills.base import SkillResult


def answer_question_recommendation(question: str, owner_team_name: str, understanding: dict) -> dict:
    summary = """The question I would be asking is:

**How do I turn my QB/WR leverage into RB or TE help without weakening my championship window?**

The three follow-up questions I would ask next:
1. Which QB or WR can I move without damaging my starting lineup?
2. Which expensive contracts are creating the most cap pressure?
3. What RB/TE upgrade is actually worth paying for?

My GM read: your next edge is not random churn. It is converting surplus value into a cleaner weekly lineup."""
    return SkillResult(
        decision="QUESTION_RECOMMENDATION",
        summary=summary,
        confidence=0.86,
    ).to_dict()


def answer_sell_high(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_roster_exit_decision

    base = answer_roster_exit_decision(question, owner_team_name, understanding)
    summary = base.get("summary", "")

    summary = summary.replace(
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My sell-high board is based on where market value, contract pressure, age risk, and production risk may be misaligned."
    )

    summary = summary.replace(
        "Lean: start market checks with the top names, but do not dump them blindly.",
        "Lean: these are market-check names, not automatic sells."
    )

    base["decision"] = "SELL_HIGH"
    base["summary"] = summary
    return base


def answer_contract_audit(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_roster_exit_decision

    base = answer_roster_exit_decision(question, owner_team_name, understanding)
    summary = base.get("summary", "")

    summary = summary.replace(
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My overpaid/contract-pressure board is based on salary, years, contract risk, production, and dynasty value."
    )

    summary = summary.replace(
        "Lean: start market checks with the top names, but do not dump them blindly.",
        "Lean: bad contract does not always mean cut. Shop real-name value first, then churn replaceable contracts."
    )

    base["decision"] = "CONTRACT_AUDIT"
    base["summary"] = summary
    return base


def answer_trade_return_value(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.pipeline.models import EvidencePack
    from gm_assistant.pipeline.reasoning.trade_reasoner import reason_trade_return
    from gm_assistant.pipeline.response.gm_writer import write_gm_response

    plan_context = understanding.get("plan_context") or {}
    loaded = plan_context.get("loaded") or {}

    evidence = EvidencePack(
        question=question,
        owner_team_name=owner_team_name,
        understanding=understanding,
        player=loaded.get("player") or loaded.get("target_player"),
        roster=loaded.get("roster") or [],
        team_context=loaded.get("team_context") or {},
        notes=plan_context.get("warnings") or [],
    )

    reasoning = reason_trade_return(evidence)
    summary = write_gm_response(reasoning)

    return {
        "decision": reasoning.decision,
        "summary": summary,
        "confidence": reasoning.confidence,
        "evidence": reasoning.evidence,
        "reasoning": reasoning.to_dict(),
        "pipeline": "planner_v1_evidence_reasoning_response",
    }
