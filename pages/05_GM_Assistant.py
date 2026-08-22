
from __future__ import annotations

import os
import json
from hashlib import sha256

import streamlit as st

from components.sidebar_nav import render_nav
from auth import current_user, require_login, service_client
from gm_assistant.coach_condor import coach_opening, quick_prompts_for_context
from gm_assistant.conversation_state import (
    get_or_create_conversation_id,
    load_conversation_state,
    save_conversation_state,
)
from gm_assistant.data import load_gm_context
from gm_assistant.brain_context import AssistantAccessError, load_gm_brain_context
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.runtime import AssistantRuntime, AssistantRuntimeInput
from gm_assistant.openai_reasoning import configuration_status, load_reasoning_config


st.set_page_config(
    page_title="GM Assistant",
    layout="wide"
)

render_nav()
require_login()

loading_placeholder = st.empty()
loading_placeholder.markdown(
    """
    <style>
    .legacy-loader-wrap { height: 70vh; display: flex; flex-direction: column; justify-content: center; align-items: center; }
    .legacy-loader { width: 70px; height: 70px; border: 6px solid rgba(226,188,91,.25); border-top: 6px solid #E2BC5B; border-radius: 50%; animation: legacy-spin 1s linear infinite; box-shadow: 0 0 24px rgba(226,188,91,.18); }
    .legacy-loader-text { margin-top: 22px; font-size: 1.35rem; font-weight: 800; color: #F5EBD7; letter-spacing: .04em; text-transform: uppercase; }
    @keyframes legacy-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
    <div class="legacy-loader-wrap">
        <div class="legacy-loader"></div>
        <div class="legacy-loader-text">Loading GM Assistant...</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ----------------------------
# LOAD ACTIVE TEAM
# ----------------------------

ctx = load_gm_context()
team = (ctx or {}).get("team", {})
request_context: AssistantRequestContext | None = (ctx or {}).get("assistant_context")

if (ctx or {}).get("assistant_context_error"):
    loading_placeholder.empty()
    st.error((ctx or {}).get("assistant_context_error"))
    st.stop()

owner_team_name = (
    (request_context.owner_name if request_context else None)
    or team.get("owner_name")
    or team.get("team_name")
)

user = current_user() or {}
user_id = request_context.user_id if request_context else user.get("id")
league_id = request_context.league_id if request_context else team.get("league_id")
league_team_id = request_context.league_team_id if request_context else team.get("league_team_id")

if not owner_team_name or not league_id or not user_id:
    loading_placeholder.empty()
    st.error("Unable to determine your active team. Please open My Team first or verify your league membership.")
    st.stop()


# ----------------------------
# LOAD GM BRAIN CONTEXT
# ----------------------------

try:
    brain_ctx = load_gm_brain_context(
        owner_team_name,
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        allow_legacy_fallback=False,
    )
except AssistantAccessError:
    loading_placeholder.empty()
    st.error(
        "GM Assistant could not connect your account to a league team yet. "
        "Please ask the commissioner to verify your league membership, then reload this page."
    )
    st.stop()

loading_placeholder.empty()

team_brain = brain_ctx.get("team_brain") or {}
gm_memory = brain_ctx.get("gm_memory") or {}
context_summary = brain_ctx.get("context_summary") or ""
conversation_id = "default"
conversation_state = None
if request_context:
    conversation_id = get_or_create_conversation_id(request_context, st.session_state)
    conversation_state = load_conversation_state(
        request_context,
        st.session_state,
        conversation_id=conversation_id,
    )

if os.getenv("ASSISTANT_IDENTITY_DEBUG") == "1":
    print(
        "ASSISTANT_IDENTITY_DEBUG page_scope "
        f"user_id_present={bool(user_id)} "
        f"league_id_present={bool(league_id)} "
        f"league_team_id_present={bool(league_team_id)} "
        "legacy_fallback=false"
    )


def _secret_present(name: str) -> bool:
    try:
        return bool(str(st.secrets.get(name, "")).strip())
    except Exception:
        return False


def _secret_value(name: str) -> str | None:
    try:
        value = str(st.secrets.get(name, "")).strip()
    except Exception:
        return None
    return value or None


def _openai_request_diagnostic(runtime_result=None) -> dict:
    status = configuration_status()
    config = load_reasoning_config()
    env_enabled = os.getenv("OPENAI_REASONING_ENABLED")
    secrets_enabled = _secret_value("OPENAI_REASONING_ENABLED")
    env_model = os.getenv("OPENAI_MODEL") or os.getenv("OPENAI_GM_MODEL")
    secrets_model = _secret_value("OPENAI_MODEL") or _secret_value("OPENAI_GM_MODEL")
    payload = {
        "OPENAI_REASONING_ENABLED_env": env_enabled if env_enabled is not None else "<unset>",
        "OPENAI_REASONING_ENABLED_secrets": secrets_enabled if secrets_enabled is not None else "<unset>",
        "OPENAI_API_KEY_present_env": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "OPENAI_API_KEY_present_secrets": _secret_present("OPENAI_API_KEY"),
        "configured_model_env": env_model or "<unset>",
        "configured_model_secrets": secrets_model or "<unset>",
        "resolved_model": config.model,
        "activation_decision": "enabled" if status.configuration_valid else "skipped",
        "activation_reason": status.safe_error_code or "configuration_valid",
        "provider_type_selected": status.provider_selected,
    }
    if runtime_result is not None:
        safe_metadata = runtime_result.safe_metadata or {}
        reasoning = safe_metadata.get("reasoning") or {}
        evidence = getattr(runtime_result, "evidence_diagnostics", None) or safe_metadata.get("evidence") or {}
        payload.update(
            {
                "provider_reason_called": bool(reasoning.get("provider_called")),
                "provider_result_ok": reasoning.get("provider_status") == "success",
                "provider_error_code": reasoning.get("safe_error_code"),
                "response_validation_result": reasoning.get("validation_status"),
                "response_validation_errors": reasoning.get("validation_errors") or [],
                "final_answer_source": reasoning.get("final_answer_source"),
                "fallback_status": reasoning.get("fallback_status"),
                "fallback_reason": reasoning.get("fallback_reason"),
                "roster_player_count": evidence.get("roster_player_count"),
                "evaluated_player_count": evidence.get("evaluated_player_count"),
                "missing_profile_count": evidence.get("missing_profile_count"),
                "missing_contract_count": evidence.get("missing_contract_count"),
                "player_evaluation_requested": evidence.get("player_evaluation_requested"),
                "player_evaluation_included": evidence.get("player_evaluation_included"),
                "player_evaluation_fact_ref_count": evidence.get("player_evaluation_fact_ref_count"),
            }
        )
    return payload


def _print_openai_diagnostic(runtime_result=None) -> None:
    print(
        "GM_OPENAI_DIAGNOSTIC "
        + json.dumps(_openai_request_diagnostic(runtime_result), sort_keys=True, default=str),
        flush=True,
    )


# ----------------------------
# PAGE STYLE
# ----------------------------

st.markdown(
    """
    <style>
    .gm-header {
        padding: 1.2rem 0 0.75rem 0;
    }
    .gm-subtitle {
        opacity: 0.72;
        margin-top: -0.5rem;
        margin-bottom: 1rem;
    }
    [data-testid="stChatMessageContent"] {
        font-size: 1.06rem;
        line-height: 1.65;
    }
    .gm-context-card {
        border: 1px solid rgba(214, 167, 58, 0.35);
        border-radius: 14px;
        padding: 1rem;
        margin-bottom: 1rem;
        background: rgba(255,255,255,0.025);
    }
    .gm-chip {
        display: inline-block;
        padding: 0.25rem 0.55rem;
        margin: 0.15rem 0.2rem 0.15rem 0;
        border-radius: 999px;
        border: 1px solid rgba(214, 167, 58, 0.45);
        font-size: 0.85rem;
        opacity: 0.92;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="gm-header">
        <h1>GM Assistant</h1>
        <p class="gm-subtitle">
            Your live GM room — talk through roster strategy, contracts, trades, and league moves.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ----------------------------
# TOP GM CONTEXT
# ----------------------------

with st.expander("Current GM Brain Context", expanded=False):
    st.markdown(f"**Team Direction:** {team_brain.get('team_direction', 'Unknown')}")
    st.markdown(f"**Strengths:** {', '.join(team_brain.get('position_strengths') or []) or 'None clear'}")
    st.markdown(f"**Needs:** {', '.join(team_brain.get('position_needs') or []) or 'None clear'}")
    st.markdown(f"**Core:** {', '.join((team_brain.get('core_players') or [])[:8]) or 'None flagged'}")
    st.markdown(f"**Current Focus:** {gm_memory.get('current_focus') or 'No focus saved yet'}")
    st.markdown(f"**GM Style:** {gm_memory.get('gm_style')} / {gm_memory.get('trade_style')}")
    st.caption(context_summary)


# ----------------------------
# CHAT STATE
# ----------------------------

history_key = f"gm_messages:{user_id}:{league_id}:{league_team_id}:{conversation_id}"
pending_hash_key = f"gm_pending_prompt_hash:{user_id}:{league_id}:{league_team_id}:{conversation_id}"

if history_key not in st.session_state:
    st.session_state[history_key] = [
        {
            "role": "assistant",
            "content": coach_opening(brain_ctx),
        }
    ]

gm_messages = st.session_state[history_key]


# ----------------------------
# QUICK FOLLOW-UP CHIPS
# ----------------------------

st.markdown("##### Quick GM prompts")

chip_cols = st.columns(5)

quick_prompts = quick_prompts_for_context(brain_ctx)

for col, prompt_text in zip(chip_cols, quick_prompts):
    with col:
        if st.button(prompt_text, use_container_width=True):
            st.session_state.gm_pending_prompt = prompt_text


# ----------------------------
# CHAT DISPLAY
# ----------------------------

for msg in gm_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ----------------------------
# CHAT INPUT
# ----------------------------

typed_prompt = st.chat_input("Talk to your GM Assistant...")

prompt = st.session_state.pop("gm_pending_prompt", None) or typed_prompt

if prompt:
    prompt_hash = sha256(f"{history_key}:{prompt}".encode("utf-8")).hexdigest()
    if st.session_state.get(pending_hash_key) == prompt_hash:
        st.stop()

    st.session_state[pending_hash_key] = prompt_hash

    gm_messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking like your GM..."):
            _print_openai_diagnostic()
            assistant_sb = service_client()
            runtime_result = AssistantRuntime().run(
                AssistantRuntimeInput(
                    context=request_context,
                    question=prompt,
                    conversation_state=conversation_state,
                    conversation_history=gm_messages[:-1],
                    owner_preferences=gm_memory,
                    team_context=brain_ctx,
                    supabase_client=assistant_sb,
                    message_id=prompt_hash,
                )
            )
            _print_openai_diagnostic(runtime_result)
            response = runtime_result.answer_text
            if runtime_result.ok:
                if request_context and runtime_result.conversation_state:
                    save_conversation_state(
                        request_context,
                        st.session_state,
                        runtime_result.conversation_state,
                    )
                st.markdown(response)
            elif runtime_result.error_code in {"identity_context_missing", "team_context_missing", "identity_context_invalid"}:
                st.error(response)
            elif runtime_result.error_code == "rendering_configuration_missing":
                st.warning(response)
            else:
                st.error(response)
            st.session_state.pop(pending_hash_key, None)

    gm_messages.append({"role": "assistant", "content": response})
