from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

from auth import service_client


DEFAULT_MEMORY = {
    "gm_style": "balanced",
    "risk_tolerance": "medium",
    "team_build_preference": "contend_with_flexibility",
    "trade_style": "value-aware",
    "preferred_strategy": "use strengths to fix needs",
    "notes": [],
}


class AssistantAccessError(RuntimeError):
    """Raised when assistant context cannot be scoped to the active membership."""


@dataclass(frozen=True)
class AssistantIdentity:
    team_name: str
    user_id: str | None
    league_id: str | None
    league_team_id: str | None
    allow_legacy_fallback: bool = False

    @property
    def has_modern_team_scope(self) -> bool:
        return bool(self.user_id and self.league_id and self.league_team_id)


def load_gm_brain_context(
    team_name: str,
    user_id: str | None = None,
    league_id: str | None = None,
    league_team_id: str | None = None,
    allow_legacy_fallback: bool = False,
    sb: Any | None = None,
) -> Dict:
    """
    Fast conversational brain context scoped by authenticated user, league, and league team.
    """

    sb = sb or service_client()
    identity = AssistantIdentity(
        team_name=team_name,
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        allow_legacy_fallback=allow_legacy_fallback,
    )

    membership = _validate_membership(sb, identity)

    team_brain = _load_team_brain(sb, identity)
    league_brain = _load_league_brain(sb, identity)
    player_profiles = _load_team_collection(sb, "player_strategic_profiles", identity)
    relative_values = _load_team_collection(sb, "league_relative_player_values", identity)
    memory = load_gm_memory(
        team_name=team_name,
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        sb=sb,
    )

    _debug(
        "context_loaded",
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        legacy_fallback=allow_legacy_fallback,
    )

    return {
        "team_name": team_name,
        "user_id": user_id,
        "league_id": league_id,
        "league_team_id": league_team_id,
        "legacy_team_fallback_used": allow_legacy_fallback,
        "membership_role": membership.get("role"),
        "team_brain": team_brain,
        "league_brain": league_brain,
        "player_profiles": player_profiles,
        "relative_values": relative_values,
        "gm_memory": memory,
        "context_summary": build_context_summary(
            team_name=team_name,
            team_brain=team_brain,
            league_brain=league_brain,
            memory=memory,
        ),
    }


