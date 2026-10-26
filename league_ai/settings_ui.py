"""Streamlit form for League AI settings (commissioner only)."""
from __future__ import annotations

from typing import Any

import streamlit as st

from league_ai.settings_store import (
    apply_sleeper_scoring,
    fetch_sleeper_scoring,
    load_settings,
    save_settings,
    scoring_summary,
)


def render_league_ai_settings(client: Any, league_id: str, sleeper_league_id: str | None) -> None:
    st.caption("Roster, IR/taxi, FAAB and standings rules are edited on League rules; the AI reads them from there.")

    s = load_settings(client, league_id)

    st.markdown("#### Scoring (from Sleeper)")
    st.write(scoring_summary(s))
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("Sync scoring from Sleeper", use_container_width=True, disabled=not sleeper_league_id):
            try:
                payload = fetch_sleeper_scoring(sleeper_league_id or "")
                apply_sleeper_scoring(s, payload)
                save_settings(client, s)
                st.success("Scoring synced.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not sync scoring: {exc}")
    with c2:
        st.caption(f"Last synced: {s.scoring_synced_at or 'never'}")

    s.house_rules = st.text_area(
        "House rules and anything else the AI should know about this league",
        value=s.house_rules,
        height=160,
        help="Plain English. Waiver timing, keeper quirks, unwritten rules, how the league likes to trade.",
    )

    if st.button("Save house rules", use_container_width=True):
        try:
            save_settings(client, s)
            st.success("League AI settings saved.")
        except Exception as exc:
            st.error(f"Could not save: {exc}")

    render_weekly_reports(client, league_id)


def render_weekly_reports(client: Any, league_id: str) -> None:
    """Commissioner view of the weekly League AI report (also emailed when SMTP is set up)."""
    st.markdown("#### Weekly AI report")
    try:
        rows = (client.table("league_ai_reports").select("week_start, week_end, html").eq("league_id", league_id)
                .order("week_start", desc=True).limit(26).execute().data or [])
    except Exception:
        rows = []
    if not rows:
        st.caption("No reports yet. The first one is written the Tuesday after the AI has been used for a week.")
        return
    labels = [f"{r['week_start']} to {r['week_end']}" for r in rows]
    choice = st.selectbox("Week", labels, key="league_ai_report_week")
    report = rows[labels.index(choice)]
    import streamlit.components.v1 as components

    components.html(f"<div style='background:#fff;color:#111;padding:16px;border-radius:8px'>{report.get('html') or ''}</div>", height=900, scrolling=True)
