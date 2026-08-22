from __future__ import annotations

from typing import Any, Mapping

from gm_assistant.conversation_state import ConversationState
from gm_assistant.owner_intelligence.models import (
    ConfirmationState,
    CorrectnessDispute,
    OwnerCommunicationPreference,
    OwnerConstraint,
    OwnerFeedback,
    OwnerGoal,
    OwnerIntelligenceAvailability,
    OwnerIntelligenceContext,
    OwnerIntelligenceLineage,
    OwnerMemoryScope,
    OwnerPreference,
    OwnerPreferenceCategory,
    OwnerPreferenceSource,
    OwnerPreferenceStatus,
    OwnerRiskPreference,
    OwnerStrategyState,
)
from gm_assistant.owner_intelligence.normalization import normalize_feedback, normalize_owner_preferences_from_text
from gm_assistant.owner_intelligence.repository import MissingOwnerIntelligenceRepository, OwnerIntelligenceRepository
from gm_assistant.request_context import AssistantRequestContext


SOURCE_PRIORITY = {
    OwnerPreferenceSource.EXPLICIT_USER_STATEMENT.value: 1,
    OwnerPreferenceSource.CURRENT_CONVERSATION.value: 2,
    OwnerPreferenceSource.EXPLICIT_USER_CONFIRMATION.value: 3,
    OwnerPreferenceSource.LEGACY_COMPATIBILITY.value: 4,
    OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value: 5,
    OwnerPreferenceSource.SYSTEM_DEFAULT.value: 6,
}


class OwnerIntelligenceService:
    def __init__(self, repository: OwnerIntelligenceRepository | None = None):
        self.repository = repository or MissingOwnerIntelligenceRepository()

    def get_context(
        self,
        *,
        context: AssistantRequestContext,
        conversation_state: ConversationState | None = None,
        owner_preferences: Mapping[str, Any] | None = None,
        current_message: str | None = None,
    ) -> OwnerIntelligenceContext:
        _require_scope(context)
        stored = self.repository.load_preferences(user_id=context.user_id, league_id=context.league_id, league_team_id=context.league_team_id)
        compatibility = _compatibility_preferences(context, owner_preferences or {})
        current, normalization_warnings = normalize_owner_preferences_from_text(current_message or "", context)
        feedback_item = normalize_feedback(current_message or "", context)
        conversation = _conversation_preferences(context, conversation_state)

        all_preferences = stored + compatibility + conversation + current
        active, temporary, superseded = _partition_preferences(all_preferences)
        active_resolved, conflicts = _resolve_active_preferences(active + temporary)
        strategy = _strategy_state(active_resolved, temporary, conflicts)
        feedback: list[OwnerFeedback] = [feedback_item] if isinstance(feedback_item, OwnerFeedback) else []
        disputes: list[CorrectnessDispute] = [feedback_item] if isinstance(feedback_item, CorrectnessDispute) else []
        availability = OwnerIntelligenceAvailability(
            explicit_preferences="available" if any(pref.explicit for pref in active_resolved) else "empty",
            conversation_context="available" if temporary or conversation_state else "empty",
            durable_persistence="available" if self.repository.persistent else "deferred",
            inferred_tendencies="available" if strategy.inferred_tendencies else "deferred",
            conflicts="present" if conflicts else "none",
            scoped_context="complete",
        )
        lineage = [line for pref in active_resolved + temporary for line in pref.lineage]
        return OwnerIntelligenceContext(
            user_id=context.user_id,
            league_id=context.league_id,
            league_team_id=context.league_team_id,
            strategy_state=strategy,
            active_preferences=active_resolved,
            temporary_preferences=temporary,
            superseded_preferences=superseded,
            feedback=feedback,
            correctness_disputes=disputes,
            availability=availability,
            lineage=lineage,
            warnings=normalization_warnings,
        )

    def record_explicit_preference(self, preference: OwnerPreference) -> OwnerPreference:
        if preference.source_type not in {OwnerPreferenceSource.EXPLICIT_USER_STATEMENT.value, OwnerPreferenceSource.EXPLICIT_USER_CONFIRMATION.value}:
            raise ValueError("Only explicit preferences can be recorded.")
        return self.repository.save_preference(preference)

    def supersede_preference(self, preference: OwnerPreference) -> OwnerPreference:
        return self.repository.supersede_preference(preference)


