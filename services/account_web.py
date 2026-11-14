"""Website side of the App Store account features, so the website and the app match:
report a GM answer, delete my account, and page-view counts (same tables as the app, via api/account.py
and api/usage_log.py)."""
from __future__ import annotations

import os

import streamlit as st

from api import account, usage_log

_REASONS = {"It's wrong": "wrong", "It's out of date": "outdated", "Offensive or harmful": "harmful", "Something else": "other"}


def _client_and_user():
    from auth import auth_client, current_user
    user = current_user() or {}
    return auth_client(), str(user.get("id") or ""), str(user.get("email") or "")


def render_report(key: str, question: str | None, answer: str, league_id: str | None = None, conversation_id: str | None = None) -> None:
    """A small "Report" popover under a GM answer."""
    with st.popover("⚑ Report", use_container_width=False):
        choice = st.radio("What's wrong with this answer?", list(_REASONS), key=f"report_reason_{key}")
        note = st.text_input("Anything to add? (optional)", key=f"report_note_{key}", max_chars=500)
        if st.button("Send report", key=f"report_send_{key}"):
            client, user_id, _ = _client_and_user()
            try:
                account.report_answer(client, user_id=user_id, league_id=league_id, reason=_REASONS[choice], question=question,
                                      answer=answer, conversation_id=conversation_id, note=note, source="website")
                st.success("Thanks, we'll look at this answer.")
            except ValueError as exc:
                st.error(str(exc))


def delete_account() -> str | None:
    """Delete the signed-in owner's account. Returns an error message, or None when it worked."""
    client, _, _ = _client_and_user()
    try:
        account.delete_account(client)
    except ValueError as exc:
        return str(exc)
    return None


def log_page_view(page_file: str) -> None:
    """One row per page per browser session in app_screen_views (source 'website'). Never breaks the page."""
    try:
        name = "web/" + os.path.splitext(os.path.basename(page_file))[0]
        seen = st.session_state.setdefault("_pages_logged", set())
        if name in seen:
            return
        client, user_id, email = _client_and_user()
        if not user_id or email.lower() == "applereview@legacydynasty.app":
            return
        seen.add(name)
        usage_log.save(client, user_id=user_id, league_id=st.session_state.get("active_league_id"),
                       views=[{"screen": name, "seconds": 0}], build=None, source="website")
    except Exception:
        pass
