from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from gm_assistant.request_context import AssistantRequestContext


STATE_VERSION = "gm_conversation_state.v1"


class ConversationStateError(RuntimeError):
    """Raised when conversation state cannot be trusted for this context."""


@dataclass
class ConversationState:
    conversation_id: str
    user_id: str
    league_id: str
    league_team_id: str
    active_objective: str | None = None
    active_timeframe: str | None = None
    discussed_player_ids: list[str] = field(default_factory=list)
    discussed_team_ids: list[str] = field(default_factory=list)
    discussed_pick_ids: list[str] = field(default_factory=list)
    discussed_assets: list[dict] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    rejected_options: list[dict] = field(default_factory=list)
    accepted_assumptions: list[str] = field(default_factory=list)
    unresolved_ambiguities: list[str] = field(default_factory=list)
    current_scenario: dict | None = None
    prior_recommendation_ref: str | None = None
    updated_at: str | None = None
    last_message_id: str | None = None
    state_version: str = STATE_VERSION


@dataclass
class ConversationStateUpdate:
    replace_objective: str | None = None
    clear_objective: bool = False
    replace_timeframe: str | None = None
    clear_timeframe: bool = False
    add_player_ids: list[str] = field(default_factory=list)
    add_team_ids: list[str] = field(default_factory=list)
    add_pick_ids: list[str] = field(default_factory=list)
    add_assets: list[dict] = field(default_factory=list)
    add_constraints: dict = field(default_factory=dict)
    remove_constraint_keys: list[str] = field(default_factory=list)
    add_rejected_options: list[dict] = field(default_factory=list)
    clear_rejected_options: bool = False
    replace_current_scenario: dict | None = None
    clear_current_scenario: bool = False
    add_ambiguities: list[str] = field(default_factory=list)
    resolve_ambiguities: list[str] = field(default_factory=list)
    prior_recommendation_ref: str | None = None
    reset_state: bool = False
    last_message_id: str | None = None


def conversation_scope_key(context: AssistantRequestContext) -> str:
    _require_context(context)
    return f"{context.user_id}:{context.league_id}:{context.league_team_id}"


def active_conversation_id_key(context: AssistantRequestContext) -> str:
    return f"gm_active_conversation_id:{conversation_scope_key(context)}"


def conversation_state_key(context: AssistantRequestContext, conversation_id: str) -> str:
    return f"gm_conversation_state:{conversation_scope_key(context)}:{conversation_id}"


def get_or_create_conversation_id(context: AssistantRequestContext, session_state: dict) -> str:
    key = active_conversation_id_key(context)
    conversation_id = str(session_state.get(key) or "").strip()
    if not conversation_id:
        conversation_id = "default"
        session_state[key] = conversation_id
    return conversation_id


def create_conversation_state(
    context: AssistantRequestContext,
    conversation_id: str | None = None,
) -> ConversationState:
    _require_context(context)
    return ConversationState(
        conversation_id=conversation_id or "default",
        user_id=context.user_id,
        league_id=context.league_id,
        league_team_id=context.league_team_id,
        updated_at=_utc_now(),
    )


def load_conversation_state(
    context: AssistantRequestContext,
    session_state: dict,
    *,
    conversation_id: str | None = None,
) -> ConversationState:
    active_id = conversation_id or get_or_create_conversation_id(context, session_state)
    key = conversation_state_key(context, active_id)
    raw = session_state.get(key)
    if not raw:
        state = create_conversation_state(context, active_id)
        save_conversation_state(context, session_state, state)
        return state

    state = deserialize_conversation_state(raw)
    validate_conversation_state(context, state)
    return state


def save_conversation_state(
    context: AssistantRequestContext,
    session_state: dict,
    state: ConversationState,
) -> None:
    validate_conversation_state(context, state)
    session_state[conversation_state_key(context, state.conversation_id)] = serialize_conversation_state(state)


def reset_conversation_state(
    context: AssistantRequestContext,
    session_state: dict,
    *,
    conversation_id: str | None = None,
) -> ConversationState:
    new_id = conversation_id or uuid4().hex
    session_state[active_conversation_id_key(context)] = new_id
    state = create_conversation_state(context, new_id)
    save_conversation_state(context, session_state, state)
    return state


