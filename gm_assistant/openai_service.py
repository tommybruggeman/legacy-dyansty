from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from auth import service_client
from gm_assistant.answer_packet import AnswerPacket, build_answer_packet_payload
from gm_assistant.brain_context import (
    AssistantAccessError,
    AssistantIdentity,
    _validate_membership,
    load_gm_brain_context,
    update_gm_memory,
)
from gm_assistant.conversation_state import (
    ConversationState,
    build_model_context_packet,
    durable_memory_fields_from_text,
)
from gm_assistant.calculations import CalculationPacket, build_calculation_packet_payload
from gm_assistant.decision import DecisionOutput, build_decision_packet
from gm_assistant.evidence import EvidencePacket, build_evidence_packet_payload
from gm_assistant.interpretation import InterpretedQuestion, build_interpretation_packet
from gm_assistant.objective import OwnerObjective, build_objective_packet
from gm_assistant.openai_reasoning import OpenAIReasoningService
from gm_assistant.planning import DecisionPlan, build_plan_packet
from gm_assistant.rendered_answer import RenderedAnswerValidation, validate_rendered_answer
from gm_assistant.rules import RulesEvaluation, build_rules_packet
from gm_assistant.validation import RecommendationValidation, build_validation_packet

try:
    from openai import APITimeoutError, OpenAI
except Exception:  # pragma: no cover - exercised when dependency is absent locally.
    APITimeoutError = TimeoutError
    OpenAI = None


DEFAULT_MODEL = "gpt-4.1-mini"
MAX_TOOL_ROUNDS = 4
MAX_ROWS = 40


class AssistantServiceError(RuntimeError):
    """Raised when the OpenAI assistant cannot produce a safe answer."""


class AssistantConfigurationError(AssistantServiceError):
    """Raised when local OpenAI configuration is missing."""


@dataclass(frozen=True)
class AssistantAnswer:
    text: str
    model: str
    tool_calls: List[str]
    request_ids: List[str]
    latency_ms: int
    raw_rendered_text: str | None = None
    rendered_validation: RenderedAnswerValidation | None = None
    reasoning_trace: Any | None = None


