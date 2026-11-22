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

# Settings > "Weeks per playoff round": 1 = one week each, 2 = two weeks each, 3 = one-week rounds and a two-week
# final (Legacy, Tommy 2026-11-18: semis week 15, title game weeks 16-17, the season ends week 17).
ROUND_FORMATS = {1: "One week each", 2: "Two weeks each", 3: "Two-week final only"}


def round_type(rules: Mapping[str, Any]) -> int:
    """own_league.round_weeks' round_type for our setting: 0 one week each, 1 a two-week final, 2 two weeks each."""
    return {1: 0, 2: 2, 3: 1}.get(int(rules.get("playoff_round_weeks") or 2), 2)


def rules_of(settings: Any) -> dict[str, Any]:
    """Our playoff format from league_ai_settings (a LeagueAISettings or a plain dict)."""
    get = (lambda k, d: settings.get(k, d)) if isinstance(settings, Mapping) else (lambda k, d: getattr(settings, k, d))
    teams = int(get("playoff_teams", 4) or 4)
    return {"playoff_teams": teams if teams in own_league.LAYOUTS else 4,
            "playoff_week_start": int(get("playoff_week_start", 15) or 15),
            "playoff_round_weeks": max(1, min(3, int(get("playoff_round_weeks", 2) or 2))),
            "loser_bracket": bool(get("loser_bracket", True))}


def settings_of(rules: Mapping[str, Any], league_size: int | None = None) -> dict[str, Any]:
    """league_size (teams in the league) sizes the loser bracket; without it the loser bracket is assumed to run its
    longest (three one-week rounds) so the last week is never cut short."""
    start, teams = int(rules["playoff_week_start"]), int(rules["playoff_teams"])
    rounds = max(r for r, *_ in own_league.LAYOUTS[teams])
    weeks = own_league.round_weeks(start, rounds, round_type(rules))
    losers = bool(rules["loser_bracket"])
    lweeks = own_league.loser_weeks(start, league_size - teams) if losers and league_size else {}
    last = max(w for ws in weeks.values() for w in ws)
    if losers:
        last = max([last, *(w for ws in lweeks.values() for w in ws)] if league_size else [last, start + 2])
    return {"start": start, "teams": teams, "rounds": rounds, "weeks": weeks, "loser_bracket": losers,
            "loser_weeks": lweeks, "last_week": last}


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


def loser_name(r: int, place: Any, playoff_teams: int) -> str:
    """'5th place' etc. for the loser bracket's placement games (place counts inside it), else its round."""
    if place:
        n = playoff_teams + int(place)
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')} place"
    return f"Consolation · round {r}"


