"""Streamlit form for League AI settings (commissioner only)."""
from __future__ import annotations

from typing import Any

import streamlit as st

from league_ai.settings_store import (
    LeagueAISettings,
    apply_sleeper_scoring,
    fetch_sleeper_scoring,
    load_settings,
    save_settings,
    scoring_summary,
)


def render_league_ai_settings(client: Any, league_id: str, sleeper_league_id: str | None) -> None:
    st.markdown("### League AI")
    st.caption("Every league-specific rule the co-GM relies on. league_rules (cap, dead cap %, contract terms) is edited under League Rules.")

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

    st.markdown("#### Taxi and IR")
    a, b, c, d = st.columns(4)
    with a:
        s.taxi_cap_fraction = st.number_input("Taxi cap fraction", 0.0, 1.0, float(s.taxi_cap_fraction), 0.05)
    with b:
        s.ir_cap_fraction = st.number_input("IR cap fraction", 0.0, 1.0, float(s.ir_cap_fraction), 0.05)
    with c:
        s.taxi_limit = int(st.number_input("Taxi slots", 0, 10, int(s.taxi_limit)))
    with d:
        s.ir_limit = int(st.number_input("IR slots", 0, 10, int(s.ir_limit)))
    e, f, g = st.columns(3)
    with e:
        s.taxi_rookies_only = st.checkbox("Taxi is rookies only", value=bool(s.taxi_rookies_only))
    with f:
        s.taxi_locked_full_season = st.checkbox("Taxi locks for the season", value=bool(s.taxi_locked_full_season))
    with g:
        s.taxi_skips_contract_year = st.checkbox("Taxi season skips a contract year", value=bool(s.taxi_skips_contract_year))

    st.markdown("#### Roster, FAAB and trades")
    h, i, j, k = st.columns(4)
    with h:
        s.roster_max = int(st.number_input("Roster max (incl. IR/taxi)", 1, 60, int(s.roster_max)))
    with i:
        s.max_trade_teams = int(st.number_input("Max teams per trade", 2, 12, int(s.max_trade_teams)))
    with j:
        s.faab_dollar_per_salary = st.number_input("Salary per FAAB $", 0.0, 10.0, float(s.faab_dollar_per_salary), 0.25)
    with k:
        s.faab_zero_counts_as = st.number_input("$0 FAAB win counts as", 0.0, 10.0, float(s.faab_zero_counts_as), 1.0)
    l, m, n = st.columns(3)
    with l:
        s.faab_pickup_years = int(st.number_input("FAAB pickup contract years", 1, 5, int(s.faab_pickup_years)))
    with m:
        s.one_dollar_deals_no_dead_cap = st.checkbox("$1 deals carry no dead cap", value=bool(s.one_dollar_deals_no_dead_cap))
    with n:
        s.traded_faab_moves_cap = st.checkbox("Traded FAAB moves cap", value=bool(s.traded_faab_moves_cap))

    st.markdown("#### Standings and season")
    o, p, q, r = st.columns(4)
    with o:
        s.win_points = int(st.number_input("Points per win", 0, 10, int(s.win_points)))
    with p:
        s.top_scorer_bonus_points = int(st.number_input("Top-scorer bonus points", 0, 10, int(s.top_scorer_bonus_points)))
    with q:
        s.top_scorer_bonus_count = int(st.number_input("Top-scorer bonus: top N", 0, 12, int(s.top_scorer_bonus_count)))
    with r:
        s.playoff_teams = int(st.number_input("Playoff teams", 0, 12, int(s.playoff_teams)))
    t, u = st.columns(2)
    with t:
        s.standings_tiebreaker = st.text_input("Standings tiebreaker", value=s.standings_tiebreaker)
    with u:
        s.trade_deadline = st.text_input("Trade deadline (e.g. week 12)", value=s.trade_deadline)

    s.house_rules = st.text_area(
        "House rules and anything else the AI should know about this league",
        value=s.house_rules,
        height=160,
        help="Plain English. Waiver timing, keeper quirks, unwritten rules, how the league likes to trade.",
    )

    if st.button("Save League AI settings", use_container_width=True):
        try:
            save_settings(client, s)
            st.success("League AI settings saved.")
        except Exception as exc:
            st.error(f"Could not save: {exc}")