def unavailable_owner_intelligence_context(context: AssistantRequestContext, warning: str) -> OwnerIntelligenceContext:
    return OwnerIntelligenceContext(
        user_id=context.user_id or "unknown",
        league_id=context.league_id or "unknown",
        league_team_id=context.league_team_id or "unknown",
        strategy_state=OwnerStrategyState(),
        availability=OwnerIntelligenceAvailability(
            explicit_preferences="unavailable",
            conversation_context="unavailable",
            durable_persistence="deferred",
            inferred_tendencies="deferred",
            conflicts="none",
            scoped_context="incomplete",
        ),
        warnings=[warning],
    )


def _compatibility_preferences(context: AssistantRequestContext, raw: Mapping[str, Any]) -> list[OwnerPreference]:
    prefs: list[OwnerPreference] = []
    team_build = str(raw.get("team_build_preference") or "").strip().lower()
    if team_build in {"contend", "rebuild", "retool", "prioritize_youth"}:
        value = "prefers_younger_players" if team_build == "prioritize_youth" else team_build
        category = OwnerPreferenceCategory.ASSET_PREFERENCE.value if value == "prefers_younger_players" else OwnerPreferenceCategory.STRATEGIC_GOAL.value
        prefs.append(_preference(context, category, value, OwnerMemoryScope.TEAM.value, OwnerPreferenceSource.LEGACY_COMPATIBILITY.value, f"team_build_preference:{team_build}"))
    risk = str(raw.get("risk_tolerance") or "").strip().lower()
    if risk in {"conservative", "balanced", "aggressive"}:
        prefs.append(_preference(context, OwnerPreferenceCategory.RISK_TOLERANCE.value, risk, OwnerMemoryScope.USER_GLOBAL.value, OwnerPreferenceSource.LEGACY_COMPATIBILITY.value, f"risk_tolerance:{risk}"))
    communication = str(raw.get("communication_style") or "").strip().lower()
    if communication in {"concise", "standard", "detailed"}:
        prefs.append(_preference(context, OwnerPreferenceCategory.COMMUNICATION_STYLE.value, communication, OwnerMemoryScope.USER_GLOBAL.value, OwnerPreferenceSource.LEGACY_COMPATIBILITY.value, f"communication_style:{communication}"))
    for note in raw.get("notes") or []:
        text = str(note or "")
        if "do_not_trade_first_round_pick" in text:
            prefs.append(_preference(context, OwnerPreferenceCategory.HARD_CONSTRAINT.value, "do_not_trade_first_round_pick", OwnerMemoryScope.LEAGUE.value, OwnerPreferenceSource.LEGACY_COMPATIBILITY.value, text, target="future first"))
        if "prefers_younger_players" in text:
            prefs.append(_preference(context, OwnerPreferenceCategory.ASSET_PREFERENCE.value, "prefers_younger_players", OwnerMemoryScope.LEAGUE.value, OwnerPreferenceSource.LEGACY_COMPATIBILITY.value, text))
    return prefs


def _conversation_preferences(context: AssistantRequestContext, state: ConversationState | None) -> list[OwnerPreference]:
    if not state:
        return []
    prefs = []
    if state.active_objective:
        prefs.append(_preference(context, OwnerPreferenceCategory.STRATEGIC_GOAL.value, _normalize_goal(state.active_objective), OwnerMemoryScope.CONVERSATION.value, OwnerPreferenceSource.CURRENT_CONVERSATION.value, "conversation_state.active_objective", status=OwnerPreferenceStatus.CONVERSATION_ONLY.value))
    if state.active_timeframe:
        prefs.append(_preference(context, OwnerPreferenceCategory.TIME_HORIZON.value, str(state.active_timeframe), OwnerMemoryScope.CONVERSATION.value, OwnerPreferenceSource.CURRENT_CONVERSATION.value, "conversation_state.active_timeframe", status=OwnerPreferenceStatus.CONVERSATION_ONLY.value))
    for key, value in (state.constraints or {}).items():
        if key == "do_not_trade_first_round_pick" and value:
            prefs.append(_preference(context, OwnerPreferenceCategory.HARD_CONSTRAINT.value, key, OwnerMemoryScope.CONVERSATION.value, OwnerPreferenceSource.CURRENT_CONVERSATION.value, key, status=OwnerPreferenceStatus.CONVERSATION_ONLY.value, target="future first"))
    return prefs


