"""ESPN team rosters -> NFL team for each player in nfl_players, plus a log of team moves (nfl_player_moves).

Sleeper's player file is a once-a-day download, so a deadline-day trade could sit stale in the
Front Office AI for most of a day. ESPN's 32 public team rosters move within minutes of a deal and
cost nothing, so this loader runs every hour: when ESPN shows a player on a different team than we
have, we take ESPN's team, clear the old depth-chart slot (it belonged to the old team) and log the
move so the AI can say "traded from LV to MIN on Nov 10". Moves Sleeper reports in its daily file
are logged too, and a recent ESPN move is never rolled back by an older Sleeper file.

Feeds stay swappable: this module only needs "player id/name/position -> team" from a roster source.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from pipeline.nfl_data.common import normalize_name, now_iso, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K"}
TEAM_ALIASES = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR", "LVR": "LV"}
FREE_AGENT = "FA"  # stored in nfl_player_moves for "no team" so the unique key works
# A move seen on ESPN wins over Sleeper's daily file for this long (Sleeper normally catches up within a day).
PROTECT_DAYS = 14
MOVES_TABLE = "nfl_player_moves"


def team_code(value: Any) -> str | None:
    code = (to_text(value) or "").upper()
    return TEAM_ALIASES.get(code, code) or None


def espn_team_ids(payload: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for sport in payload.get("sports") or []:
        for league in sport.get("leagues") or []:
            for t in league.get("teams") or []:
                tid = to_text((t.get("team") or {}).get("id"))
                if tid:
                    ids.append(tid)
    return ids


def roster_players(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One ESPN roster response -> [{espn_id, name, position, team}] for fantasy positions."""
    team = team_code((payload.get("team") or {}).get("abbreviation"))
    out: list[dict[str, Any]] = []
    if not team:
        return out
    for group in payload.get("athletes") or []:
        items = group.get("items") if isinstance(group, dict) and "items" in group else [group]
        for a in items or []:
            if not isinstance(a, dict):
                continue
            pos = to_text((a.get("position") or {}).get("abbreviation"))
            name = to_text(a.get("fullName")) or to_text(a.get("displayName"))
            if pos in KEEP_POSITIONS and name:
                out.append({"espn_id": to_text(a.get("id")), "name": name, "position": pos, "team": team})
    return out


def espn_teams_by_sleeper(espn_players: Iterable[Mapping[str, Any]], current: Mapping[str, Mapping[str, Any]],
                          crosswalk: Crosswalk | None) -> dict[str, str]:
    """sleeper_id -> ESPN team. Joins on the ESPN id crosswalk, then on a unique name + position match.

    A player listed on two rosters at once (mid-transaction) is left out until ESPN settles.
    """
    by_name: dict[tuple[str, str], list[str]] = {}
    for sid, row in current.items():
        key = (normalize_name(row.get("full_name")), to_text(row.get("position")) or "")
        by_name.setdefault(key, []).append(sid)
    teams: dict[str, set[str]] = {}
    for p in espn_players:
        sid = crosswalk.sleeper_for_espn(p.get("espn_id")) if (crosswalk and p.get("espn_id")) else None
        if sid and sid not in current:
            sid = None
        if not sid:
            hits = by_name.get((normalize_name(p.get("name")), p.get("position") or ""), [])
            sid = hits[0] if len(hits) == 1 else None
        if sid:
            teams.setdefault(sid, set()).add(p["team"])
    return {sid: next(iter(t)) for sid, t in teams.items() if len(t) == 1}


def move_row(sid: str, row: Mapping[str, Any], from_team: Any, to_team: Any, source: str, stamp: str) -> dict[str, Any]:
    return {
        "sleeper_id": str(sid),
        "full_name": row.get("full_name"),
        "position": row.get("position"),
        "from_team": team_code(from_team) or FREE_AGENT,
        "to_team": team_code(to_team) or FREE_AGENT,
        "source": source,
        "seen_at": stamp,
    }


