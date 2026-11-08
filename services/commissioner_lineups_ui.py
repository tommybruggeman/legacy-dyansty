"""Website commissioner tool "Lineups": set any team's lineup for an owner who's away.

For the weeks the app runs itself (league switch on). Same slot rules and kickoff locks as the owner's own lineup
(commissioner_set_lineup_authenticated). Scores are never edited by hand: stat corrections come in automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

from api import commissioner, league, lineups, own_league


def _rows(sb: Any, table: str, cols: str, **eq: Any) -> list[dict[str, Any]]:
    query = sb.table(table).select(cols)
    for key, value in eq.items():
        query = query.eq(key, value)
    try:
        return query.execute().data or []
    except Exception:
        return []


def render_commissioner_lineups(sb: Any, league_id: str, season: int | None) -> None:
    st.markdown("### Lineups")
    if st.session_state.get("commish_lineup_done"):
        st.success(st.session_state.pop("commish_lineup_done"))
    switch = (_rows(sb, "league_app_transactions", "enabled", league_id=league_id) or [{}])[0]
    if not switch.get("enabled") or not season:
        st.info("Lineups move into the app with the playoffs. Until then they're set in Sleeper.")
        return
    settings = (_rows(sb, "league_ai_settings", "roster_positions", league_id=league_id) or [{}])[0]
    slots = lineups.starter_slots(settings.get("roster_positions") or [])
    if not slots:
        st.caption(lineups.ERRORS["lineup_slots_missing"])
        return
    week = league.current_week()
    st.caption(f"Set a lineup for an owner who's away. Week {week}. Players lock when their game kicks off, same as for owners.")

    teams = _rows(sb, "league_teams", "id,team_name,owner_name,sleeper_roster_id", league_id=league_id)
    label = {str(t["id"]): str(t.get("owner_name") or t.get("team_name") or "Team") for t in teams}
    team_id = st.selectbox("Team", sorted(label, key=label.get), format_func=label.get, key="commish_lineup_team")
    rid = next((int(t["sleeper_roster_id"]) for t in teams if str(t["id"]) == team_id and t.get("sleeper_roster_id") is not None), None)
    roster = (own_league.app_rosters(sb, league_id, int(season), {team_id: rid}) or [{}])[0] if rid is not None else {}
    inactive = {str(p) for p in roster.get("taxi", []) + roster.get("reserve", [])}
    active = [str(p) for p in roster.get("players", []) if str(p) not in inactive]
    info: dict[str, dict[str, Any]] = {}
    if active:
        try:
            info = {str(r["sleeper_id"]): r for r in sb.table("nfl_players").select("sleeper_id, full_name, position, team")
                    .in_("sleeper_id", active).execute().data or []}
        except Exception:
            info = {}
    name = lambda p: f"{(info.get(p) or {}).get('full_name') or p} ({(info.get(p) or {}).get('position') or '?'})" if p else "Empty"
    saved = sorted((r for r in _rows(sb, "league_lineups", "week,slots", league_id=league_id, league_team_id=team_id, season=int(season))
                    if int(r.get("week") or 0) <= week), key=lambda r: -int(r["week"]))
    ids = lineups.effective(slots, saved, [], set(active))
    starts = lineups.kickoffs(_rows(sb, "nfl_games", "gameday,gametime,home_team,away_team", season=int(season), week=week))
    now = datetime.now(timezone.utc)
    locked = {p for p in active if lineups.is_locked(p, (info.get(p) or {}).get("team"), starts, now)}

    picks: list[str | None] = []
    for i, slot in enumerate(slots):
        current = ids[i]
        if current and current in locked:
            st.text_input(lineups.label(slot), value=f"🔒 {name(current)}", disabled=True, key=f"commish_lineup_{team_id}_{i}")
            picks.append(current)
            continue
        options = [None] + sorted((p for p in active if lineups.fits(slot, str((info.get(p) or {}).get("position") or ""))
                                   and p not in locked), key=name)
        picks.append(st.selectbox(lineups.label(slot), options, index=options.index(current) if current in options else 0,
                                  format_func=name, key=f"commish_lineup_{team_id}_{i}"))
    if st.button("Save lineup", key=f"commish_lineup_save_{team_id}", use_container_width=True):
        chosen = [p for p in picks if p]
        if len(chosen) != len(set(chosen)):
            st.error(lineups.ERRORS["lineup_duplicate"])
            return
        try:
            commissioner.set_lineup(sb, league_id=league_id, team_id=team_id, season=int(season), week=week, slots=slots, player_ids=picks)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state["commish_lineup_done"] = f"Lineup saved for {label.get(team_id, 'the team')}."
        st.rerun()