def answer_gm_question(
    question: str,
    identity: AssistantIdentity,
    conversation_history: List[Dict[str, str]] | None = None,
    conversation_state: ConversationState | None = None,
    interpreted_question: InterpretedQuestion | None = None,
    owner_objective: OwnerObjective | None = None,
    decision_plan: DecisionPlan | None = None,
    evidence_packet: EvidencePacket | None = None,
    rules_evaluation: RulesEvaluation | None = None,
    calculation_packet: CalculationPacket | None = None,
    decision_output: DecisionOutput | None = None,
    recommendation_validation: RecommendationValidation | None = None,
    answer_packet: AnswerPacket | None = None,
    request_context: Any | None = None,
    owner_intelligence_context: Any | None = None,
    league_owner_intelligence_context: Any | None = None,
    football_intelligence_context: Any | None = None,
    reasoning_provider: Any | None = None,
    *,
    sb: Any | None = None,
    client: Any | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> AssistantAnswer:
    if not question or not question.strip():
        raise AssistantServiceError("Please ask a question first.")

    sb = sb or service_client()
    _validate_membership(sb, identity)

    model = os.getenv("OPENAI_GM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if answer_packet and client is None:
        context = request_context or getattr(answer_packet, "_context", None)
        if context is None and evidence_packet is not None:
            context = getattr(evidence_packet, "context", None)
        if context is None:
            from gm_assistant.request_context import AssistantRequestContext
            context = AssistantRequestContext(
                user_id=identity.user_id,
                league_id=identity.league_id,
                league_team_id=identity.league_team_id,
                role="owner",
                current_season=0,
                requested_season=0,
                permission_scopes=(),
                team_name=identity.team_name,
                owner_name=identity.team_name,
            )
        reasoned = OpenAIReasoningService(reasoning_provider).answer(
            question=question,
            context=context,
            conversation_history=conversation_history or [],
            conversation_state=conversation_state,
            interpreted_question=interpreted_question,
            owner_objective=owner_objective,
            decision_plan=decision_plan,
            evidence_packet=evidence_packet,
            rules_evaluation=rules_evaluation,
            calculation_packet=calculation_packet,
            decision_output=decision_output,
            recommendation_validation=recommendation_validation,
            answer_packet=answer_packet,
            owner_intelligence_context=owner_intelligence_context,
            league_owner_intelligence_context=league_owner_intelligence_context,
            football_intelligence_context=football_intelligence_context,
        )
        if (
            getattr(reasoned.trace, "final_answer_source", None) == "openai"
            and reasoned.validation
            and reasoned.validation.ok
            and str(reasoned.text or "").strip()
        ):
            render_validation = RenderedAnswerValidation(
                validation_status="approved",
                original_rendered_text=reasoned.text,
                approved_text=reasoned.text,
                used_openai_response=True,
                used_deterministic_fallback=False,
                answer_packet_version=answer_packet.answer_version,
            )
        else:
            render_validation = validate_rendered_answer(answer_packet, reasoned.text)
        return AssistantAnswer(
            text=render_validation.approved_text,
            model=getattr(getattr(reasoned.provider_result, "trace", None), "model_label", None) or model,
            tool_calls=[],
            request_ids=[reasoned.trace.request_id],
            latency_ms=reasoned.trace.latency_ms,
            raw_rendered_text=reasoned.text,
            rendered_validation=render_validation,
            reasoning_trace=reasoned.trace,
        )
    try:
        client = client or _build_openai_client()
    except AssistantConfigurationError:
        if answer_packet:
            render_validation = validate_rendered_answer(answer_packet, None)
            return AssistantAnswer(
                text=render_validation.approved_text,
                model=model,
                tool_calls=[],
                request_ids=[],
                latency_ms=0,
                raw_rendered_text=None,
                rendered_validation=render_validation,
            )
        raise

    messages = _build_initial_messages(
        question,
        conversation_history,
        conversation_state,
        interpreted_question,
        owner_objective,
        decision_plan,
        evidence_packet,
        rules_evaluation,
        calculation_packet,
        decision_output,
        recommendation_validation,
        answer_packet,
    )
    request_ids: List[str] = []
    tool_calls: List[str] = []
    start = time.perf_counter()
    previous_response_id = None

    try:
        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=messages,
            tools=TOOL_SCHEMAS,
        )
        request_ids.append(_response_request_id(response))
        _log_openai_call(response=response, start=start, tool_count=0)

        for _round in range(max_tool_rounds + 1):
            calls = _extract_tool_calls(response)
            if not calls:
                text = _extract_response_text(response)
                if not text and not answer_packet:
                    raise AssistantServiceError("The assistant did not return an answer.")
                render_validation = validate_rendered_answer(answer_packet, text) if answer_packet else None
                approved_text = render_validation.approved_text if render_validation else text
                latency_ms = int((time.perf_counter() - start) * 1000)
                _store_conversation_memory(
                    sb=sb,
                    identity=identity,
                    question=question,
                    answer=approved_text,
                )
                return AssistantAnswer(
                    text=approved_text,
                    model=model,
                    tool_calls=tool_calls,
                    request_ids=[rid for rid in request_ids if rid],
                    latency_ms=latency_ms,
                    raw_rendered_text=text,
                    rendered_validation=render_validation,
                )

            if _round >= max_tool_rounds:
                raise AssistantServiceError("The assistant needed too many data lookups. Please ask a narrower question.")

            outputs = []
            for call in calls:
                tool_name = call["name"]
                tool_calls.append(tool_name)
                result = execute_assistant_tool(
                    tool_name,
                    call.get("arguments") or {},
                    identity=identity,
                    sb=sb,
                )
                outputs.append({
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(result, default=str),
                })

            previous_response_id = _response_id(response)
            response = client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                previous_response_id=previous_response_id,
                input=outputs,
                tools=TOOL_SCHEMAS,
            )
            request_ids.append(_response_request_id(response))

    except AssistantServiceError:
        raise
    except APITimeoutError as exc:
        if answer_packet:
            render_validation = validate_rendered_answer(answer_packet, None)
            return AssistantAnswer(
                text=render_validation.approved_text,
                model=model,
                tool_calls=tool_calls,
                request_ids=[rid for rid in request_ids if rid],
                latency_ms=int((time.perf_counter() - start) * 1000),
                raw_rendered_text=None,
                rendered_validation=render_validation,
            )
        raise AssistantServiceError("The assistant timed out while thinking. Please try again.") from exc
    except Exception as exc:
        if answer_packet:
            render_validation = validate_rendered_answer(answer_packet, None)
            return AssistantAnswer(
                text=render_validation.approved_text,
                model=model,
                tool_calls=tool_calls,
                request_ids=[rid for rid in request_ids if rid],
                latency_ms=int((time.perf_counter() - start) * 1000),
                raw_rendered_text=None,
                rendered_validation=render_validation,
            )
        raise AssistantServiceError("The assistant is temporarily unavailable. Please try again shortly.") from exc

    raise AssistantServiceError("The assistant could not complete the request.")


