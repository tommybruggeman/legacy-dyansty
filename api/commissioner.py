"""Commissioner lineups for the weeks the app runs itself (api/own_league.py): set any team's lineup for an owner
who's away, under the same slot rules and kickoff locks as the owner (commissioner_set_lineup_authenticated).

Scores are never edited by hand. Stat corrections come in automatically with the official stats
(scripts/final_scores.py), and the whole league gets a push when one changes a score.
"""
from __future__ import annotations

from typing import Any, Mapping

ERRORS = {
    "Commissioner authority required": "Only the commissioner can do that.",
    "lineup_team_invalid": "That team isn't in this league.",
}


def friendly(exc: Exception) -> str:
    from api import lineups
    text = str(exc)
    for code, message in {**ERRORS, **lineups.ERRORS}.items():
        if code in text:
            return message
    return "That didn't save. Try again."


def _call(client: Any, fn: str, request: Mapping[str, Any]) -> Any:
    try:
        return client.rpc(fn, {"p_request": dict(request)}).execute().data
    except Exception as exc:
        body = getattr(getattr(exc, "response", None), "text", "")  # the database's error code is in the body
        raise ValueError(friendly(Exception(f"{exc} {body}"))) from None


def set_lineup(client: Any, *, league_id: str, team_id: str, season: int, week: int, slots: list[str],
               player_ids: list[str | None]) -> Any:
    """Any team's starters for a week, in slot order."""
    return _call(client, "commissioner_set_lineup_authenticated", {
        "league_id": league_id, "team_id": team_id, "season": season, "week": week,
        "slots": [{"slot": s, "player_id": player_ids[i] or None} for i, s in enumerate(slots)]})
