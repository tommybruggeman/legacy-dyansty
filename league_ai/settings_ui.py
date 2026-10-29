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

    render_scoring_editor(client, s, sleeper_league_id)

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


def render_scoring_editor(client: Any, s: Any, sleeper_league_id: str | None) -> None:
    """The league's scoring rules, edited and saved here. This copy is what scores the league (scoring/engine.py)."""
    import pandas as pd

    from scoring.rules import rows_from_rules, rules_from_rows

    st.markdown("#### Scoring")
    st.write(scoring_summary(s))
    source = "copied from Sleeper" if s.scoring_source == "sleeper" else "set here"
    st.caption(f"Points per stat. Rules are {source}" + (f" (last copied {s.scoring_synced_at[:10]})." if s.scoring_source == "sleeper" and s.scoring_synced_at else "."))

    rows = rows_from_rules(s.scoring_settings)
    edited: list[dict[str, Any]] = []
    for group in dict.fromkeys(r["group"] for r in rows):
        mine = [r for r in rows if r["group"] == group]
        used = sum(1 for r in mine if r["points"])
        with st.expander(f"{group} ({used} in use)", expanded=False):
            table = st.data_editor(
                pd.DataFrame(mine)[["rule", "points", "key"]],
                key=f"scoring_rules_{group}",
                hide_index=True,
                use_container_width=True,
                disabled=["rule", "key"],
                column_config={
                    "rule": st.column_config.TextColumn("Rule"),
                    "points": st.column_config.NumberColumn("Points", step=0.01, format="%.2f"),
                    "key": None,
                },
            )
            edited += table.to_dict("records")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save scoring", type="primary", use_container_width=True):
            try:
                s.scoring_settings = rules_from_rows(edited)
                s.scoring_source = "app"
                save_settings(client, s)
                st.success("Scoring saved.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save scoring: {exc}")
    with c2:
        if st.button("Copy rules from Sleeper", use_container_width=True, disabled=not sleeper_league_id,
                     help="Replaces the rules above with this league's current Sleeper scoring."):
            try:
                payload = fetch_sleeper_scoring(sleeper_league_id or "")
                apply_sleeper_scoring(s, payload)
                save_settings(client, s)
                st.success("Scoring copied from Sleeper.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not copy scoring: {exc}")


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