def load_gm_memory(
    team_name: str,
    user_id: str | None = None,
    league_id: str | None = None,
    league_team_id: str | None = None,
    sb: Any | None = None,
) -> Dict:
    sb = sb or service_client()

    if not user_id or not league_id or not league_team_id:
        return _default_memory(
            team_name=team_name,
            user_id=user_id,
            league_id=league_id,
            league_team_id=league_team_id,
        )

    try:
        rows = (
            sb.table("gm_user_memory")
            .select("*")
            .eq("user_id", user_id)
            .eq("league_id", league_id)
            .eq("league_team_id", league_team_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        memory = _default_memory(
            team_name=team_name,
            user_id=user_id,
            league_id=league_id,
            league_team_id=league_team_id,
        )
        memory["memory_load_error"] = "retrieval_failed"
        return memory

    if rows:
        row = rows[0]
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "league_id": row.get("league_id"),
            "league_team_id": row.get("league_team_id"),
            "team_name": row.get("team_name") or team_name,
            "gm_style": row.get("gm_style") or DEFAULT_MEMORY["gm_style"],
            "risk_tolerance": row.get("risk_tolerance") or DEFAULT_MEMORY["risk_tolerance"],
            "team_build_preference": row.get("team_build_preference") or DEFAULT_MEMORY["team_build_preference"],
            "trade_style": row.get("trade_style") or DEFAULT_MEMORY["trade_style"],
            "preferred_strategy": row.get("preferred_strategy") or DEFAULT_MEMORY["preferred_strategy"],
            "current_focus": row.get("current_focus"),
            "players_discussed": row.get("players_discussed") or [],
            "teams_discussed": row.get("teams_discussed") or [],
            "trade_targets_discussed": row.get("trade_targets_discussed") or [],
            "conversation_summary": row.get("conversation_summary"),
            "notes": row.get("notes") or [],
        }

    return _default_memory(
        team_name=team_name,
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
    )


def update_gm_memory(
    team_name: str,
    user_id: str | None = None,
    league_id: str | None = None,
    league_team_id: str | None = None,
    current_focus: str | None = None,
    players_discussed: List[str] | None = None,
    teams_discussed: List[str] | None = None,
    trade_targets_discussed: List[str] | None = None,
    conversation_summary: str | None = None,
    notes: List[str] | None = None,
    gm_style: str | None = None,
    risk_tolerance: str | None = None,
    team_build_preference: str | None = None,
    trade_style: str | None = None,
    preferred_strategy: str | None = None,
    sb: Any | None = None,
):
    if not user_id or user_id == "default" or not league_id or not league_team_id:
        raise AssistantAccessError("GM memory updates require user_id, league_id, and league_team_id.")

    sb = sb or service_client()
    _validate_membership(
        sb,
        AssistantIdentity(
            team_name=team_name,
            user_id=user_id,
            league_id=league_id,
            league_team_id=league_team_id,
        ),
    )

    existing = load_gm_memory(
        team_name=team_name,
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        sb=sb,
    )

    row = {
        "user_id": user_id,
        "league_id": league_id,
        "league_team_id": league_team_id,
        "team_name": team_name,
        "gm_style": gm_style or existing.get("gm_style") or DEFAULT_MEMORY["gm_style"],
        "risk_tolerance": risk_tolerance or existing.get("risk_tolerance") or DEFAULT_MEMORY["risk_tolerance"],
        "team_build_preference": team_build_preference or existing.get("team_build_preference") or DEFAULT_MEMORY["team_build_preference"],
        "trade_style": trade_style or existing.get("trade_style") or DEFAULT_MEMORY["trade_style"],
        "preferred_strategy": preferred_strategy or existing.get("preferred_strategy") or DEFAULT_MEMORY["preferred_strategy"],
        "current_focus": current_focus or existing.get("current_focus"),
        "players_discussed": _merge_unique(existing.get("players_discussed", []), players_discussed or []),
        "teams_discussed": _merge_unique(existing.get("teams_discussed", []), teams_discussed or []),
        "trade_targets_discussed": _merge_unique(existing.get("trade_targets_discussed", []), trade_targets_discussed or []),
        "conversation_summary": conversation_summary or existing.get("conversation_summary"),
        "notes": _merge_unique(existing.get("notes", []), notes or []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    sb.table("gm_user_memory").upsert(
        row,
        on_conflict="user_id,league_id,league_team_id",
    ).execute()

    _debug("memory_updated", user_id=user_id, league_id=league_id, league_team_id=league_team_id)
    return row


def build_context_summary(team_name: str, team_brain: Dict, league_brain: Dict, memory: Dict) -> str:
    if not team_brain:
        return f"No team brain found for {team_name}."

    return (
        f"{team_name} is currently profiled as {team_brain.get('team_direction')}. "
        f"Strengths: {', '.join(team_brain.get('position_strengths') or []) or 'none clear'}. "
        f"Needs: {', '.join(team_brain.get('position_needs') or []) or 'none clear'}. "
        f"Core players: {', '.join((team_brain.get('core_players') or [])[:5]) or 'none flagged'}. "
        f"GM preference: {memory.get('team_build_preference')}; "
        f"risk tolerance: {memory.get('risk_tolerance')}; "
        f"trade style: {memory.get('trade_style')}. "
        f"League summary: {(league_brain or {}).get('summary', 'No league brain loaded')}."
    )


def _validate_membership(sb: Any, identity: AssistantIdentity) -> Dict:
    if not identity.user_id or identity.user_id == "default":
        raise AssistantAccessError("Assistant context requires an authenticated user.")
    if not identity.league_id:
        raise AssistantAccessError("Assistant context requires an active league.")

    rows = (
        sb.table("league_memberships")
        .select("id,league_id,user_id,role,league_team_id,team_id")
        .eq("user_id", identity.user_id)
        .eq("league_id", identity.league_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    if not rows:
        raise AssistantAccessError("No matching league membership found for assistant context.")

    membership = rows[0]
    member_league_team_id = membership.get("league_team_id")
    member_team_id = membership.get("team_id")
    resolved_member_team_id = _resolve_membership_team_id(
        sb,
        league_id=identity.league_id,
        league_team_id=member_league_team_id,
        team_id=member_team_id,
    )

    _log_membership_identity(
        authenticated_user_id=identity.user_id,
        requested_league_id=identity.league_id,
        membership_id=membership.get("id"),
        membership_team_id=member_team_id,
        membership_league_team_id=member_league_team_id,
        resolved_league_team_id=resolved_member_team_id,
    )

    if identity.league_team_id:
        if resolved_member_team_id and str(resolved_member_team_id) != str(identity.league_team_id):
            raise AssistantAccessError("Assistant team scope does not match league membership.")
        if not resolved_member_team_id:
            raise AssistantAccessError("League membership is missing a valid league team reference.")
    else:
        raise AssistantAccessError("Assistant context requires league_team_id.")

    return membership


def _resolve_membership_team_id(
    sb: Any,
    *,
    league_id: str,
    league_team_id: Any,
    team_id: Any,
) -> str | None:
    for candidate in (league_team_id, team_id):
        if not candidate:
            continue

        rows = _optional_rows(
            sb.table("league_teams")
            .select("id,league_id")
            .eq("id", candidate)
            .eq("league_id", league_id)
            .limit(1)
        )

        if rows:
            return str(rows[0].get("id"))

    return None


def _log_membership_identity(
    *,
    authenticated_user_id,
    requested_league_id,
    membership_id,
    membership_team_id,
    membership_league_team_id,
    resolved_league_team_id,
) -> None:
    print(
        "ASSISTANT_IDENTITY "
        f"authenticated_user_id={_safe_identity_log_value(authenticated_user_id)} "
        f"requested_league_id={_safe_identity_log_value(requested_league_id)} "
        f"membership_id={_safe_identity_log_value(membership_id)} "
        f"membership_team_id={_safe_identity_log_value(membership_team_id)} "
        f"membership_league_team_id={_safe_identity_log_value(membership_league_team_id)} "
        f"resolved_league_team_id={_safe_identity_log_value(resolved_league_team_id)}",
        flush=True,
    )


def _safe_identity_log_value(value: Any) -> str:
    if value is None:
        return "none"
    text = str(value).strip()
    return text or "none"


def _load_team_brain(sb: Any, identity: AssistantIdentity) -> Dict:
    if identity.league_id and identity.league_team_id:
        rows = _optional_rows(
            sb.table("team_brain")
            .select("*")
            .eq("league_id", identity.league_id)
            .eq("league_team_id", identity.league_team_id)
            .limit(1)
        )
        if rows:
            return rows[0]

    if identity.allow_legacy_fallback:
        rows = _optional_rows(
            sb.table("team_brain")
            .select("*")
            .eq("team_name", identity.team_name)
            .limit(1)
        )
        return _one(rows)

    return {}


def _load_league_brain(sb: Any, identity: AssistantIdentity) -> Dict:
    if identity.league_id:
        rows = _optional_rows(
            sb.table("league_brain")
            .select("*")
            .eq("league_id", identity.league_id)
            .limit(1)
        )
        if rows:
            return rows[0]

    if identity.allow_legacy_fallback:
        rows = _optional_rows(
            sb.table("league_brain")
            .select("*")
            .eq("league_key", "default")
            .limit(1)
        )
        return _one(rows)

    return {}


def _load_team_collection(sb: Any, table_name: str, identity: AssistantIdentity) -> List[Dict]:
    if identity.league_id and identity.league_team_id:
        rows = _optional_rows(
            sb.table(table_name)
            .select("*")
            .eq("league_id", identity.league_id)
            .eq("league_team_id", identity.league_team_id)
        )
        if rows:
            return rows

    if identity.allow_legacy_fallback:
        return _optional_rows(
            sb.table(table_name)
            .select("*")
            .eq("owner_team_name", identity.team_name)
        )

    return []


def _optional_rows(query: Any) -> List[Dict]:
    try:
        return query.execute().data or []
    except Exception:
        return []


def _default_memory(
    team_name: str,
    user_id: str | None,
    league_id: str | None,
    league_team_id: str | None,
) -> Dict:
    return {
        **DEFAULT_MEMORY,
        "user_id": user_id,
        "league_id": league_id,
        "league_team_id": league_team_id,
        "team_name": team_name,
        "current_focus": None,
        "players_discussed": [],
        "teams_discussed": [],
        "trade_targets_discussed": [],
        "conversation_summary": None,
    }


def _one(rows):
    return rows[0] if rows else {}


def _merge_unique(a, b):
    out = []
    for x in (a or []) + (b or []):
        if x and x not in out:
            out.append(x)
    return out


def _debug(label: str, **fields: Any) -> None:
    if os.getenv("ASSISTANT_IDENTITY_DEBUG") != "1":
        return

    safe = " ".join(f"{key}={_safe_debug_value(value)}" for key, value in fields.items())
    print(f"ASSISTANT_IDENTITY_DEBUG {label} {safe}".strip())


def _safe_debug_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if len(text) <= 8:
        return text
    return f"{text[:4]}...{text[-4:]}"