def validate_conversation_state(
    context: AssistantRequestContext,
    state: ConversationState,
) -> None:
    _require_context(context)
    if state.state_version != STATE_VERSION:
        raise ConversationStateError("Conversation state version is not supported.")
    if state.user_id != context.user_id:
        raise ConversationStateError("Conversation state user scope does not match.")
    if state.league_id != context.league_id:
        raise ConversationStateError("Conversation state league scope does not match.")
    if state.league_team_id != context.league_team_id:
        raise ConversationStateError("Conversation state team scope does not match.")
    if not state.conversation_id:
        raise ConversationStateError("Conversation state requires a conversation id.")


def update_conversation_state(
    state: ConversationState,
    update: ConversationStateUpdate,
) -> ConversationState:
    if update.reset_state:
        return ConversationState(
            conversation_id=state.conversation_id,
            user_id=state.user_id,
            league_id=state.league_id,
            league_team_id=state.league_team_id,
            updated_at=_utc_now(),
            last_message_id=update.last_message_id,
        )

    if update.clear_objective:
        state.active_objective = None
    if update.replace_objective:
        state.active_objective = update.replace_objective

    if update.clear_timeframe:
        state.active_timeframe = None
    if update.replace_timeframe:
        state.active_timeframe = update.replace_timeframe

    state.discussed_player_ids = _merge_unique(state.discussed_player_ids, update.add_player_ids)
    state.discussed_team_ids = _merge_unique(state.discussed_team_ids, update.add_team_ids)
    state.discussed_pick_ids = _merge_unique(state.discussed_pick_ids, update.add_pick_ids)
    state.discussed_assets = _merge_unique_dicts(state.discussed_assets, update.add_assets)

    for key in update.remove_constraint_keys:
        state.constraints.pop(str(key), None)
    state.constraints.update({str(key): value for key, value in update.add_constraints.items()})

    if update.clear_rejected_options:
        state.rejected_options = []
    state.rejected_options = _merge_unique_dicts(state.rejected_options, update.add_rejected_options)

    if update.clear_current_scenario:
        state.current_scenario = None
    if update.replace_current_scenario is not None:
        state.current_scenario = dict(update.replace_current_scenario)

    state.unresolved_ambiguities = [
        value for value in state.unresolved_ambiguities
        if value not in set(update.resolve_ambiguities)
    ]
    state.unresolved_ambiguities = _merge_unique(state.unresolved_ambiguities, update.add_ambiguities)

    if update.prior_recommendation_ref:
        state.prior_recommendation_ref = update.prior_recommendation_ref

    state.last_message_id = update.last_message_id or state.last_message_id
    state.updated_at = _utc_now()
    return state


def serialize_conversation_state(state: ConversationState) -> dict:
    data = asdict(state)
    data["state_version"] = STATE_VERSION
    return data


def deserialize_conversation_state(raw: dict | ConversationState) -> ConversationState:
    if isinstance(raw, ConversationState):
        return raw
    if not isinstance(raw, dict):
        raise ConversationStateError("Stored conversation state is malformed.")
    allowed = set(ConversationState.__dataclass_fields__.keys())
    data = {key: raw.get(key) for key in allowed if key in raw}
    return ConversationState(**data)


def resolve_current_player_reference(state: ConversationState) -> str | None:
    if len(state.discussed_player_ids) == 1:
        return state.discussed_player_ids[0]
    if len(state.discussed_player_ids) > 1:
        ambiguity = "current_player_reference"
        if ambiguity not in state.unresolved_ambiguities:
            state.unresolved_ambiguities.append(ambiguity)
    return None


