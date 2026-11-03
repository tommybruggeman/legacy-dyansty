"""Commissioner scoring tools for the weeks the app runs itself (api/own_league.py): fix a score, finalize a week.

- Score fixes (league_score_fixes): a player's points for one week set by hand (a stat the feed got wrong), or points
  added to a team's total (a ruling). Applied on top of every app-scored week, finalized or not, so standings and the
  bracket use the fixed numbers.
- Finalized weeks (league_week_finals): once a week's official stats are in, the commissioner finalizes it. That saves
  the week's rows as scored right then (before fixes), and from then on those rows are served, so stat corrections,
  roster moves and lineup edits never change a finished week. Un-finalizing deletes the row.
- Lineups for any team: commissioner_set_lineup_authenticated (same rules as an owner's, no kickoff locks).

Writes go through the commissioner-only database functions (wrappers below, shared by the website and the API).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

ERRORS = {
    "Commissioner authority required": "Only the commissioner can do that.",
    "fix_week_invalid": "That week can't be changed.",
    "fix_team_invalid": "That team isn't in this league.",
    "fix_points_invalid": "Points must be between -500 and 500.",
    "finalize_week_no_official_stats": "That week's official stats aren't in yet. They land the morning after its last game.",
    "finalize_rows_missing": "That week has no app scores to save yet.",
    "lineup_week_finalized": "That week is final. Un-finalize it first to change a lineup.",
}


def friendly(exc: Exception) -> str:
    from api import lineups
    text = str(exc)
    for code, message in {**ERRORS, **lineups.ERRORS}.items():
        if code in text:
            return message
    return "That didn't save. Try again."


def load(client: Any, league_id: str, season: int) -> dict[str, Any]:
    """{"fixes": [...], "finals": {week: rows or None}} for one league season (empty when the tables don't exist yet)."""
    try:
        fixes = (client.table("league_score_fixes").select("week,league_team_id,player_id,points,note")
                 .eq("league_id", league_id).eq("season", season).execute().data or [])
    except Exception:
        fixes = []
    try:
        finals = {int(r["week"]): r.get("rows") for r in
                  (client.table("league_week_finals").select("week,rows").eq("league_id", league_id).eq("season", season)
                   .execute().data or [])}
    except Exception:
        finals = {}
    return {"fixes": fixes, "finals": finals}


def apply_fixes(rows: Iterable[Mapping[str, Any]], fixes: Iterable[Mapping[str, Any]], week: int,
                team_of_roster: Mapping[int, str]) -> list[dict[str, Any]]:
    """Sleeper-shaped matchup rows with the week's fixes applied: player points replaced, team adjustments added.
    Each fixed row gets "adjustment" (team points added) and "fixed_players" (player ids set by hand)."""
    week_fixes = [f for f in fixes if int(f.get("week") or 0) == week]
    out = []
    for row in rows:
        team = team_of_roster.get(int(row.get("roster_id") or 0))
        mine = [f for f in week_fixes if str(f.get("league_team_id")) == str(team)] if team else []
        if not mine:
            out.append(dict(row))
            continue
        starters = [str(p) for p in row.get("starters") or []]
        starters_points = list(row.get("starters_points") or [])
        players_points = dict(row.get("players_points") or {})
        adjustment, fixed = 0.0, []
        for f in mine:
            pid, pts = str(f.get("player_id") or ""), float(f.get("points") or 0)
            if not pid:
                adjustment += pts
                continue
            fixed.append(pid)
            players_points[pid] = pts
            for i, s in enumerate(starters):
                if s == pid and i < len(starters_points):
                    starters_points[i] = pts
        out.append({**row, "starters_points": starters_points, "players_points": players_points,
                    "points": round(sum(float(p or 0) for p in starters_points) + adjustment, 2),
                    "adjustment": round(adjustment, 2), "fixed_players": fixed})
    return out


def _call(client: Any, fn: str, request: Mapping[str, Any]) -> Any:
    try:
        return client.rpc(fn, {"p_request": dict(request)}).execute().data
    except Exception as exc:
        body = getattr(getattr(exc, "response", None), "text", "")  # the database's error code is in the body
        raise ValueError(friendly(Exception(f"{exc} {body}"))) from None


def fix_score(client: Any, *, league_id: str, season: int, week: int, team_id: str, player_id: str | None,
              points: float | None, note: str = "") -> Any:
    """Set (or with points=None, remove) a fix: a player's points for the week, or points added to the team."""
    return _call(client, "commissioner_fix_score_authenticated", {
        "league_id": league_id, "season": season, "week": week, "team_id": team_id,
        "player_id": player_id or None, "points": points, "note": note})


def finalize(client: Any, *, league_id: str, sleeper_id: str, season: int, week: int, final: bool = True) -> Any:
    """Finalize a week with its rows as scored by the latest sync (before fixes), or un-finalize it."""
    from api import own_league
    request: dict[str, Any] = {"league_id": league_id, "season": season, "week": week, "final": final}
    if final:
        request["rows"] = own_league.UNFIXED.get((str(sleeper_id), int(week))) or []
    return _call(client, "commissioner_finalize_week_authenticated", request)


def set_lineup(client: Any, *, league_id: str, team_id: str, season: int, week: int, slots: list[str],
               player_ids: list[str | None]) -> Any:
    """Any team's starters for a week (slot order), without kickoff locks."""
    return _call(client, "commissioner_set_lineup_authenticated", {
        "league_id": league_id, "team_id": team_id, "season": season, "week": week,
        "slots": [{"slot": s, "player_id": player_ids[i] or None} for i, s in enumerate(slots)]})
