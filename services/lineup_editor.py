"""Website lineup card on My Team: the same lineup the iOS app edits, saved through the same checked database call.

Only shows while the league's switch (league_app_transactions.enabled) is on. Uses api.lineups for the slot rules,
the carry-forward lineup and kickoff locks, so the website and the app always agree.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import requests
import streamlit as st

from api import lineups


def _rows(sb: Any, table: str, cols: str, **eq: Any) -> list[dict[str, Any]]:
    query = sb.table(table).select(cols)
    for key, value in eq.items():
        query = query.eq(key, value)
    try:
        return query.execute().data or []
    except Exception:
        return []


def _sleeper(path: str) -> Any:
    try:
        return requests.get(f"https://api.sleeper.app/v1/{path}", timeout=15).json()
    except Exception:
        return None


def render_lineup_editor(sb: Any, *, league_id: str, season: int, team_id: str, sleeper_league_id: str | None,
                         roster: Iterable[Mapping[str, Any]]) -> None:
    on = _rows(sb, "league_app_transactions", "enabled", league_id=league_id)
    if not (on and on[0].get("enabled")):
        return
    settings = _rows(sb, "league_ai_settings", "roster_positions", league_id=league_id)
    slots = lineups.starter_slots((settings[0].get("roster_positions") if settings else None) or [])
    week = int(((_sleeper("state/nfl") or {}).get("week")) or 1)

    players = {str(r.get("sleeper_id")): r for r in roster
               if r.get("sleeper_id") and str(r.get("roster_designation") or "").lower() not in {"taxi", "ir"}}
    saved = sorted((r for r in _rows(sb, "league_lineups", "week,slots", league_id=league_id, league_team_id=team_id, season=season)
                    if int(r.get("week") or 0) <= week), key=lambda r: -int(r["week"]))
    sleeper_starters: list[str] = []
    if not saved and sleeper_league_id:
        mine = next((r for r in _sleeper(f"league/{sleeper_league_id}/rosters") or []
                     if any(str(p) in players for p in r.get("players") or [])), {})
        sleeper_starters = [str(p) for p in mine.get("starters") or []]
    ids = lineups.effective(slots, saved, sleeper_starters, set(players))

    games = _rows(sb, "nfl_games", "gameday,gametime,home_team,away_team", season=season, week=week)
    starts, now = lineups.kickoffs(games), datetime.now(timezone.utc)
    pos = {pid: str(r.get("player_position") or r.get("pos") or "") for pid, r in players.items()}
    team_of: dict[str, str] = {}
    if players:
        try:
            info = sb.table("nfl_players").select("sleeper_id,team").in_("sleeper_id", list(players)).execute().data or []
            team_of = {str(r["sleeper_id"]): str(r.get("team") or "") for r in info}
        except Exception:
            pass
    locked = {pid for pid in players if lineups.is_locked(pid, team_of.get(pid), starts, now)}
    name = lambda pid: f"{players[pid].get('player') or pid} ({pos.get(pid, '')})" if pid else "Empty"

    with st.container(border=True, key="my_team_lineup"):
        st.markdown(f"### Week {week} lineup")
        if not slots:
            st.caption(lineups.ERRORS["lineup_slots_missing"])
            return
        st.caption("Players lock when their game kicks off.")
        picks: list[str | None] = []
        for i, slot in enumerate(slots):
            current = ids[i]
            if current and current in locked:
                st.text_input(lineups.label(slot), value=f"🔒 {name(current)}", disabled=True, key=f"lineup_{week}_{i}")
                picks.append(current)
                continue
            options = [None] + sorted((pid for pid in players if lineups.fits(slot, pos.get(pid, "")) and pid not in locked),
                                      key=lambda pid: name(pid))
            if current and current not in options:
                options.insert(1, current)
            picks.append(st.selectbox(lineups.label(slot), options, index=options.index(current), format_func=name, key=f"lineup_{week}_{i}"))
        if st.button("Save lineup", key=f"lineup_save_{week}", use_container_width=True):
            chosen = [p for p in picks if p]
            if len(chosen) != len(set(chosen)):
                st.error(lineups.ERRORS["lineup_duplicate"])
                return
            try:
                sb.rpc("set_lineup_authenticated", {"p_request": {
                    "league_id": league_id, "season": season, "week": week,
                    "slots": [{"slot": s, "player_id": picks[i]} for i, s in enumerate(slots)],
                }}).execute()
                st.success("Lineup saved.")
            except Exception as exc:
                body = getattr(getattr(exc, "response", None), "text", "")  # the database's error code is in the body
                st.error(lineups.friendly(Exception(f"{exc} {body}")))
