from __future__ import annotations

import re
from typing import Any

from gm_assistant.owner_intelligence.models import (
    ConfirmationState,
    CorrectnessDispute,
    OwnerFeedback,
    OwnerIntelligenceLineage,
    OwnerMemoryScope,
    OwnerPreference,
    OwnerPreferenceCategory,
    OwnerPreferenceSource,
    OwnerPreferenceStatus,
)
from gm_assistant.request_context import AssistantRequestContext


AMBIGUOUS_MARKERS = ("maybe", "might", "could", "feels", "wonder", "thinking about")
TEMPORARY_MARKERS = ("for this trade", "for this scenario", "today", "right now", "this answer")
DURABLE_MARKERS = ("going forward", "for this league", "always", "never", "do not recommend", "don't recommend", "keep answers", "i am ", "i want ")


def normalize_owner_preferences_from_text(text: Any, context: AssistantRequestContext) -> tuple[list[OwnerPreference], list[str]]:
    raw = str(text or "").strip()
    normalized = _normalize(raw)
    if not normalized:
        return [], []
    warnings: list[str] = []
    conversation_only = any(marker in normalized for marker in TEMPORARY_MARKERS)
    ambiguous = any(marker in normalized for marker in AMBIGUOUS_MARKERS)
    if ambiguous and not _is_explicit_hard_constraint(normalized):
        return [], ["ambiguous_preference_not_promoted"]

    source = OwnerPreferenceSource.CURRENT_CONVERSATION.value if conversation_only else OwnerPreferenceSource.EXPLICIT_USER_STATEMENT.value
    status = OwnerPreferenceStatus.CONVERSATION_ONLY.value if conversation_only else OwnerPreferenceStatus.ACTIVE.value
    prefs: list[OwnerPreference] = []

    goal = _strategic_goal(normalized)
    if goal:
        confirmation = ConfirmationState.REQUIRED.value if goal in {"rebuild", "contend"} and not _has_durable_signal(normalized) else ConfirmationState.NOT_REQUIRED.value
        prefs.append(_preference(context, OwnerPreferenceCategory.STRATEGIC_GOAL.value, goal, OwnerMemoryScope.TEAM.value, source, status, raw, confirmation))

    horizon = _time_horizon(normalized)
    if horizon:
        prefs.append(_preference(context, OwnerPreferenceCategory.TIME_HORIZON.value, horizon, OwnerMemoryScope.LEAGUE.value, source, status, raw))

    for asset_value in _asset_preferences(normalized):
        prefs.append(_preference(context, OwnerPreferenceCategory.ASSET_PREFERENCE.value, asset_value, OwnerMemoryScope.LEAGUE.value, source, status, raw))

    risk = _risk_preference(normalized)
    if risk:
        confirmation = ConfirmationState.REQUIRED.value if risk == "aggressive" and not _has_durable_signal(normalized) else ConfirmationState.NOT_REQUIRED.value
        prefs.append(_preference(context, OwnerPreferenceCategory.RISK_TOLERANCE.value, risk, OwnerMemoryScope.USER_GLOBAL.value, source, status, raw, confirmation))

    for communication in _communication_preferences(normalized):
        scope = OwnerMemoryScope.USER_GLOBAL.value
        prefs.append(_preference(context, OwnerPreferenceCategory.COMMUNICATION_STYLE.value, communication, scope, source, status, raw))

    for decision in _decision_preferences(normalized):
        prefs.append(_preference(context, OwnerPreferenceCategory.DECISION_STYLE.value, decision, OwnerMemoryScope.USER_GLOBAL.value, source, status, raw))

    hard = _hard_constraint(normalized)
    if hard:
        value, target = hard
        prefs.append(_preference(context, OwnerPreferenceCategory.HARD_CONSTRAINT.value, value, OwnerMemoryScope.LEAGUE.value, source, status, raw, ConfirmationState.REQUIRED.value, target=target))

    if not prefs and any(marker in normalized for marker in DURABLE_MARKERS):
        warnings.append("unsupported_preference_language")
    return prefs, warnings


def normalize_feedback(text: Any, context: AssistantRequestContext) -> OwnerFeedback | CorrectnessDispute | None:
    raw = str(text or "").strip()
    normalized = _normalize(raw)
    if not normalized:
        return None
    if any(marker in normalized for marker in ("not on my team", "salary is wrong", "do not own that pick", "don't own that pick", "that pick is wrong")):
        return CorrectnessDispute(context.user_id, context.league_id, context.league_team_id, raw, "user_feedback", False)
    mapping = (
        ("accepted", ("i accept", "accepted", "that works")),
        ("rejected", ("i reject", "reject that", "no thanks")),
        ("too_aggressive", ("too aggressive", "too risky")),
        ("too_conservative", ("too conservative", "not aggressive enough")),
        ("too_detailed", ("too detailed", "too long")),
        ("insufficient_explanation", ("explain more", "not enough explanation")),
        ("irrelevant", ("irrelevant", "not relevant")),
    )
    for feedback_type, markers in mapping:
        if any(marker in normalized for marker in markers):
            return OwnerFeedback(context.user_id, context.league_id, context.league_team_id, feedback_type, "current_recommendation", creates_preference_candidate=feedback_type in {"too_aggressive", "too_conservative", "too_detailed", "insufficient_explanation"})
    return None


