"""Home tab: one page that changes with the calendar.

In season it leads with your game (game day, Thu-Mon) or last week's recap
(Tue-Wed); in the fantasy playoffs it shows the bracket. In the offseason
it is your rookie-deal decisions until the rollover (June 30), then draft
prep. Everything here comes from data the app already loads, so Home never
calls the AI; its Front Office card shows the week's saved brief line.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

ROLLOVER_MONTH, ROLLOVER_DAY = 6, 30
ROSTER_MAX = 22
GAME_DAYS = {3, 4, 5, 6, 0}  # Thu-Mon (Monday=0)
ROUND_NAMES = {1: "Semifinal", 2: "Final"}


def _f(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def phase(today: date, league_status: str | None, week: int, playoff_start: int | None) -> str:
    """game_day, recap, playoffs, decisions (offseason until the rollover) or draft_prep."""
    if (league_status or "") == "in_season":
        if playoff_start and week >= playoff_start:
            return "playoffs"
        return "game_day" if today.weekday() in GAME_DAYS else "recap"
    if (today.month, today.day) <= (ROLLOVER_MONTH, ROLLOVER_DAY):
        return "decisions"
    return "draft_prep"


def rollover_deadline(today: date) -> date:
    return date(today.year, ROLLOVER_MONTH, ROLLOVER_DAY)


def my_game(games: list[dict[str, Any]], my_roster_id: int | None) -> dict[str, Any] | None:
    """My game as me vs them, or None on a bye."""
    for game in games:
        sides = game.get("teams") or []
        mine = [t for t in sides if t.get("roster_id") == my_roster_id]
        if mine:
            them = next((t for t in sides if t is not mine[0]), None)
            return {"matchup_id": game.get("matchup_id"), "me": mine[0], "them": them}
    return None


def playoff_picture(rows: list[dict[str, Any]], playoff_teams: int, show: int = 6) -> list[dict[str, Any]]:
    """The top of the standings with a line after the last playoff spot; I'm always included."""
    picked = rows[:show]
    me = next((r for r in rows if r.get("is_me")), None)
    if me and me not in picked:
        picked = picked[: show - 1] + [me]
    return [{"rank": r["rank"], "name": r["name"], "standing_points": r.get("standing_points"), "is_me": bool(r.get("is_me")),
             "in": r["rank"] <= playoff_teams, "team_id": r.get("team_id")} for r in picked]


def bracket(matches: list[dict[str, Any]], names: dict[int, str], points: dict[tuple[int, int], float], playoff_start: int) -> list[dict[str, Any]]:
    """Sleeper's winners bracket as rounds of games with names and scores.

    `points` maps (round, roster_id) to that roster's score in the round's week.
    """
    rounds: dict[int, list[dict[str, Any]]] = {}
    for m in matches:
        rnd = int(m.get("r") or 0)
        if not rnd or m.get("p") not in (None, 1):  # keep the title path, not placement games
            continue

        def side(roster_id: Any) -> dict[str, Any] | None:
            if not isinstance(roster_id, int):
                return None
            return {"roster_id": roster_id, "name": names.get(roster_id) or f"Roster {roster_id}", "score": points.get((rnd, roster_id))}

        rounds.setdefault(rnd, []).append({"a": side(m.get("t1")), "b": side(m.get("t2")), "winner": m.get("w")})
    last = max(rounds) if rounds else 0
    return [{"round": r, "week": playoff_start + r - 1, "label": "Final" if r == last else ROUND_NAMES.get(r, f"Round {r}"), "games": games}
            for r, games in sorted(rounds.items())]


def rookie_decisions(decisions: list[dict[str, Any]], roster: Iterable[dict[str, Any]], ppg: dict[str, float]) -> list[dict[str, Any]]:
    """My rollover decisions with the player's name, position, salary and PPG."""
    by_id = {_text(r.get("sleeper_player_id")): r for r in roster}
    out = []
    for d in decisions:
        pid = _text(d.get("player_id"))
        row = by_id.get(pid, {})
        status = _text(d.get("decision_status"))
        choice = {"recontract": "keep", "decline": "walk", "commissioner_review": "review"}.get(_text(d.get("owner_choice")))
        out.append({
            "decision_id": _text(d.get("id")),
            "player_id": pid,
            "name": _text(row.get("player_name")) or pid,
            "pos": _text(row.get("pos")),
            "salary": _f(row.get("contract_cap_hit") or row.get("cap_hit")),
            "ppg": ppg.get(pid),
            "choice": choice,
            "open": status in {"waiting_for_owner", "recontract_invalid"} and not d.get("locked_at"),
            "status": status,
        })
    return sorted(out, key=lambda r: (-r["salary"], r["name"]))


def rookie_deals_ending(roster: Iterable[dict[str, Any]], team_id: str, ppg: dict[str, float]) -> list[dict[str, Any]]:
    """Before the commissioner opens decisions: my rookie deals in their final year."""
    out = []
    for r in roster:
        if _text(r.get("league_team_id")) != team_id or not r.get("is_rookie") or int(r.get("contract_years_left") or 0) > 1:
            continue
        pid = _text(r.get("sleeper_player_id"))
        out.append({"decision_id": None, "player_id": pid, "name": _text(r.get("player_name")) or pid, "pos": _text(r.get("pos")),
                    "salary": _f(r.get("contract_cap_hit") or r.get("cap_hit")), "ppg": ppg.get(pid), "choice": None, "open": False, "status": "not_open"})
    return sorted(out, key=lambda r: (-r["salary"], r["name"]))


def coming_off_books(roster: Iterable[dict[str, Any]], team_id: str) -> dict[str, Any]:
    rows = sorted(
        ({"name": _text(r.get("player_name")), "salary": _f(r.get("contract_cap_hit") or r.get("cap_hit"))}
         for r in roster if _text(r.get("league_team_id")) == team_id and int(r.get("contract_years_left") or 0) <= 1),
        key=lambda r: -r["salary"],
    )
    return {"total": round(sum(r["salary"] for r in rows), 2), "players": rows}


def who_has_money(summaries: list[dict[str, Any]], season_index: int = 1, limit: int = 3) -> list[dict[str, Any]]:
    """Owners with the most cap space in a season (next season by default)."""
    def space(s: dict[str, Any]) -> float:
        years = s.get("cap_by_year") or []
        return years[season_index]["space"] if len(years) > season_index else s.get("cap_space", 0)

    ranked = sorted(summaries, key=lambda s: -space(s))
    return [{"name": s["owner_name"], "space": space(s), "is_me": bool(s.get("is_me")), "team_id": s.get("team_id")} for s in ranked[:limit]]


def upcoming_picks(picks: list[dict[str, Any]]) -> dict[str, Any]:
    if not picks:
        return {"year": None, "picks": []}
    year = min(p["year"] for p in picks)
    return {"year": year, "picks": [p for p in picks if p["year"] == year]}