def build(
    *, week: int, rules: Mapping[str, Any], standings: list[Mapping[str, Any]], names: Mapping[int, str],
    rows_for: Callable[[int], list[Mapping[str, Any]]], last_scored: int, current_week: int, my_roster_id: int | None,
    avatars: Mapping[int, Any] | None = None,
) -> dict[str, Any] | None:
    """Everything the playoff view needs for one week, or None for a regular-season week.

    rules: rules_of(league_ai_settings). standings: our standings rows (rank order, regular season only).
    rows_for(week): that week's matchup rows, used for points only (asked only for weeks that have started).
    """
    cfg = settings_of(rules, len(standings) or None)
    if week < cfg["start"]:
        return None
    week = min(week, cfg["last_week"])
    weeks = cfg["weeks"]
    lweeks = cfg["loser_weeks"]
    rnd = next((r for r, ws in weeks.items() if week in ws), max(weeks))  # past the title game only the loser bracket plays
    order = [int(r["roster_id"]) for r in standings]
    seed = {rid: i + 1 for i, rid in enumerate(order)}
    record = {int(r["roster_id"]): f"{r.get('wins', 0)}-{r.get('losses', 0)}" + (f"-{r['ties']}" if r.get("ties") else "")
              for r in standings}
    projected = last_scored < cfg["start"] - 1

    # Points for every playoff week that has started: (week, roster) -> score.
    points: dict[tuple[int, int], float] = {}
    forfeits: set[tuple[int, int]] = set()  # (week, roster) that started that Sunday over the cap
    row_mid: dict[tuple[int, int], Any] = {}
    for w in sorted({w for ws in [*weeks.values(), *lweeks.values()] for w in ws}):
        if w > current_week:
            continue
        for row in rows_for(w) or []:
            if row.get("roster_id") is None:
                continue
            points[(w, int(row["roster_id"]))] = _team_points(dict(row))
            if row.get("auto_loss"):
                forfeits.add((w, int(row["roster_id"])))
            row_mid[(w, int(row["roster_id"]))] = row.get("matchup_id")

    def done(r: int) -> bool:
        return max(weeks[r]) <= last_scored

    def loser_done(r: int) -> bool:
        return max(lweeks[r]) <= last_scored

    playoff_seeds = order[:cfg["teams"]]
    loser_seeds = order[cfg["teams"]:]
    win_bracket = own_league.build_bracket(playoff_seeds, points, done, weeks, auto_loss=forfeits) if len(playoff_seeds) == cfg["teams"] else []
    loser_bracket = own_league.build_bracket(
        loser_seeds, points, loser_done, lweeks, own_league.loser_layout(len(loser_seeds)), auto_loss=forfeits,
        spans=own_league.loser_spans(len(loser_seeds))) if cfg["loser_bracket"] else []

    def team(rid: int | None, r: int, when: Mapping[int, list[int]] = weeks, game_weeks: list[int] | None = None) -> dict[str, Any] | None:
        if not rid:
            return None
        legs = [points.get((w, rid)) if w <= current_week else None for w in (game_weeks or when[r])]
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
        when, is_done = (weeks, done) if kind == "winners" else (lweeks, loser_done)
        if r not in when:
            r = max(when)
        gw = list(game.get("weeks") or when[r])  # a game can span rounds (the two-week 9th place game)
        t1, t2 = team(game.get("t1"), r, when, gw), team(game.get("t2"), r, when, gw)
        final = bool(is_done(int(game.get("last_r") or r)) and t1 and t2)
        started = min(gw) <= current_week and any(
            leg for t in (t1, t2) if t for leg in t["legs"] if leg)
        winner = game.get("w") if final else None
        if final and not winner and t1 and t2:
            winner = t1["roster_id"] if t1["score"] >= t2["score"] else t2["roster_id"]
        mid = None
        if t1 and t2 and week in gw:
            a, b = row_mid.get((week, t1["roster_id"])), row_mid.get((week, t2["roster_id"]))
            mid = a if a is not None and a == b else None
        return {
            "bracket": kind, "round": r, "m": int(game.get("m") or 0),
            "label": round_name(r, cfg["rounds"], game.get("p")) if kind == "winners" else loser_name(r, game.get("p"), cfg["teams"]),
            "place": game.get("p"), "weeks": gw, "state": "final" if final else "live" if started else "pre",
            "teams": [t1 or {"placeholder": placeholder(game, "t1", bracket)}, t2 or {"placeholder": placeholder(game, "t2", bracket)}],
            "winner": winner, "matchup_id": mid,
            "mine": bool(my_roster_id and my_roster_id in (game.get("t1"), game.get("t2"))),
        }

    def places(m: int, seen: frozenset = frozenset()) -> list[int]:
        """Every final place (counted league-wide) a team in loser game m can still reach."""
        game = next((g for g in loser_bracket if int(g.get("m") or 0) == m), None)
        if not game or m in seen:
            return []
        if game.get("p"):
            return [cfg["teams"] + int(game["p"]), cfg["teams"] + int(game["p"]) + 1]
        out: list[int] = []
        for g in loser_bracket:
            if m in {*(g.get("t1_from") or {}).values(), *(g.get("t2_from") or {}).values()}:
                out += places(int(g.get("m") or 0), seen | {m})
        return out

    def stakes(game: Mapping[str, Any]) -> dict[str, str] | None:
        """What a consolation game decides: where its winner and its loser go next (Tommy, 2026-11-22)."""
        if game.get("p"):
            return None
        m = int(game.get("m") or 0)
        nxt: dict[str, Any] = {}
        for g in loser_bracket:
            for src in (g.get("t1_from") or {}, g.get("t2_from") or {}):
                for kind, mm in src.items():
                    if int(mm) == m:
                        nxt[kind] = g
        if "w" not in nxt or "l" not in nxt:
            return None

        def say(g: Mapping[str, Any]) -> str:
            if g.get("p"):
                return f"{loser_name(0, g['p'], cfg['teams'])} game"
            reach = places(int(g.get("m") or 0))
            return f"play for {_ord(min(reach))}–{_ord(max(reach))}" if reach else "next round"

        return {"win": f"Win: {say(nxt['w'])}", "lose": f"Lose: {say(nxt['l'])}"}

    tree = [shape(g, "winners", win_bracket) for g in sorted(win_bracket, key=lambda g: (int(g.get("r") or 0), int(g.get("m") or 0)))]
    loser_games = [{**shape(g, "losers", loser_bracket), "stakes": stakes(g)}
                   for g in sorted(loser_bracket, key=lambda g: (int(g.get("r") or 0), int(g.get("m") or 0)))]
    this_round = [g for g in tree if g["round"] == rnd]
    # The title game first, then your game, then the rest in bracket order.
    this_round.sort(key=lambda g: (g["place"] != 1, not g["mine"], g["m"]))
    # Consolation games before placement games, then 5th, 7th, 9th place (a game spanning two rounds sorts by place).
    losers_now = sorted((g for g in loser_games if week in g["weeks"]), key=lambda g: (g["place"] is not None, g["place"] or 0, g["m"]))
    playing = {t.get("roster_id") for g in losers_now for t in g["teams"]}
    lrnd = next((r for r, ws in lweeks.items() if week in ws), None)
    byes = [team(rid, lrnd, lweeks) for rid in loser_seeds if rid not in playing and losers_now and lrnd]
    champion = next((g["winner"] for g in tree if g["place"] == 1 and g["state"] == "final"), None)
    return {
        "week": week, "round": rnd, "rounds": cfg["rounds"], "leg": weeks[rnd].index(week) + 1 if week in weeks[rnd] else len(weeks[rnd]),
        "legs": len(weeks[rnd]),
        "round_name": round_name(rnd, cfg["rounds"], None if rnd < cfg["rounds"] else 1),
        "projected": projected, "start": cfg["start"], "last_week": cfg["last_week"],
        "tree": tree, "games": this_round, "losers": losers_now, "byes": [b for b in byes if b],
        "champion": team(champion, rnd) if champion else None,
    }


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def add_projections(view: dict[str, Any], week_proj: Mapping[int, Mapping[str, float]], week_final: bool = False) -> None:
    """Each team's projected total for every game played this week (Tommy, 2026-11-22: every matchup shows
    projections). week_proj: roster_id -> {"projected": live projected week total, "kickoff": the projection frozen
    at kickoff}. A game's projection is its earlier weeks' points plus this week's projection; once the game is
    final it's the kickoff projection instead, so final rows still show what was expected."""
    week = view["week"]
    for g in [*view.get("tree", []), *view.get("games", []), *view.get("losers", [])]:
        if week not in g["weeks"]:
            continue
        i = g["weeks"].index(week)
        for t in g["teams"]:
            p = week_proj.get(t.get("roster_id") or -1)
            if not p:
                continue
            banked = sum(x for x in t["legs"][:i] if x is not None)
            this = p.get("kickoff") if g["state"] == "final" else p.get("projected")
            if this is not None:
                t["proj"] = round(banked + float(this), 1)
    for t in view.get("byes", []):
        p = week_proj.get(t.get("roster_id") or -1) or {}
        this = p.get("kickoff") if week_final else p.get("projected")
        if this is not None:
            t["proj"] = round(float(this), 1)


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