def _preference(
    context: AssistantRequestContext,
    category: str,
    value: str,
    scope: str,
    source: str,
    status: str,
    raw: str,
    confirmation: str = ConfirmationState.NOT_REQUIRED.value,
    *,
    target: str | None = None,
) -> OwnerPreference:
    return OwnerPreference(
        user_id=context.user_id,
        league_id=context.league_id if scope in {OwnerMemoryScope.LEAGUE.value, OwnerMemoryScope.TEAM.value, OwnerMemoryScope.CONVERSATION.value} else None,
        league_team_id=context.league_team_id if scope in {OwnerMemoryScope.TEAM.value, OwnerMemoryScope.CONVERSATION.value} else None,
        scope=scope,
        category=category,
        normalized_value=value,
        source_type=source,
        explicit=source != OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value,
        inferred=source == OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value,
        status=status,
        confirmation_state=confirmation,
        target=target,
        raw_text=raw[:240],
        lineage=[OwnerIntelligenceLineage(source, "current_message", raw[:240])],
    )


def _strategic_goal(normalized: str) -> str | None:
    if any(marker in normalized for marker in ("i am rebuilding", "i want to rebuild")):
        return "rebuild"
    if any(marker in normalized for marker in ("trying to contend", "want to compete this year", "want to compete now", "compete this year", "contend this year")):
        return "contend"
    if "i am retooling" in normalized or "i want to retool" in normalized:
        return "retool"
    return None


def _time_horizon(normalized: str) -> str | None:
    if "this year" in normalized or "this season" in normalized or "compete now" in normalized:
        return "current_season"
    if "next two seasons" in normalized or "two year" in normalized:
        return "next_two_seasons"
    if "long term" in normalized or "multi year" in normalized:
        return "long_term"
    return None


def _asset_preferences(normalized: str) -> list[str]:
    values = []
    if "prefer younger players" in normalized or "prioritize young players" in normalized:
        values.append("prefers_younger_players")
    if "prefer proven veterans" in normalized or "prefer veterans" in normalized:
        values.append("prefers_veterans")
    if "prioritize draft picks" in normalized or "value draft picks" in normalized:
        values.append("prioritize_draft_picks")
    if "preserve my future first" in normalized or "preserve future first" in normalized:
        values.append("preserve_future_firsts")
    if "prioritize cap flexibility" in normalized or "preserve cap flexibility" in normalized:
        values.append("prioritize_cap_flexibility")
    return values


def _risk_preference(normalized: str) -> str | None:
    if "prefer safer moves" in normalized or "conservative" in normalized:
        return "conservative"
    if "take more risk" in normalized or "take a swing" in normalized or "aggressive" in normalized:
        return "aggressive"
    return None


def _communication_preferences(normalized: str) -> list[str]:
    values = []
    if "keep answers concise" in normalized or "keep answers short" in normalized:
        values.append("concise")
    if "give me more detail" in normalized or "detailed answer" in normalized:
        values.append("detailed")
    return values


def _decision_preferences(normalized: str) -> list[str]:
    values = []
    if "give me several options" in normalized:
        values.append("several_options")
    if "challenge my assumptions" in normalized or "challenge me" in normalized:
        values.append("challenge_assumptions")
    if "direct recommendation" in normalized:
        values.append("direct_recommendation")
    return values


def _hard_constraint(normalized: str) -> tuple[str, str | None] | None:
    if _is_explicit_hard_constraint(normalized) and re.search(r"\b20\d{2}\s+first\b", normalized):
        year = re.search(r"\b(20\d{2})\s+first\b", normalized)
        return "do_not_trade_first_round_pick", f"{year.group(1)} first" if year else "future first"
    player = re.search(r"do not recommend trading\s+(.+?)(?:[?.!]|$)", normalized)
    if player:
        target = player.group(1).strip()
        if target and "my " not in target:
            return "do_not_trade_player", target
    return None


def _is_explicit_hard_constraint(normalized: str) -> bool:
    return "do not recommend trading" in normalized or "don't recommend trading" in normalized


def _has_durable_signal(normalized: str) -> bool:
    return any(marker in normalized for marker in ("going forward", "for this league", "always", "never"))


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9. ]+", " ", str(text or "").lower())).strip()
