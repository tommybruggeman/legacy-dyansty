"""Playoff weeks on the Matchups page (app and website): the winners bracket, the loser bracket and this week's games.

Sleeper keeps handing out regular-season pairings for playoff weeks, so Matchups never reads those. Instead:
- Seeds are our standings (2 per win, 1 per top-5 week, points-for tiebreaker), the same order the Standings page shows.
- The format is ours, from Settings (league_ai_settings: playoff teams, first playoff week, weeks per round, loser
  bracket), never Sleeper's league settings or brackets, so the app stays independent of Sleeper.
- Brackets are built here from our seeds and scores (the same build api.own_league uses when the app runs the
  playoffs). Before the regular season ends they are projected from today's standings.
- A round can last more than one week; its score is the sum of its weeks.

Kept free of FastAPI and Streamlit so both the app server and the website use the same answer.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from api import own_league
from services.matchups_view import _team_points


def rules_of(settings: Any) -> dict[str, Any]:
    """Our playoff format from league_ai_settings (a LeagueAISettings or a plain dict)."""
    get = (lambda k, d: settings.get(k, d)) if isinstance(settings, Mapping) else (lambda k, d: getattr(settings, k, d))
    teams = int(get("playoff_teams", 4) or 4)
    return {"playoff_teams": teams if teams in own_league.LAYOUTS else 4,
            "playoff_week_start": int(get("playoff_week_start", 15) or 15),
            "playoff_round_weeks": max(1, min(2, int(get("playoff_round_weeks", 2) or 2))),
            "loser_bracket": bool(get("loser_bracket", True))}


def settings_of(rules: Mapping[str, Any]) -> dict[str, Any]:
    start, teams = int(rules["playoff_week_start"]), int(rules["playoff_teams"])
    rounds = max(r for r, *_ in own_league.LAYOUTS[teams])
    weeks = own_league.round_weeks(start, rounds, 2 if int(rules["playoff_round_weeks"]) == 2 else 0)
    return {"start": start, "teams": teams, "rounds": rounds, "weeks": weeks, "loser_bracket": bool(rules["loser_bracket"]),
            "last_week": max(w for ws in weeks.values() for w in ws)}


def last_week(rules: Mapping[str, Any]) -> int:
    """The last week of the playoffs (the Matchups week picker stops here)."""
    return settings_of(rules)["last_week"]


def round_name(r: int, rounds: int, place: Any) -> str:
    if place == 1 or (r == rounds and place is None):
        return "Championship"
    if place == 3:
        return "3rd place"
    if place:
        return f"{place}th place"
    left = rounds - r
    return "Semifinal" if left == 1 else "Quarterfinal" if left == 2 else f"Round {r}"


def build(
    *, week: int, rules: Mapping[str, Any], standings: list[Mapping[str, Any]], names: Mapping[int, str],
    rows_for: Callable[[int], list[Mapping[str, Any]]], last_scored: int, current_week: int, my_roster_id: int | None,
    avatars: Mapping[int, Any] | None = None,
) -> dict[str, Any] | None:
    """Everything the playoff view needs for one week, or None for a regular-season week.

    rules: rules_of(league_ai_settings). standings: our standings rows (rank order, regular season only).
    rows_for(week): that week's matchup rows, used for points only (asked only for weeks that have started).
    """
    cfg = settings_of(rules)
    if week < cfg["start"]:
        return None
    week = min(week, cfg["last_week"])
    weeks = cfg["weeks"]
    rnd = next(r for r, ws in weeks.items() if week in ws)
    order = [int(r["roster_id"]) for r in standings]
    seed = {rid: i + 1 for i, rid in enumerate(order)}
    record = {int(r["roster_id"]): f"{r.get('wins', 0)}-{r.get('losses', 0)}" + (f"-{r['ties']}" if r.get("ties") else "")
              for r in standings}
    projected = last_scored < cfg["start"] - 1

    # Points for every playoff week that has started: (week, roster) -> score.
    points: dict[tuple[int, int], float] = {}
    row_mid: dict[tuple[int, int], Any] = {}
    for w in sorted({w for ws in weeks.values() for w in ws}):
        if w > current_week:
            continue
        for row in rows_for(w) or []:
            if row.get("roster_id") is None:
                continue
            points[(w, int(row["roster_id"]))] = _team_points(dict(row))
            row_mid[(w, int(row["roster_id"]))] = row.get("matchup_id")

    def done(r: int) -> bool:
        return max(weeks[r]) <= last_scored

    playoff_seeds = order[:cfg["teams"]]
    loser_seeds = order[cfg["teams"]:]
    win_bracket = own_league.build_bracket(playoff_seeds, points, done, weeks) if len(playoff_seeds) == cfg["teams"] else []
    loser_bracket = own_league.build_bracket(
        loser_seeds, points, done, weeks, own_league.loser_layout(len(loser_seeds), cfg["rounds"])) if cfg["loser_bracket"] else []

    def team(rid: int | None, r: int) -> dict[str, Any] | None:
        if not rid:
            return None
        legs = [points.get((w, rid)) if w <= current_week else None for w in weeks[r]]
        return {
            "roster_id": rid, "name": str(names.get(rid) or f"Roster {rid}"), "seed": seed.get(rid),
            "record": record.get(rid), "avatar": (avatars or {}).get(rid), "is_me": rid == my_roster_id,
            "score": round(sum(p for p in legs if p is not None), 2), "legs": legs,
        }

    def placeholder(game: Mapping[str, Any], side: str, bracket: list[Mapping[str, Any]]) -> str | None:
        src = game.get(f"{side}_from") or {}
        for kind, m in src.items():
            prev = next((g for g in bracket if int(g.get("m") or 0) == int(m)), None)
            pair = ""
            if prev:
                s1, s2 = seed.get(prev.get("t1")), seed.get(prev.get("t2"))
                pair = f"{s1} v {s2}" if s1 and s2 else ""
            return f"{'Winner' if kind == 'w' else 'Loser'} of {pair or 'game ' + str(m)}"
        return None

    def shape(game: Mapping[str, Any], kind: str, bracket: list[Mapping[str, Any]]) -> dict[str, Any]:
        r = int(game.get("r") or 1)
        t1, t2 = team(game.get("t1"), r), team(game.get("t2"), r)
        if r not in weeks:
            r = max(weeks)
        final = bool(done(r) and t1 and t2)
        started = min(weeks[r]) <= current_week and any(
            leg for t in (t1, t2) if t for leg in t["legs"] if leg)
        winner = game.get("w") if final else None
        if final and not winner and t1 and t2:
            winner = t1["roster_id"] if t1["score"] >= t2["score"] else t2["roster_id"]
        mid = None
        if t1 and t2 and week in weeks[r]:
            a, b = row_mid.get((week, t1["roster_id"])), row_mid.get((week, t2["roster_id"]))
            mid = a if a is not None and a == b else None
        return {
            "bracket": kind, "round": r, "m": int(game.get("m") or 0),
            "label": round_name(r, cfg["rounds"], game.get("p")) if kind == "winners" else f"Loser bracket · round {r}",
            "place": game.get("p"), "weeks": weeks[r], "state": "final" if final else "live" if started else "pre",
            "teams": [t1 or {"placeholder": placeholder(game, "t1", bracket)}, t2 or {"placeholder": placeholder(game, "t2", bracket)}],
            "winner": winner, "matchup_id": mid,
            "mine": bool(my_roster_id and my_roster_id in (game.get("t1"), game.get("t2"))),
        }

    tree = [shape(g, "winners", win_bracket) for g in sorted(win_bracket, key=lambda g: (int(g.get("r") or 0), int(g.get("m") or 0)))]
    loser_games = [shape(g, "losers", loser_bracket) for g in sorted(loser_bracket, key=lambda g: (int(g.get("r") or 0), int(g.get("m") or 0)))]
    this_round = [g for g in tree if g["round"] == rnd]
    # The title game first, then your game, then the rest in bracket order.
    this_round.sort(key=lambda g: (g["place"] != 1, not g["mine"], g["m"]))
    losers_now = [g for g in loser_games if g["round"] == rnd]
    playing = {t.get("roster_id") for g in losers_now for t in g["teams"]}
    byes = [team(rid, rnd) for rid in loser_seeds if rid not in playing and losers_now]
    champion = next((g["winner"] for g in tree if g["place"] == 1 and g["state"] == "final"), None)
    return {
        "week": week, "round": rnd, "rounds": cfg["rounds"], "leg": weeks[rnd].index(week) + 1, "legs": len(weeks[rnd]),
        "round_name": round_name(rnd, cfg["rounds"], None if rnd < cfg["rounds"] else 1),
        "projected": projected, "start": cfg["start"], "last_week": cfg["last_week"],
        "tree": tree, "games": this_round, "losers": losers_now, "byes": [b for b in byes if b],
        "champion": team(champion, rnd) if champion else None,
    }


def legacy_games(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """This week's playoff games as plain Matchups cards, for app builds that predate the playoff view."""
    out = []
    for g in [*view["games"], *view["losers"]]:
        if any("placeholder" in t for t in g["teams"]):
            continue
        teams = []
        for t in g["teams"]:
            leg = t["legs"][view["leg"] - 1] if view["leg"] - 1 < len(t["legs"]) else None
            teams.append({"roster_id": t["roster_id"], "name": t["name"], "score": round(t["score"], 2),
                          "rank": t["seed"] or 0, "top5": False, "avatar": t.get("avatar"), "record": t.get("record"),
                          "week_score": leg})
        out.append({"matchup_id": g["matchup_id"] if g["matchup_id"] is not None else 900 + g["m"], "teams": teams})
    return out