def espn_changes(current: Mapping[str, Mapping[str, Any]], espn_team: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(nfl_players updates, move log rows) for players whose ESPN team differs from ours."""
    stamp = now_iso()
    updates, moves = [], []
    for sid, team in espn_team.items():
        row = current.get(sid) or {}
        have = team_code(row.get("team"))
        if have == team:
            continue
        updates.append({"sleeper_id": sid, "team": team, "depth_chart_order": None, "depth_chart_position": None, "refreshed_at": stamp})
        moves.append(move_row(sid, row, have, team, "espn_roster", stamp))
    return updates, moves


def sleeper_changes(current: Mapping[str, Mapping[str, Any]], new_rows: list[dict[str, Any]],
                    recent_moves: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Log Sleeper-reported moves, and keep a recent ESPN move from being undone by a Sleeper file that hasn't caught up.

    Mutates new_rows in place (team and depth chart) for protected players; returns move log rows.
    """
    stamp = now_iso()
    latest: dict[str, Mapping[str, Any]] = {}
    for m in sorted(recent_moves, key=lambda m: str(m.get("seen_at") or "")):
        latest[str(m.get("sleeper_id"))] = m
    moves = []
    for r in new_rows:
        sid = str(r.get("sleeper_id"))
        old = current.get(sid)
        if old is None:
            continue
        have, new = team_code(old.get("team")), team_code(r.get("team"))
        m = latest.get(sid)
        if m and m.get("source") == "espn_roster" and have == team_code(m.get("to_team")) and new != have:
            # Sleeper still shows the old team: keep ESPN's
            r["team"] = old.get("team")
            r["depth_chart_order"] = old.get("depth_chart_order")
            r["depth_chart_position"] = old.get("depth_chart_position")
            continue
        if have != new and (have or new):
            moves.append(move_row(sid, old, have, new, "sleeper", stamp))
    return moves


def recent_moves(client: Any, days: int = PROTECT_DAYS) -> list[dict[str, Any]] | None:
    """Moves logged in the last `days` days, or None when the moves table isn't created yet."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        return client.table(MOVES_TABLE).select("*").gte("seen_at", since).limit(2000).execute().data or []
    except Exception as exc:
        if _missing_table(exc):
            return None
        raise


def save_moves(client: Any, moves: list[dict[str, Any]]) -> int:
    if not moves:
        return 0
    try:
        client.table(MOVES_TABLE).upsert(moves, on_conflict="sleeper_id,from_team,to_team", ignore_duplicates=True).execute()
        return len(moves)
    except Exception as exc:
        if _missing_table(exc):
            print("[nfl_data] team moves not logged: run sql/nfl-player-moves.sql first", flush=True)
            return 0
        raise


def current_players(client: Any) -> dict[str, dict[str, Any]]:
    """Every nfl_players row (id, name, position, team, depth chart), paged past the 1,000-row API cap."""
    out: dict[str, dict[str, Any]] = {}
    step, start = 1000, 0
    while True:
        rows = (client.table("nfl_players").select("sleeper_id, full_name, position, team, depth_chart_order, depth_chart_position")
                .order("sleeper_id").range(start, start + step - 1).execute().data or [])
        for r in rows:
            out[str(r["sleeper_id"])] = r
        if len(rows) < step:
            return out
        start += step


def fetch_espn_players(fetch_json: Callable[..., Any]) -> tuple[list[dict[str, Any]], int]:
    """All 32 rosters. Returns (players, teams fetched); a team that fails is skipped, not fatal."""
    ids = espn_team_ids(fetch_json(ESPN_TEAMS_URL, timeout=60))
    players: list[dict[str, Any]] = []
    ok = 0
    for tid in ids:
        try:
            players += roster_players(fetch_json(ESPN_ROSTER_URL.format(team_id=tid), timeout=60))
            ok += 1
        except Exception as exc:
            print(f"[nfl_data] espn roster team {tid}: unavailable ({type(exc).__name__})", flush=True)
    return players, ok


def apply_updates(client: Any, updates: list[dict[str, Any]]) -> int:
    for u in updates:
        body = {k: v for k, v in u.items() if k != "sleeper_id"}
        client.table("nfl_players").update(body).eq("sleeper_id", u["sleeper_id"]).execute()
    return len(updates)


def _missing_table(exc: Exception) -> bool:
    s = str(exc)
    return MOVES_TABLE in s and ("PGRST205" in s or "does not exist" in s or "schema cache" in s)