def infer_conversation_state_update_from_text(
    text: str,
    *,
    message_id: str | None = None,
) -> ConversationStateUpdate:
    q = " ".join(str(text or "").strip().lower().split())
    update = ConversationStateUpdate(last_message_id=message_id)
    if not q:
        return update

    if any(marker in q for marker in ("start over", "reset conversation", "new chat", "forget everything")):
        update.reset_state = True
        return update

    if "actually" in q or q.startswith("forget "):
        if "rebuild" in q or "get younger" in q:
            update.replace_objective = "rebuild" if "rebuild" in q else "get_younger"
        elif "win" in q or "compete" in q or "contend" in q:
            update.replace_objective = "compete"

    if "i want to rebuild" in q or "want to rebuild" in q:
        update.replace_objective = "rebuild"
    elif "i want to get younger" in q or "get younger" in q:
        update.replace_objective = "get_younger"
    elif "win now" in q or "compete this season" in q or "contend this season" in q:
        update.replace_objective = "compete"
        update.replace_timeframe = "current_season"

    if "next year" in q or "next season" in q:
        update.replace_timeframe = "next_season"
    elif "this year" in q or "this season" in q:
        update.replace_timeframe = "current_season"

    if "do not want to move my first" in q or "don't want to move my first" in q or "without moving my first" in q:
        update.add_constraints["do_not_trade_first_round_pick"] = True
    if "willing to move my first" in q or "will move my first" in q or "include my first" in q:
        update.remove_constraint_keys.append("do_not_trade_first_round_pick")
        update.add_constraints["first_round_pick_available"] = True
    if "include my second" in q or "add my second" in q:
        update.add_assets.append({"type": "draft_pick", "round": 2, "side": "outgoing"})
        update.replace_current_scenario = {"type": "hypothetical_trade", "includes_second_round_pick": True}

    if q.startswith("what if ") or " what if " in q:
        update.replace_current_scenario = update.replace_current_scenario or {
            "type": "hypothetical",
            "summary": str(text).strip()[:240],
        }

    if "second option" in q:
        update.prior_recommendation_ref = "second_option"

    if any(ref in q.split() for ref in ("him", "he", "that", "they")):
        update.add_ambiguities.append("unresolved_reference")

    return update


def build_model_context_packet(state: ConversationState | None) -> dict:
    if not state:
        return {}
    return {
        "conversation_id": state.conversation_id,
        "active_objective": state.active_objective,
        "active_timeframe": state.active_timeframe,
        "discussed_player_ids": state.discussed_player_ids[-8:],
        "discussed_team_ids": state.discussed_team_ids[-8:],
        "discussed_pick_ids": state.discussed_pick_ids[-8:],
        "discussed_assets": state.discussed_assets[-8:],
        "constraints": state.constraints,
        "rejected_options": state.rejected_options[-5:],
        "accepted_assumptions": state.accepted_assumptions[-5:],
        "unresolved_ambiguities": state.unresolved_ambiguities[-5:],
        "current_scenario": state.current_scenario,
        "prior_recommendation_ref": state.prior_recommendation_ref,
        "last_message_id": state.last_message_id,
        "state_version": state.state_version,
    }


def is_explicit_durable_preference(text: str) -> bool:
    q = " ".join(str(text or "").strip().lower().split())
    if not q:
        return False
    explicit_markers = (
        "do not recommend",
        "don't recommend",
        "never recommend",
        "i prefer",
        "i am comfortable",
        "i'm comfortable",
        "for this league, i want",
        "i want to compete this season",
    )
    temporary_markers = ("should i", "maybe", "what if", "hypothetical")
    return any(marker in q for marker in explicit_markers) and not any(marker in q for marker in temporary_markers)


def durable_memory_fields_from_text(text: str) -> dict:
    if not is_explicit_durable_preference(text):
        return {}

    q = " ".join(str(text or "").strip().lower().split())
    fields: dict[str, Any] = {}
    notes = []

    if "first" in q and ("do not" in q or "don't" in q or "never" in q):
        notes.append("explicit_preference:do_not_trade_first_round_pick")
    if "younger" in q:
        notes.append("explicit_preference:prefers_younger_players")
        fields["team_build_preference"] = "prioritize_youth"
    if "risk" in q and ("comfortable" in q or "high" in q):
        fields["risk_tolerance"] = "high"
    if "compete this season" in q or "win this season" in q:
        fields["team_build_preference"] = "compete_this_season"
        fields["current_focus"] = "compete_this_season"

    if notes:
        fields["notes"] = notes
    return fields


def _require_context(context: AssistantRequestContext) -> None:
    if not isinstance(context, AssistantRequestContext):
        raise ConversationStateError("Conversation state requires a valid assistant request context.")
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise ConversationStateError("Conversation state requires user, league, and team scope.")


def _merge_unique(existing: list[str], additions: list[Any]) -> list[str]:
    out = list(existing or [])
    for value in additions or []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _merge_unique_dicts(existing: list[dict], additions: list[dict]) -> list[dict]:
    out = list(existing or [])
    seen = {_dict_key(item) for item in out}
    for item in additions or []:
        if not isinstance(item, dict):
            continue
        key = _dict_key(item)
        if key not in seen:
            out.append(dict(item))
            seen.add(key)
    return out


def _dict_key(value: dict) -> tuple:
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
