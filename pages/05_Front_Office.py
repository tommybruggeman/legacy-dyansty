from __future__ import annotations

from hashlib import sha256

import streamlit as st

from components.sidebar_nav import render_nav
from auth import current_user, require_login
from gm_assistant.data import load_gm_context
from gm_assistant.request_context import AssistantRequestContext
from league_ai.page_glue import (
    CONVERSATION_KEY,
    front_office_data,
    front_office_opening,
    league_ai_available,
    list_conversations,
    load_conversation,
    render_brief,
    render_fo_prompts,
    render_front_office_header,
    render_tiles,
    run_league_ai,
    start_new_conversation,
    weekly_brief,
)


st.set_page_config(page_title="Front Office", layout="wide")

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
        <div class="legacy-loader-text">Opening the Front Office...</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ----------------------------
# ACTIVE TEAM + REQUEST CONTEXT
# ----------------------------
ctx = load_gm_context()
team = (ctx or {}).get("team", {})
request_context: AssistantRequestContext | None = (ctx or {}).get("assistant_context")

if (ctx or {}).get("assistant_context_error"):
    loading_placeholder.empty()
    st.error((ctx or {}).get("assistant_context_error"))
    st.stop()

owner_team_name = (request_context.owner_name if request_context else None) or team.get("owner_name") or team.get("team_name")
user = current_user() or {}
user_id = request_context.user_id if request_context else user.get("id")
league_id = request_context.league_id if request_context else team.get("league_id")
league_team_id = request_context.league_team_id if request_context else team.get("league_team_id")

if not owner_team_name or not league_id or not user_id or not request_context:
    loading_placeholder.empty()
    st.error("Unable to determine your active team. Please open My Team first or verify your league membership.")
    st.stop()

_ai_ok, _ai_status = league_ai_available()

# ----------------------------
# HEADER, TILES, BRIEF PLACEHOLDER
# ----------------------------
fo = None
try:
    fo = front_office_data(request_context=request_context, team=team)
except Exception as exc:  # the chat still works if the header data fails
    print(f"FRONT_OFFICE_HEADER_ERROR {type(exc).__name__}: {exc}", flush=True)

loading_placeholder.empty()
render_front_office_header(owner_team_name, fo["week"] if fo else 0, _ai_status)

brief_slot = None
if fo:
    render_tiles(fo["tiles"])
    brief_slot = st.empty()
    with brief_slot.container():
        render_brief(fo["data_brief"])  # instant, from data; verdict lines fill in below
else:
    st.warning("Couldn't load the Front Office header right now; the Assistant GM still works below.")
if not _ai_ok:
    st.warning(_ai_status)

# ----------------------------
# CHAT STATE (opens clean; past threads via the footer)
# ----------------------------
history_key = f"gm_messages:{user_id}:{league_id}:{league_team_id}:front_office"
pending_hash_key = f"gm_pending_prompt_hash:{user_id}:{league_id}:{league_team_id}:front_office"

if history_key not in st.session_state:
    st.session_state[history_key] = [{"role": "assistant", "content": front_office_opening(owner_team_name, _ai_ok)}]

gm_messages = st.session_state[history_key]

_clicked = render_fo_prompts(fo["week"] if fo else None)
if _clicked:
    st.session_state.gm_pending_prompt = _clicked


def _render_chat_markdown(text: str) -> None:
    """Render chat text; escape $ so Streamlit doesn't treat $..$ as LaTeX."""
    st.markdown(str(text).replace("$", "\\$"))


for msg in gm_messages:
    with st.chat_message(msg["role"], avatar=("🏈" if msg["role"] == "assistant" else None)):
        _render_chat_markdown(msg["content"])

# ----------------------------
# CHAT INPUT
# ----------------------------
typed_prompt = st.chat_input("Ask your assistant GM...")
prompt = st.session_state.pop("gm_pending_prompt", None) or typed_prompt

if prompt:
    prompt_hash = sha256(f"{history_key}:{prompt}".encode("utf-8")).hexdigest()
    if st.session_state.get(pending_hash_key) == prompt_hash:
        st.stop()
    st.session_state[pending_hash_key] = prompt_hash
    gm_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        _render_chat_markdown(prompt)
    with st.chat_message("assistant", avatar="🏈"):
        with st.spinner("Working on it..."):
            result = run_league_ai(request_context=request_context, team=team, question=prompt, history=gm_messages[:-1])
            print(result.trace_line(), flush=True)
            response = result.text if result.ok else result.human_error
            if result.ok:
                _render_chat_markdown(response)
            else:
                st.error(response)
            st.session_state.pop(pending_hash_key, None)
    gm_messages.append({"role": "assistant", "content": response})

# ----------------------------
# FOOTER: new / past conversations
# ----------------------------
f1, f2, f3 = st.columns([1.2, 1.5, 6])
with f1:
    if st.button("New conversation", key="fo_new_conversation"):
        start_new_conversation()
        st.session_state[history_key] = [{"role": "assistant", "content": front_office_opening(owner_team_name, _ai_ok)}]
        st.session_state.pop("fo_show_past", None)
        st.rerun()
with f2:
    if st.button("Past conversations", key="fo_past_conversations"):
        st.session_state["fo_show_past"] = not st.session_state.get("fo_show_past", False)

if st.session_state.get("fo_show_past"):
    past = list_conversations(request_context)  # titles only; one small query
    if not past:
        st.caption("No saved conversations yet.")
    else:
        labels = [f"{str(c.get('updated_at') or '')[:10]} · {c.get('title') or 'Conversation'}" for c in past]
        choice = st.selectbox("Open a past conversation", ["—", *labels], key="fo_past_choice", label_visibility="collapsed")
        if choice != "—":
            chosen = past[labels.index(choice)]
            loaded = load_conversation(request_context, str(chosen["id"]))
            if loaded:
                st.session_state[history_key] = loaded
                st.session_state.pop("fo_show_past", None)
                st.session_state.pop("fo_past_choice", None)
                st.rerun()

# ----------------------------
# WEEKLY BRIEF (filled last so the page is usable immediately; cached per week)
# ----------------------------
if fo and brief_slot is not None:
    _brief = None
    if _ai_ok:
        try:
            _brief = weekly_brief(request_context=request_context, team=team, fo=fo)
        except Exception as exc:
            print(f"FRONT_OFFICE_BRIEF_ERROR {type(exc).__name__}: {exc}", flush=True)
    from league_ai.front_office import merge_brief as _merge
    with brief_slot.container():
        render_brief(_merge(fo["data_brief"], _brief))