def _strategy_state(active: list[OwnerPreference], temporary: list[OwnerPreference], conflicts: list[str]) -> OwnerStrategyState:
    goal_pref = _first_category(active, OwnerPreferenceCategory.STRATEGIC_GOAL.value)
    risk_pref = _first_category(active, OwnerPreferenceCategory.RISK_TOLERANCE.value)
    communication_pref = _first_category(active, OwnerPreferenceCategory.COMMUNICATION_STYLE.value)
    hard = [
        OwnerConstraint(pref.normalized_value, True, pref.target, pref.scope, pref.source_type, True, pref.confirmation_state)
        for pref in active
        if pref.category == OwnerPreferenceCategory.HARD_CONSTRAINT.value
    ]
    horizon_pref = _first_category(active, OwnerPreferenceCategory.TIME_HORIZON.value)
    return OwnerStrategyState(
        strategic_goal=OwnerGoal(goal_pref.normalized_value, goal_pref.source_type, goal_pref.scope, goal_pref.confirmation_state) if goal_pref else None,
        time_horizon=horizon_pref.normalized_value if horizon_pref else None,
        asset_preferences=[pref for pref in active if pref.category == OwnerPreferenceCategory.ASSET_PREFERENCE.value],
        risk_preference=OwnerRiskPreference(risk_pref.normalized_value, risk_pref.source_type, risk_pref.scope, risk_pref.confidence_band) if risk_pref else None,
        hard_constraints=hard,
        decision_preferences=[pref for pref in active if pref.category == OwnerPreferenceCategory.DECISION_STYLE.value],
        communication_preference=OwnerCommunicationPreference(communication_pref.normalized_value, communication_pref.source_type, communication_pref.scope) if communication_pref else None,
        conversation_only=temporary,
        inferred_tendencies=[pref for pref in active if pref.inferred],
        conflicts=conflicts,
        confirmation_required=[pref for pref in active + temporary if pref.confirmation_state == ConfirmationState.REQUIRED.value],
    )

def _partition_preferences(preferences: list[OwnerPreference]) -> tuple[list[OwnerPreference], list[OwnerPreference], list[OwnerPreference]]:
    active = [pref for pref in preferences if pref.status == OwnerPreferenceStatus.ACTIVE.value]
    temporary = [pref for pref in preferences if pref.status == OwnerPreferenceStatus.CONVERSATION_ONLY.value]
    superseded = [pref for pref in preferences if pref.status == OwnerPreferenceStatus.SUPERSEDED.value]
    return active, temporary, superseded


def _resolve_active_preferences(preferences: list[OwnerPreference]) -> tuple[list[OwnerPreference], list[str]]:
    by_category: dict[str, list[OwnerPreference]] = {}
    for pref in preferences:
        by_category.setdefault(pref.category, []).append(pref)
    resolved: list[OwnerPreference] = []
    conflicts: list[str] = []
    for category, items in by_category.items():
        if category in {OwnerPreferenceCategory.ASSET_PREFERENCE.value, OwnerPreferenceCategory.HARD_CONSTRAINT.value, OwnerPreferenceCategory.DECISION_STYLE.value}:
            resolved.extend(sorted(items, key=_priority_key))
            continue
        ordered = sorted(items, key=_priority_key)
        if len({item.normalized_value for item in ordered}) > 1:
            conflicts.append(f"conflicting_{category}")
        resolved.append(ordered[0])
    return resolved, conflicts


def _priority_key(pref: OwnerPreference) -> tuple[int, int]:
    status_priority = 0 if pref.status == OwnerPreferenceStatus.CONVERSATION_ONLY.value else 1
    return (status_priority, SOURCE_PRIORITY.get(pref.source_type, 99))


def _first_category(preferences: list[OwnerPreference], category: str) -> OwnerPreference | None:
    for pref in preferences:
        if pref.category == category:
            return pref
    return None


def _preference(
    context: AssistantRequestContext,
    category: str,
    value: str,
    scope: str,
    source: str,
    raw: str,
    *,
    status: str = OwnerPreferenceStatus.ACTIVE.value,
    target: str | None = None,
) -> OwnerPreference:
    return OwnerPreference(
        user_id=context.user_id,
        league_id=context.league_id if scope != OwnerMemoryScope.USER_GLOBAL.value else None,
        league_team_id=context.league_team_id if scope in {OwnerMemoryScope.TEAM.value, OwnerMemoryScope.CONVERSATION.value} else None,
        scope=scope,
        category=category,
        normalized_value=value,
        source_type=source,
        explicit=source != OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value,
        inferred=source == OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value,
        target=target,
        raw_text=raw[:240],
        status=status,
        lineage=[OwnerIntelligenceLineage(source, "compatibility" if source == OwnerPreferenceSource.LEGACY_COMPATIBILITY.value else "conversation_state", raw[:240])],
    )


def _normalize_goal(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"compete", "contend_this_season", "win_now"}:
        return "contend"
    if text in {"get_younger"}:
        return "rebuild"
    return text


def _require_scope(context: AssistantRequestContext) -> None:
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise ValueError("Owner Intelligence requires authenticated user, league, and team scope.")
