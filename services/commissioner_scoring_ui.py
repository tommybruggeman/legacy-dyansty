"""Website commissioner tool "Scores": fix a score, set any team's lineup, finalize a week.

For the weeks the app scores itself (league switch on, from own_from_week). Reads the week's rows the same way every
page does (api.own_league.sync, then api.sleeper's overrides) and writes through the commissioner-only database
functions in api/commissioner.py, so the website and the iOS app always show the same numbers.
"""
from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from api import commissioner, league, lineups, own_league
from api import sleeper as sleeper_api


def _rows(sb: Any, table: str, cols: str, **eq: Any) -> list[dict[str, Any]]:
    query = sb.table(table).select(cols)
    for key, value in eq.items():
        query = query.eq(key, value)
    try:
        return query.execute().data or []
    except Exception:
        return []


def _names(sb: Any, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        rows = sb.table("nfl_players").select("sleeper_id, full_name, position").in_("sleeper_id", sorted(ids)).execute().data or []
    except Exception:
        rows = []
    return {str(r["sleeper_id"]): f"{r.get('full_name') or r['sleeper_id']} ({r.get('position') or '?'})" for r in rows}


def _pts(value: Any) -> str:
    return f"{float(value or 0):.2f}"


def render_commissioner_scores(sb: Any, league_id: str, sleeper_league_id: str | None, season: int | None) -> None:
    st.markdown("### Scores")
    if st.session_state.get("commish_scores_done"):
        st.success(st.session_state.pop("commish_scores_done"))
    switch = (_rows(sb, "league_app_transactions", "enabled,own_from_week", league_id=league_id) or [{}])[0]
    try:  # the active season's Sleeper league (the score feed for now)
        from season_engine import SeasonResolver
        active = SeasonResolver(sb).get_active_season(league_id)
        sleeper_league_id, season = active.sleeper_league_id or sleeper_league_id, active.season or season
    except Exception:
        pass
    if not sleeper_league_id or not season:
        st.info("The active season isn't linked yet.")
        return
    settings = (_rows(sb, "league_ai_settings", "roster_positions,playoff_week_start", league_id=league_id) or [{}])[0]
    own_from = int(switch.get("own_from_week") or settings.get("playoff_week_start") or 15)
    if not switch.get("enabled"):
        st.info(f"These tools work on the weeks the app scores itself: week {own_from} on, once the league switch is on. "
                "Until then Sleeper's scores stand.")
        return

    current = league.current_week()
    weeks = list(range(own_from, max(own_from, current) + 1))
    week = st.selectbox("Week", weeks, index=len(weeks) - 1, key="commish_scores_week")
    try:
        own_league.sync(sb, league_id, sleeper_league_id, int(season))
    except Exception:
        st.warning("The week couldn't be scored right now. Try again in a minute.")
    rows = sleeper_api.get(f"league/{sleeper_league_id}/matchups/{week}", ttl=15) or []
    teams = _rows(sb, "league_teams", "id,team_name,owner_name,sleeper_roster_id", league_id=league_id)
    team_of_roster = {int(t["sleeper_roster_id"]): t for t in teams if t.get("sleeper_roster_id") is not None}
    by_team = {str(team_of_roster[int(r["roster_id"])]["id"]): r for r in rows if int(r.get("roster_id") or 0) in team_of_roster}
    label = {str(t["id"]): str(t.get("owner_name") or t.get("team_name") or "Team") for t in teams}
    final = bool(_rows(sb, "league_week_finals", "week", league_id=league_id, season=int(season), week=int(week)))
    fixes = [f for f in _rows(sb, "league_score_fixes", "week,league_team_id,player_id,points,note",
                              league_id=league_id, season=int(season)) if int(f.get("week") or 0) == int(week)]

    # The week at a glance
    with st.container(border=True):
        st.markdown(f"**Week {week}** · {'Final' if final else 'Not final'}")
        pairs: dict[Any, list[Mapping[str, Any]]] = {}
        for r in rows:
            pairs.setdefault(r.get("matchup_id"), []).append(r)
        for mid, pair in sorted(pairs.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
            names = []
            for r in pair:
                team = team_of_roster.get(int(r.get("roster_id") or 0)) or {}
                adj = f" ({float(r['adjustment']):+g})" if r.get("adjustment") else ""
                names.append(f"{label.get(str(team.get('id')), 'Team')} {_pts(r.get('points'))}{adj}")
            st.markdown((" vs ".join(names) if mid is not None else " · ".join(names)) or "-")
        if final:
            st.caption("Final: these scores are saved and won't change with stat corrections or roster moves. Fixes still apply.")
            if st.button("Un-finalize week", key=f"commish_unfinal_{week}"):
                _run(lambda: commissioner.finalize(sb, league_id=league_id, sleeper_id=sleeper_league_id, season=int(season),
                                                   week=int(week), final=False), "Week reopened.")
        else:
            st.caption("Finalize once the official stats are in (the morning after the week's last game).")
            if st.button("Finalize week", type="primary", key=f"commish_final_{week}"):
                _run(lambda: commissioner.finalize(sb, league_id=league_id, sleeper_id=sleeper_league_id, season=int(season),
                                                   week=int(week)), "Week finalized.")

    team_ids = [str(t["id"]) for t in sorted(teams, key=lambda t: label.get(str(t["id"]), ""))]
    names = _names(sb, {str(p) for r in rows for p in r.get("players") or []})

    # Fix a score
    with st.container(border=True):
        st.markdown("**Fix a score**")
        team_id = st.selectbox("Team", team_ids, format_func=lambda t: label.get(t, "Team"), key=f"commish_fix_team_{week}")
        row = by_team.get(team_id) or {}
        kind = st.segmented_control("Fix", ["Player's points", "Team points"], default="Player's points",
                                    key=f"commish_fix_kind_{week}") or "Player's points"
        player_id = None
        if kind == "Player's points":
            players = [str(p) for p in row.get("players") or []] or [""]
            player_id = st.selectbox("Player", players, format_func=lambda p: f"{names.get(p, p)} · {_pts((row.get('players_points') or {}).get(p))}",
                                     key=f"commish_fix_player_{week}")
            points = st.number_input("His points this week", value=float((row.get("players_points") or {}).get(player_id) or 0),
                                     step=0.1, format="%.2f", key=f"commish_fix_pts_{week}_{player_id}")
        else:
            points = st.number_input("Points to add (use a minus for a penalty)", value=0.0, step=0.5, format="%.2f",
                                     key=f"commish_fix_adj_{week}")
        note = st.text_input("Note (optional)", max_chars=200, key=f"commish_fix_note_{week}")
        if st.button("Save fix", key=f"commish_fix_save_{week}", disabled=kind == "Player's points" and not player_id):
            _run(lambda: commissioner.fix_score(sb, league_id=league_id, season=int(season), week=int(week), team_id=team_id,
                                                player_id=player_id, points=float(points), note=note), "Fix saved.")
        for f in fixes:
            who = names.get(str(f.get("player_id") or ""), "") or "Team points"
            line = f"{label.get(str(f['league_team_id']), 'Team')}: {who} → {float(f['points']):+g}" if not f.get("player_id") \
                else f"{label.get(str(f['league_team_id']), 'Team')}: {who} set to {_pts(f['points'])}"
            c1, c2 = st.columns([4, 1])
            c1.caption(line + (f" · {f['note']}" if f.get("note") else ""))
            if c2.button("Remove", key=f"commish_fix_rm_{week}_{f['league_team_id']}_{f.get('player_id') or 'team'}"):
                _run(lambda f=f: commissioner.fix_score(sb, league_id=league_id, season=int(season), week=int(week),
                                                        team_id=str(f["league_team_id"]), player_id=f.get("player_id") or None,
                                                        points=None), "Fix removed.")

    # Set a lineup
    with st.container(border=True):
        st.markdown("**Set a lineup**")
        st.caption("Sets any team's starters for this week, even after kickoff.")
        slots = lineups.starter_slots(settings.get("roster_positions") or [])
        if not slots:
            st.caption(lineups.ERRORS["lineup_slots_missing"])
            return
        lt = st.selectbox("Team", team_ids, format_func=lambda t: label.get(t, "Team"), key=f"commish_lineup_team_{week}")
        lrow = by_team.get(lt) or {}
        roster = next((r for r in own_league.app_rosters(sb, league_id, int(season), {lt: int(lrow["roster_id"])})
                       if lrow.get("roster_id") is not None), None) if lrow else None
        inactive = {str(p) for p in (roster or {}).get("taxi", []) + (roster or {}).get("reserve", [])}
        active = [str(p) for p in (roster or {}).get("players", []) if str(p) not in inactive]
        pnames = _names(sb, set(active))
        pos = {p: pnames.get(p, "").rsplit("(", 1)[-1].rstrip(")") for p in active}
        current_ids = [str(p) if str(p) not in ("", "0") else None for p in lrow.get("starters") or []]
        picks: list[str | None] = []
        for i, slot in enumerate(slots):
            options = [None] + sorted((p for p in active if lineups.fits(slot, pos.get(p, ""))), key=lambda p: pnames.get(p, p))
            now = current_ids[i] if i < len(current_ids) else None
            if now and now not in options:
                options.insert(1, now)
            picks.append(st.selectbox(lineups.label(slot), options, index=options.index(now) if now in options else 0,
                                      format_func=lambda p: pnames.get(p, p) if p else "Empty", key=f"commish_lineup_{week}_{lt}_{i}"))
        if st.button("Save lineup", key=f"commish_lineup_save_{week}_{lt}"):
            chosen = [p for p in picks if p]
            if len(chosen) != len(set(chosen)):
                st.error(lineups.ERRORS["lineup_duplicate"])
                return
            _run(lambda: commissioner.set_lineup(sb, league_id=league_id, team_id=lt, season=int(season), week=int(week),
                                                 slots=slots, player_ids=picks), "Lineup saved.")


def _run(action, done: str) -> None:
    try:
        action()
    except ValueError as exc:
        st.error(str(exc))
        return
    from api import cache
    cache.forget("own_league")  # rescore with the change on the rerun
    st.session_state["commish_scores_done"] = done
    st.rerun()
