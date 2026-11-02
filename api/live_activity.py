"""The lock screen Live Activity for an owner's live matchup: what it shows, and how it's sent.

`props` turns one box score (box_score.game, the same numbers as the app's game screen) into the
small payload the widget draws: both scores, projected finals, win odds, starters playing / left and
the latest points change. The app starts the activity with these props; after that the game-day
watcher (scripts/live_activity_watcher.py) pushes fresh props to Apple whenever they change.

Apple caps a Live Activity payload at 4 KB, so names are short and only display text is sent.
"""
from __future__ import annotations

import json
import time
from typing import Any

NAME = "LiveMatchup"  # must match createLiveActivity('LiveMatchup', ...) in the app
MAX_NAME = 12
MIN_CHANGE = 0.05  # points; smaller moves are stat corrections in the noise


def short_name(full: str) -> str:
    """"Ja'Marr Chase" -> "J. Chase" (the widget has one line per play)."""
    parts = [p for p in str(full or "").split() if p]
    if len(parts) < 2:
        return parts[0] if parts else ""
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def owner_label(name: str) -> str:
    text = str(name or "").strip()
    return (text[:MAX_NAME].rstrip() if len(text) > MAX_NAME else text).upper()


def _side(side: dict[str, Any]) -> dict[str, Any]:
    players = side.get("players") or []
    return {
        "name": owner_label(side.get("name") or ""),
        "score": round(float(side.get("score") or 0), 1),
        "proj": round(float(side.get("projected") or side.get("score") or 0)),
        "playing": sum(p.get("status") == "playing" for p in players),
        "left": sum(p.get("status") == "waiting" for p in players),
    }


def game_text(game: dict[str, Any] | None) -> str:
    """"CIN 17-10 Q2" from the player's side of his NFL game; "" when unknown."""
    if not game:
        return ""
    team_score, opp_score = int(game.get("score") or 0), int(game.get("opp_score") or 0)
    state = game.get("state")
    when = "Final" if state == "post" else (f"Q{game.get('quarter')}" if game.get("quarter") else "")
    lead = f"{team_score}–{opp_score}"
    return f"{lead} {when}".strip()


def player_points(result: dict[str, Any]) -> dict[str, float]:
    """player id -> points for every starter in the game, the watcher's memory between checks."""
    return {p["player_id"]: float(p.get("points") or 0) for side in result.get("teams") or []
            for p in side.get("players") or [] if p.get("player_id") not in (None, "", "0")}


def latest_change(result: dict[str, Any], my_roster_id: int | None, before: dict[str, float] | None) -> dict[str, Any] | None:
    """The biggest points move since the last check (either side), as display text."""
    if not before:
        return None
    best: tuple[float, dict[str, Any], bool] | None = None
    for side in result.get("teams") or []:
        mine = side.get("roster_id") == my_roster_id
        for p in side.get("players") or []:
            pid = p.get("player_id")
            if pid not in before:
                continue
            delta = float(p.get("points") or 0) - before[pid]
            if abs(delta) >= MIN_CHANGE and (best is None or abs(delta) > abs(best[0])):
                best = (delta, p, mine)
    if best is None:
        return None
    delta, p, mine = best
    stats = " ".join(item["text"] for item in (p.get("line") or [])[:3])
    team = p.get("team") or ""
    game = game_text(p.get("game"))
    return {
        "pts": f"{delta:+.1f}",
        "good": delta > 0,
        "mine": mine,
        "who": short_name(p.get("name") or ""),
        "what": stats,
        "game": f"{team} {game}".strip() if game else "",
    }


def top_scorer(result: dict[str, Any], my_roster_id: int | None) -> dict[str, Any] | None:
    """Before any change is seen (just started): my best starter so far, in the same shape."""
    mine = next((s for s in result.get("teams") or [] if s.get("roster_id") == my_roster_id), None)
    best = max((p for p in (mine or {}).get("players") or [] if float(p.get("points") or 0) > 0),
               key=lambda p: float(p.get("points") or 0), default=None)
    if not best:
        return None
    return {"pts": f"{float(best['points']):.1f}", "good": True, "mine": True, "who": short_name(best.get("name") or ""),
            "what": "top scorer", "game": ""}


def props(result: dict[str, Any], my_roster_id: int | None, before: dict[str, float] | None = None,
          last: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The widget's props for one owner, me first. None when the game has no such owner."""
    sides = result.get("teams") or []
    me = next((s for s in sides if s.get("roster_id") == my_roster_id), None)
    them = next((s for s in sides if s.get("roster_id") != my_roster_id), None)
    if not me or not them:
        return None
    odds = result.get("win_chance")
    my_odds = None
    if odds and len(odds) == 2:
        my_odds = odds[0] if sides[0] is me else odds[1]
    change = latest_change(result, my_roster_id, before) or last or top_scorer(result, my_roster_id)
    return {
        "week": int(result.get("week") or 0),
        "me": _side(me),
        "them": _side(them),
        "odds": my_odds,
        "last": change,
        "final": bool(result.get("is_final")),
    }


def changed(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """Push only when something on screen moves (Apple throttles chatty apps)."""
    if old is None:
        return True
    keys = ("me", "them", "odds", "final", "last")
    return any(old.get(k) != new.get(k) for k in keys)


def content_state(p: dict[str, Any]) -> dict[str, Any]:
    """expo-widgets' ContentState: the activity name plus the props as a JSON string."""
    return {"name": NAME, "props": json.dumps(p, separators=(",", ":"))}


def apns_payload(event: str, p: dict[str, Any], now: float | None = None, dismiss_after: int | None = None,
                 stale_after: int | None = None) -> dict[str, Any]:
    """A liveactivity push: event is "update" or "end"."""
    ts = int(now if now is not None else time.time())
    aps: dict[str, Any] = {"timestamp": ts, "event": event, "content-state": content_state(p)}
    if stale_after:
        aps["stale-date"] = ts + stale_after
    if event == "end" and dismiss_after is not None:
        aps["dismissal-date"] = ts + dismiss_after
    return {"aps": aps}


SOON_MINUTES = 60


def is_live(result: dict[str, Any], now_ts: float | None = None) -> bool:
    """A starter on either side is playing, or kicks off within the hour (the lock screen is worth it)."""
    if result.get("is_final"):
        return False
    now_ts = now_ts if now_ts is not None else time.time()
    from datetime import datetime
    for side in result.get("teams") or []:
        for p in side.get("players") or []:
            if p.get("status") == "playing":
                return True
            if p.get("status") == "waiting" and p.get("kickoff"):
                try:
                    kick = datetime.fromisoformat(p["kickoff"]).timestamp()
                except ValueError:
                    continue
                if kick - now_ts <= SOON_MINUTES * 60:
                    return True
    return False


def deep_link(week: int, matchup_id: int) -> str:
    return f"legacydynasty://game/{week}/{matchup_id}"