def execute_assistant_tool(
    name: str,
    arguments: Dict[str, Any],
    *,
    identity: AssistantIdentity,
    sb: Any,
) -> Dict[str, Any]:
    if name not in TOOL_FUNCTIONS:
        return {"ok": False, "error": "unknown_tool", "tool": name}

    if not isinstance(arguments, dict):
        return {"ok": False, "error": "malformed_arguments"}

    try:
        _validate_tool_scope(arguments, identity)
        _validate_membership(sb, identity)
        return TOOL_FUNCTIONS[name](sb, identity, arguments)
    except AssistantAccessError as exc:
        return {"ok": False, "error": "access_denied", "message": str(exc)}
    except ValueError as exc:
        return {"ok": False, "error": "malformed_arguments", "message": str(exc)}
    except Exception:
        return {"ok": False, "error": "tool_execution_failed"}


def _build_openai_client() -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AssistantConfigurationError("OPENAI_API_KEY is not configured.")
    if OpenAI is None:
        raise AssistantConfigurationError("The OpenAI Python package is not installed.")
    return OpenAI(api_key=api_key, timeout=float(os.getenv("OPENAI_GM_TIMEOUT_SECONDS", "30")))


def _build_initial_messages(
    question: str,
    conversation_history: List[Dict[str, str]] | None,
    conversation_state: ConversationState | None = None,
    interpreted_question: InterpretedQuestion | None = None,
    owner_objective: OwnerObjective | None = None,
    decision_plan: DecisionPlan | None = None,
    evidence_packet: EvidencePacket | None = None,
    rules_evaluation: RulesEvaluation | None = None,
    calculation_packet: CalculationPacket | None = None,
    decision_output: DecisionOutput | None = None,
    recommendation_validation: RecommendationValidation | None = None,
    answer_packet: AnswerPacket | None = None,
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    state_packet = build_model_context_packet(conversation_state)
    if state_packet:
        messages.append({
            "role": "user",
            "content": (
                "Structured conversation state for this request. "
                "Use it only as current discussion context, not as permanent league fact:\n"
                f"{json.dumps(state_packet, sort_keys=True, default=str)}"
            )[:3000],
        })
    interpretation_packet = build_interpretation_packet(interpreted_question)
    if interpretation_packet:
        messages.append({
            "role": "user",
            "content": (
                "Structured question interpretation for this request. "
                "Use it as scoped routing context only; do not treat unresolved references as facts:\n"
                f"{json.dumps(interpretation_packet, sort_keys=True, default=str)}"
            )[:3000],
        })
    objective_packet = build_objective_packet(owner_objective)
    if objective_packet:
        messages.append({
            "role": "user",
            "content": (
                "Structured owner objective for this request. "
                "Use it as scoped goal context only; do not treat it as a final recommendation:\n"
                f"{json.dumps(objective_packet, sort_keys=True, default=str)}"
            )[:3000],
        })
    plan_packet = build_plan_packet(decision_plan)
    if plan_packet:
        messages.append({
            "role": "user",
            "content": (
                "Structured decision plan for this request. "
                "Use it as bounded execution guidance only; do not claim the future evidence has already been retrieved:\n"
                f"{json.dumps(plan_packet, sort_keys=True, default=str)}"
            )[:3000],
        })
    evidence_payload = build_evidence_packet_payload(evidence_packet)
    if evidence_payload:
        messages.append({
            "role": "user",
            "content": (
                "Structured evidence packet for this request. "
                "Use only verified facts as evidence; clearly label incomplete, unavailable, or reduced-mode facts:\n"
                f"{json.dumps(evidence_payload, sort_keys=True, default=str)}"
            )[:4000],
        })
    rules_packet = build_rules_packet(rules_evaluation)
    if rules_packet:
        messages.append({
            "role": "user",
            "content": (
                "Structured rules evaluation for this request. "
                "Treat verified illegal results as illegal, conditional results as conditional, and unverifiable results as unconfirmed:\n"
                f"{json.dumps(rules_packet, sort_keys=True, default=str)}"
            )[:3000],
        })
    calculation_payload = build_calculation_packet_payload(calculation_packet)
    if calculation_payload:
        messages.append({
            "role": "user",
            "content": (
                "Structured deterministic calculations for this request. "
                "Preserve exact numbers, label estimates as estimates, and do not turn calculations into unsupported recommendations:\n"
                f"{json.dumps(calculation_payload, sort_keys=True, default=str)}"
            )[:3500],
        })
    decision_payload = build_decision_packet(decision_output)
    if decision_payload:
        messages.append({
            "role": "user",
            "content": (
                "Structured deterministic decision for this request. "
                "This decision is authoritative: do not change the action, invent a different recommendation, "
                "describe an illegal move as legal, remove stated conditions, or turn insufficient information into confidence. "
                "Explain it naturally using the supplied facts, rules, and calculations:\n"
                f"{json.dumps(decision_payload, sort_keys=True, default=str)}"
            )[:3500],
        })
    validation_payload = build_validation_packet(recommendation_validation)
    if validation_payload:
        messages.append({
            "role": "user",
            "content": (
                "Structured deterministic recommendation validation for this request. "
                "This validation is authoritative over the decision packet for whether the recommendation may be explained or treated as actionable. "
                "Only explain the deterministic Stage 9 recommendation when approved_for_explanation is true. "
                "Never present a rejected, blocked, or failed validation as supported. "
                "Do not invent a replacement recommendation or fallback judgment. "
                "Preserve warnings, conditions, confidence_after_validation, and approved_for_action exactly:\n"
                f"{json.dumps(validation_payload, sort_keys=True, default=str)}"
            )[:3500],
        })
    answer_payload = build_answer_packet_payload(answer_packet)
    if answer_payload:
        messages.append({
            "role": "user",
            "content": (
                "Structured deterministic answer contract for this request. "
                "This is the highest-priority approved answer material. "
                "Answer only from this contract and the supplied tool results. "
                "Preserve the direct answer, recommendation, legality, actionability, exact-versus-estimated labels, conditions, warnings, limitations, and confidence. "
                "Do not make any forbidden_claims, do not create replacement recommendations, do not introduce unsupported players, picks, teams, values, rules, or projections, and do not claim any action was executed. "
                "Use concise natural language and do not expose internal packet names to the user:\n"
                f"{json.dumps(answer_payload, sort_keys=True, default=str)}"
            )[:4500],
        })
    for item in (conversation_history or [])[-8:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": str(content)[:2000]})
    messages.append({"role": "user", "content": question})
    return messages


def _validate_tool_scope(arguments: Dict[str, Any], identity: AssistantIdentity) -> None:
    league_id = arguments.get("league_id")
    league_team_id = arguments.get("league_team_id")
    if league_id and str(league_id) != str(identity.league_id):
        raise AssistantAccessError("Tool request crossed league scope.")
    if league_team_id and str(league_team_id) != str(identity.league_team_id):
        raise AssistantAccessError("Tool request crossed team scope.")


def _tool_current_user_context(sb: Any, identity: AssistantIdentity, _args: Dict[str, Any]) -> Dict[str, Any]:
    brain = load_gm_brain_context(
        identity.team_name,
        user_id=identity.user_id,
        league_id=identity.league_id,
        league_team_id=identity.league_team_id,
        sb=sb,
    )
    return {
        "ok": True,
        "user_id": identity.user_id,
        "league_id": identity.league_id,
        "league_team_id": identity.league_team_id,
        "team_name": brain.get("team_name"),
        "membership_role": brain.get("membership_role"),
        "context_summary": brain.get("context_summary"),
    }


def _tool_my_team_brain(sb: Any, identity: AssistantIdentity, _args: Dict[str, Any]) -> Dict[str, Any]:
    rows = _rows(
        sb.table("team_brain")
        .select("*")
        .eq("league_id", identity.league_id)
        .eq("league_team_id", identity.league_team_id)
        .limit(1)
    )
    return {"ok": True, "team_brain": _compact_row(_one(rows))}


def _tool_league_brain(sb: Any, identity: AssistantIdentity, _args: Dict[str, Any]) -> Dict[str, Any]:
    rows = _rows(
        sb.table("league_brain")
        .select("*")
        .eq("league_id", identity.league_id)
        .limit(1)
    )
    return {"ok": True, "league_brain": _compact_row(_one(rows))}


def _tool_my_roster(sb: Any, identity: AssistantIdentity, _args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "roster": _player_rows_for_team(sb, identity.league_id, identity.league_team_id),
    }


def _tool_team_roster(sb: Any, identity: AssistantIdentity, args: Dict[str, Any]) -> Dict[str, Any]:
    target_team_id = args.get("target_league_team_id")
    target_team_name = args.get("team_name")
    team = _resolve_league_team(sb, identity.league_id, target_team_id, target_team_name)
    if not team:
        return {"ok": False, "error": "team_not_found"}
    return {
        "ok": True,
        "team": _compact_row(team),
        "roster": _player_rows_for_team(sb, identity.league_id, team.get("id")),
    }


def _tool_player_details(sb: Any, identity: AssistantIdentity, args: Dict[str, Any]) -> Dict[str, Any]:
    player_name = _required_text(args, "player_name")
    details = _find_player_rows(sb, identity.league_id, player_name)
    return {"ok": True, "player_name": player_name, "matches": details[:8]}


def _tool_player_contract(sb: Any, identity: AssistantIdentity, args: Dict[str, Any]) -> Dict[str, Any]:
    player_name = _required_text(args, "player_name")
    contract_rows = _rows(
        sb.table("contracts")
        .select("id,league_id,owner_name,player_name,player_position,contract_years_left,contract_total_years,salary,sleeper_player_id,contract_tag")
        .eq("league_id", identity.league_id)
    )
    matches = [
        _compact_row(row)
        for row in contract_rows
        if _contains(row.get("player_name"), player_name)
    ][:8]
    return {"ok": True, "player_name": player_name, "contracts": matches}


def _tool_league_team_rankings(sb: Any, identity: AssistantIdentity, _args: Dict[str, Any]) -> Dict[str, Any]:
    rows = _rows(sb.table("team_brain").select("*").eq("league_id", identity.league_id))
    ranked = sorted(
        (_compact_row(row) for row in rows),
        key=lambda row: (
            float(row.get("championship_window_score") or row.get("overall_score") or row.get("win_now_score") or 0),
            len(row.get("core_players") or []),
        ),
        reverse=True,
    )
    return {"ok": True, "teams": ranked[:12]}


def _tool_compare_teams(sb: Any, identity: AssistantIdentity, args: Dict[str, Any]) -> Dict[str, Any]:
    other = _resolve_league_team(sb, identity.league_id, args.get("target_league_team_id"), args.get("team_name"))
    if not other:
        return {"ok": False, "error": "team_not_found"}
    mine = _one(_rows(sb.table("team_brain").select("*").eq("league_id", identity.league_id).eq("league_team_id", identity.league_team_id).limit(1)))
    theirs = _one(_rows(sb.table("team_brain").select("*").eq("league_id", identity.league_id).eq("league_team_id", other.get("id")).limit(1)))
    return {"ok": True, "my_team": _compact_row(mine), "other_team": _compact_row(theirs)}


def _tool_trade_fit_pairs(sb: Any, identity: AssistantIdentity, _args: Dict[str, Any]) -> Dict[str, Any]:
    league = _one(_rows(sb.table("league_brain").select("league_id,trade_fits").eq("league_id", identity.league_id).limit(1)))
    fits = league.get("trade_fits") or []
    relevant = [
        fit for fit in fits
        if identity.team_name in {fit.get("team_a"), fit.get("team_b")}
    ]
    return {"ok": True, "trade_fits": relevant[:12]}


def _tool_cap_summary(sb: Any, identity: AssistantIdentity, args: Dict[str, Any]) -> Dict[str, Any]:
    team = _resolve_league_team(sb, identity.league_id, args.get("target_league_team_id"), args.get("team_name"))
    owner_names = {identity.team_name}
    if team:
        owner_names.update(x for x in [team.get("owner_name"), team.get("team_name")] if x)
    cap_rows = _rows(sb.table("v_team_caps").select("*").eq("league_id", identity.league_id))
    scoped = [
        _compact_row(row)
        for row in cap_rows
        if _row_matches_league(row, identity.league_id)
        and (not team or row.get("owner_name") in owner_names or row.get("team_name") in owner_names)
    ]
    if not team:
        scoped = [_compact_row(row) for row in cap_rows if _row_matches_league(row, identity.league_id)]
    return {"ok": True, "cap_summary": scoped[:12]}


def _tool_recent_transactions(sb: Any, identity: AssistantIdentity, args: Dict[str, Any]) -> Dict[str, Any]:
    limit = min(int(args.get("limit") or 10), 25)
    rows = _rows(
        sb.table("transactions_enriched")
        .select("*")
        .eq("league_id", identity.league_id)
        .limit(limit)
    )
    return {"ok": True, "transactions": [_compact_row(row) for row in rows[:limit]]}


def _player_rows_for_team(sb: Any, league_id: str, league_team_id: str) -> List[Dict[str, Any]]:
    rows = _rows(
        sb.table("player_strategic_profiles")
        .select("*")
        .eq("league_id", league_id)
        .eq("league_team_id", league_team_id)
    )
    relative = {
        str(row.get("sleeper_id")): row
        for row in _rows(
            sb.table("league_relative_player_values")
            .select("*")
            .eq("league_id", league_id)
            .eq("league_team_id", league_team_id)
        )
        if row.get("sleeper_id") is not None
    }
    out = []
    for row in rows[:MAX_ROWS]:
        merged = {**row, **{
            "league_value_tier": relative.get(str(row.get("sleeper_id")), {}).get("league_value_tier"),
            "overall_percentile": relative.get(str(row.get("sleeper_id")), {}).get("overall_percentile"),
        }}
        out.append(_compact_row(merged))
    return out


def _find_player_rows(sb: Any, league_id: str, player_name: str) -> List[Dict[str, Any]]:
    tables = [
        "player_strategic_profiles",
        "league_relative_player_values",
        "contracts",
    ]
    matches = []
    for table in tables:
        for row in _rows(sb.table(table).select("*").eq("league_id", league_id)):
            if _contains(row.get("player_name"), player_name):
                item = _compact_row(row)
                item["source_table"] = table
                matches.append(item)
    return matches


def _resolve_league_team(sb: Any, league_id: str, team_id: Any = None, team_name: Any = None) -> Dict[str, Any] | None:
    query = sb.table("league_teams").select("*").eq("league_id", league_id)
    if team_id:
        rows = _rows(query.eq("id", team_id).limit(1))
        return _one(rows)

    rows = _rows(query)
    if team_name:
        for row in rows:
            if _contains(row.get("team_name"), str(team_name)) or _contains(row.get("owner_name"), str(team_name)):
                return row
    return None


def _rows(query: Any) -> List[Dict[str, Any]]:
    return query.execute().data or []


def _one(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    for row in rows:
        return row or {}
    return {}


def _required_text(args: Dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _contains(value: Any, needle: str) -> bool:
    return str(needle).strip().lower() in str(value or "").strip().lower()


def _row_matches_league(row: Dict[str, Any], league_id: str) -> bool:
    return str(row.get("league_id")) == str(league_id)


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "id", "league_id", "league_team_id", "team_name", "owner_name", "player_name",
        "player_position", "position", "pos", "sleeper_id", "sleeper_player_id",
        "salary", "contract_years_left", "contract_total_years", "years", "contract_tag",
        "contract_flag", "strategic_label", "action", "rationale", "team_direction",
        "position_strengths", "position_needs", "core_players", "trade_candidates",
        "contract_problems", "summary", "trade_fits", "league_value_tier",
        "overall_percentile", "win_now_score", "future_score", "overall_score",
        "championship_window_score", "cap_space", "total_salary", "projected_points",
        "created_at", "ts", "type", "description",
    }
    return {key: value for key, value in row.items() if key in allowed and value is not None}


def _extract_tool_calls(response: Any) -> List[Dict[str, Any]]:
    calls = []
    for item in _response_output(response):
        item_type = _field(item, "type")
        if item_type != "function_call":
            continue
        raw_args = _field(item, "arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            args = {"_malformed": raw_args}
        calls.append({
            "call_id": _field(item, "call_id") or _field(item, "id"),
            "name": _field(item, "name"),
            "arguments": args,
        })
    return calls


def _extract_response_text(response: Any) -> str:
    text = _field(response, "output_text")
    if text:
        return str(text).strip()
    chunks = []
    for item in _response_output(response):
        if _field(item, "type") == "message":
            for content in _field(item, "content") or []:
                if _field(content, "type") in {"output_text", "text"}:
                    chunks.append(str(_field(content, "text") or ""))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _response_output(response: Any) -> List[Any]:
    return _field(response, "output") or []


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _response_id(response: Any) -> str | None:
    return _field(response, "id")


def _response_request_id(response: Any) -> str:
    return str(_field(response, "_request_id") or _field(response, "request_id") or _field(response, "id") or "")


def _log_openai_call(*, response: Any, start: float, tool_count: int) -> None:
    latency_ms = int((time.perf_counter() - start) * 1000)
    print(
        "GM_OPENAI "
        f"request_id={_response_request_id(response) or 'none'} "
        f"latency_ms={latency_ms} "
        f"tool_count={tool_count}",
        flush=True,
    )


def _store_conversation_memory(
    *,
    sb: Any,
    identity: AssistantIdentity,
    question: str,
    answer: str,
) -> None:
    memory_fields = durable_memory_fields_from_text(question)
    if not memory_fields:
        return

    try:
        update_gm_memory(
            team_name=identity.team_name,
            user_id=identity.user_id,
            league_id=identity.league_id,
            league_team_id=identity.league_team_id,
            conversation_summary=(
                f"Explicit owner preference captured from user statement: {question[:500]}"
            ),
            **memory_fields,
            sb=sb,
        )
    except Exception:
        pass


SYSTEM_PROMPT = """
You are Coach Condor, a dynasty fantasy football general manager assistant.
Answer the exact question asked. Use supplied league data and tool results rather than inventing facts.
Clearly distinguish facts from recommendations. Say when data is unavailable.
Consider contracts, cap, roster construction, team window, positional depth, league-relative value, and trade partners.
Do not repeat a generic contention plan unless that directly answers the question.
Be concise by default, but explain reasoning when useful.
Never reveal another league's data. Never claim a player is on a roster unless confirmed by a tool result.
Do not fabricate contracts, rankings, trades, or transactions.
When a structured deterministic decision packet is supplied, treat it as authoritative. Do not override its action, legality, conditions, confidence, or insufficient-information status.
When a structured deterministic validation packet is supplied, it is the final gate. Only explain validated recommendations when approved_for_explanation is true, never describe approved_for_action=false as executable, and do not invent a replacement recommendation after rejection or blocking.
When a structured deterministic answer contract is supplied, it has highest priority. Answer only from that approved contract, preserve forbidden claims, limitations, conditions, and actionability, and do not expose internal packet names to the user.
""".strip()


def _schema(name: str, description: str, properties: Dict[str, Any] | None = None, required: List[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }


SCOPE_PROPS = {
    "league_id": {"type": "string", "description": "Optional echo of the authenticated league_id."},
    "league_team_id": {"type": "string", "description": "Optional echo of the authenticated league_team_id."},
}


TOOL_SCHEMAS = [
    _schema("get_current_user_context", "Get the authenticated user's team, league, and assistant context.", SCOPE_PROPS, ["league_id", "league_team_id"]),
    _schema("get_my_team_brain", "Get the authenticated user's scoped team brain.", SCOPE_PROPS, ["league_id", "league_team_id"]),
    _schema("get_league_brain", "Get the current league brain summary and trade fits.", {"league_id": SCOPE_PROPS["league_id"]}, ["league_id"]),
    _schema("get_my_roster", "Get the authenticated user's scoped roster and player strategy rows.", SCOPE_PROPS, ["league_id", "league_team_id"]),
    _schema("get_team_roster", "Get another team roster inside the same league.", {**SCOPE_PROPS, "target_league_team_id": {"type": "string"}, "team_name": {"type": "string"}}, ["league_id"]),
    _schema("get_player_details", "Find player strategic and league-relative details in this league.", {**SCOPE_PROPS, "player_name": {"type": "string"}}, ["league_id", "player_name"]),
    _schema("get_player_contract", "Find a player's contract in this league.", {**SCOPE_PROPS, "player_name": {"type": "string"}}, ["league_id", "player_name"]),
    _schema("get_league_team_rankings", "Rank teams using scoped team brain rows.", {"league_id": SCOPE_PROPS["league_id"]}, ["league_id"]),
    _schema("compare_teams", "Compare the user's team to another league team.", {**SCOPE_PROPS, "target_league_team_id": {"type": "string"}, "team_name": {"type": "string"}}, ["league_id", "league_team_id"]),
    _schema("get_trade_fit_pairs", "Get trade-fit pairs relevant to the user's team.", SCOPE_PROPS, ["league_id", "league_team_id"]),
    _schema("get_cap_summary", "Get cap summary rows for this league or a specific team.", {**SCOPE_PROPS, "target_league_team_id": {"type": "string"}, "team_name": {"type": "string"}}, ["league_id"]),
    _schema("get_recent_transactions", "Get recent transactions for this league.", {"league_id": SCOPE_PROPS["league_id"], "limit": {"type": "integer", "minimum": 1, "maximum": 25}}, ["league_id"]),
]


TOOL_FUNCTIONS: Dict[str, Callable[[Any, AssistantIdentity, Dict[str, Any]], Dict[str, Any]]] = {
    "get_current_user_context": _tool_current_user_context,
    "get_my_team_brain": _tool_my_team_brain,
    "get_league_brain": _tool_league_brain,
    "get_my_roster": _tool_my_roster,
    "get_team_roster": _tool_team_roster,
    "get_player_details": _tool_player_details,
    "get_player_contract": _tool_player_contract,
    "get_league_team_rankings": _tool_league_team_rankings,
    "compare_teams": _tool_compare_teams,
    "get_trade_fit_pairs": _tool_trade_fit_pairs,
    "get_cap_summary": _tool_cap_summary,
    "get_recent_transactions": _tool_recent_transactions,
}
