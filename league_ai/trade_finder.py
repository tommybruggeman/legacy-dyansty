"""Trade finder: shortlist trades the other owner has a reason to take, and price them like an agent. No model call.

Every team is valued its own way (see trade_value): a contender counts this season's production most, a
rebuilder counts the dynasty market most, and contracts count in a cap league. The finder looks for deals
where both sides come out ahead by their own goals, which is where real trades happen, rather than deals
that are merely even on the market. Each idea comes as a ladder: the opening offer (cheapest they might
take), where it should close, and the most the owner should pay. Market fair value is shown as context.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from statistics import median
from typing import Any, Callable, Iterable, Mapping

from league_ai.power import PlayerStrength, compute_power, fill_lineup
from league_ai.playoff_odds import pickup_odds
from league_ai.tools import ToolSpec
from league_ai.trade_value import WINDOWS, Valuer, market_value, nearest_pick, team_windows

POSITIONS = ("QB", "RB", "WR", "TE")
DEFAULT_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]


def _slots(roster_positions: Iterable[str] | None) -> list[str]:
    return [s for s in (roster_positions or []) if s not in {"BN", "IR", "TAXI"}] or list(DEFAULT_SLOTS)


class League:
    """Each team's lineup strength by position, window, and what each player is worth to each team."""

    def __init__(self, players: list[PlayerStrength], teams: list[Mapping[str, Any]], roster_positions: list[str] | None,
                 windows: Mapping[str, str] | None = None, records: Mapping[str, tuple[int, int]] | None = None,
                 pick_values: list[tuple[str, float]] | None = None, leanings: Mapping[str, str] | None = None):
        self.slots = _slots(roster_positions)
        self.teams = {str(t["league_team_id"]): t for t in teams}
        self.players = players
        self.by_team: dict[str, list[PlayerStrength]] = defaultdict(list)
        for p in players:
            self.by_team[p.team_id].append(p)
        power = {tp.team_id: tp for tp in compute_power(players, teams, self.slots)}
        self.now_rank = {tid: tp.now_rank for tid, tp in power.items()}
        self.dynasty_rank = {tid: tp.dynasty_rank for tid, tp in power.items()}
        self.records = dict(records or {})
        self.pick_values = list(pick_values or [])
        self.leanings = dict(leanings or {})
        self.playoff_odds: dict[str, float] = {}
        self.set_windows(windows)

    def set_windows(self, overrides: Mapping[str, str] | None) -> None:
        self._overrides = dict(overrides or {})
        self.windows = team_windows(self.players, self.teams.values(), self.now_rank, self.dynasty_rank, overrides, self.records, self.leanings,
                                    playoff_odds=self.playoff_odds)
        self.valuer = Valuer(self.players, self.windows)

    def set_playoff_odds(self, odds: Mapping[str, float]) -> None:
        """Owner name -> playoff chance (0-1); windows are worked out again from them."""
        self.playoff_odds = dict(odds)
        self.set_windows(self._overrides)

    def window(self, team_id: str) -> str:
        return self.windows.get(str(team_id), "middle")

    def mode(self, team_id: str) -> str:
        """'now' for teams playing for this year, 'dynasty' for rebuilders, for lineup-strength comparisons."""
        return "dynasty" if self.window(team_id) == "rebuilding" else "now"

    def team_id(self, name: str | None) -> str | None:
        key = str(name or "").casefold()
        if not key:
            return None
        for tid, t in self.teams.items():
            if key in {tid.casefold(), str(t.get("owner_name") or "").casefold(), str(t.get("team_name") or "").casefold()}:
                return tid
        return next((tid for tid, t in self.teams.items() if key in str(t.get("owner_name") or "").casefold() or key in str(t.get("team_name") or "").casefold()), None)

    def find_player(self, name: str, team_id: str | None = None) -> PlayerStrength | None:
        key = _norm(name)
        pool = self.by_team.get(str(team_id), []) if team_id else self.players
        return next((p for p in pool if _norm(p.name) == key), None) or next((p for p in pool if key and key in _norm(p.name)), None)

    def strength(self, roster: list[PlayerStrength], key: str) -> dict[str, float]:
        lineup, _ = fill_lineup(roster, self.slots, key)
        out: dict[str, float] = defaultdict(float)
        for _, p in lineup:
            if p.position in POSITIONS:
                out[p.position] += getattr(p, key)
        return out

    def starters(self, team_id: str) -> set[str]:
        lineup, _ = fill_lineup(self.by_team.get(team_id, []), self.slots, "now")
        return {p.sleeper_id for _, p in lineup}

    def position_ranks(self, key: str = "now") -> dict[str, dict[str, int]]:
        """team -> position -> rank of that position group in the league (1 = best)."""
        scores = {tid: self.strength(self.by_team.get(tid, []), key) for tid in self.teams}
        out: dict[str, dict[str, int]] = defaultdict(dict)
        for pos in POSITIONS:
            ordered = sorted(self.teams, key=lambda t: -scores[t].get(pos, 0.0))
            for i, tid in enumerate(ordered, start=1):
                out[tid][pos] = i
        return out

    def needs(self, team_id: str, key: str) -> list[str]:
        """Positions where this team's starters trail the league median, weakest first."""
        scores = {tid: self.strength(self.by_team.get(tid, []), key) for tid in self.teams}
        gaps = {}
        for pos in POSITIONS:
            med = median([scores[t].get(pos, 0.0) for t in self.teams]) if self.teams else 0.0
            gap = scores[team_id].get(pos, 0.0) - med
            if med > 0 and gap < 0:
                gaps[pos] = gap / med
        return sorted(gaps, key=gaps.get)

    def gain(self, team_id: str, sends: list[PlayerStrength], gets: list[PlayerStrength], key: str) -> float:
        """Change in starting-lineup strength for team_id after the swap."""
        roster = self.by_team.get(team_id, [])
        out_ids = {p.sleeper_id for p in sends}
        after = [p for p in roster if p.sleeper_id not in out_ids] + list(gets)
        return round(sum(self.strength(after, key).values()) - sum(self.strength(roster, key).values()), 2)

    def motivation(self, team_id: str, p: PlayerStrength) -> str | None:
        """Why an owner might be glad to move a player: he isn't starting for them, or produces well below his price."""
        v = self.valuer
        if p.sleeper_id not in self.starters(team_id):
            return f"{p.name} isn't in their best lineup"
        if v.now_rank.get(p.sleeper_id, 0) - v.market_rank.get(p.sleeper_id, 0) >= 8 and self.window(team_id) != "rebuilding":
            return f"{p.name} is producing well below his price ({p.position}{v.now_rank[p.sleeper_id]} this season), which hurts a team playing for now"
        return None


def find_trades(*, league: League, asker_team_id: str, cap_space: Mapping[str, float], want_position: str | None = None,
                goal: str | None = None, partner: str | None = None, limit: int = 4) -> dict[str, Any]:
    asker = str(asker_team_id)
    v = league.valuer
    rebuilding = goal in ("future", "dynasty", "rebuild") or (not goal and league.window(asker) == "rebuilding")
    key = "dynasty" if rebuilding else "now"
    want = (want_position or "").upper() or next(iter(league.needs(asker, key)), None)
    if want not in POSITIONS:
        return {"error": "say which position to upgrade (QB, RB, WR or TE); your lineup is at or above the league median everywhere"}
    ranks = league.position_ranks(key)
    my_starters = league.starters(asker)
    my_roster = league.by_team.get(asker, [])
    ideas: list[dict[str, Any]] = []
    for tid, team in league.teams.items():
        if tid == asker or (partner and league.team_id(partner) != tid):
            continue
        their_window = league.window(tid)
        their_needs = league.needs(tid, league.mode(tid))
        if not their_needs and their_window != "rebuilding":
            continue
        # what the asker can offer: players at the partner's weak spots (rebuilders take value anywhere), never the asker's own need position
        pool = [p for p in my_roster if p.position != want and (p.position in their_needs or their_window == "rebuilding")]
        for target in (p for p in league.by_team.get(tid, []) if p.position == want and p.available):
            their_out, my_in = v.value_to(tid, target), v.value_to(asker, target)
            if their_out <= 0:
                continue
            options = []
            for size in (1, 2):
                for package in combinations(pool, size):
                    sends = list(package)
                    their_in, my_out = v.package(tid, sends), v.package(asker, sends)
                    if their_in < OPEN_SHARE * their_out or my_out > WALK_AWAY_SHARE * my_in:
                        continue  # they'd laugh it off, or it costs us more than he's worth to us
                    my_gain = league.gain(asker, sends, [target], key)
                    if (my_gain <= 0 and not rebuilding) or (rebuilding and my_out >= my_in):
                        continue  # a contender needs a better lineup; a rebuilder needs more value back
                    cap_me = cap_space.get(asker, 0.0) - _cap(target) + sum(_cap(p) for p in sends)
                    cap_them = cap_space.get(tid, 0.0) - sum(_cap(p) for p in sends) + _cap(target)
                    if cap_me < 0 or cap_them < 0:
                        continue
                    options.append({"sends": sends, "their_share": their_in / their_out, "my_out": my_out, "my_gain": my_gain,
                                    "their_gain": league.gain(tid, [target], sends, league.mode(tid)), "cap": (cap_me, cap_them)})
            if not options:
                continue
            options.sort(key=lambda o: o["my_out"])
            opening = options[0]
            closing = next((o for o in options if o["their_share"] >= CLOSE_SHARE), None)
            if closing is None:
                continue  # nothing we'd pay gets to what they should take
            walk = max((o for o in options if o["their_share"] >= CLOSE_SHARE), key=lambda o: o["my_out"])
            surplus = (my_in - closing["my_out"]) / my_in if my_in else 0.0
            ideas.append({
                "score": round(closing["my_gain"] + 10 * surplus + 3 * min(closing["their_share"] - 1, 0.5), 2),
                "partner": team.get("owner_name"),
                "partner_window": their_window,
                "you_get": [_label(target)],
                **_ladder(opening, closing, walk),
                "by_their_goals": f"they get back {round(100 * closing['their_share'])}% of what they give up, valued their way ({_WINDOW_NOTE[their_window]})",
                "by_your_goals": f"you pay {round(100 * closing['my_out'] / my_in)}% of what he's worth to you ({_WINDOW_NOTE[league.window(asker)]})",
                "market_view": _market_view(league, [target], closing["sends"]),
                "players": [v.explain(tid, target)] + [v.explain(tid, p) for p in closing["sends"]],
                "why_they_say_yes": _why(league, tid, their_window, closing["sends"], ranks, target),
                "schedule": {p.name: p.schedule_note for p in [target] + closing["sends"] if getattr(p, "schedule_note", None)},
                "your_lineup_gain": closing["my_gain"],
                "their_lineup_gain": closing["their_gain"],
                "cap_after": {"you": round(closing["cap"][0], 2), "them": round(closing["cap"][1], 2)},
                "you_lose_a_starter": any(p.sleeper_id in my_starters for p in closing["sends"]),
            })
    ideas.sort(key=lambda x: -x["score"])
    seen: set[tuple[str, str]] = set()
    best: list[dict[str, Any]] = []
    for idea in ideas:  # one idea per partner and target, so the list isn't five versions of one deal
        k = (str(idea["partner"]), idea["you_get"][0])
        if k in seen:
            continue
        seen.add(k)
        best.append({k2: val for k2, val in idea.items() if k2 != "score"})
        if len(best) >= limit:
            break
    return {
        "upgrading": want,
        "your_group_rank": f"{want} group ranks {ranks[asker][want]} of {len(league.teams)}",
        "your_window": league.window(asker),
        "ideas": best,
        "how_to_use": HOW_TO_PITCH,
    }


def evaluate_trade(league: League, asker_team_id: str, partner: str, you_get: list[str], you_send: list[str],
                   picks_you_get: list[str] | None = None, picks_you_send: list[str] | None = None) -> dict[str, Any]:
    """Judge a specific trade the way each owner would, and find a cheaper opening that still works for them."""
    asker = str(asker_team_id)
    tid = league.team_id(partner)
    if not tid:
        return {"error": f"no team found for '{partner}'"}
    gets, sends, missing = [], [], []
    for name in you_get or []:
        p = league.find_player(name, tid)
        (gets.append(p) if p else missing.append(f"{name} (not on {league.teams[tid].get('owner_name')}'s roster)"))
    for name in you_send or []:
        p = league.find_player(name, asker)
        (sends.append(p) if p else missing.append(f"{name} (not on your roster)"))
    if missing:
        return {"error": "couldn't find: " + ", ".join(missing)}
    pk_get, pk_send = _picks(league, picks_you_get), _picks(league, picks_you_send)
    v = league.valuer
    their_out, their_in = v.package(tid, gets, pk_get), v.package(tid, sends, pk_send)
    my_in, my_out = v.package(asker, gets, pk_get), v.package(asker, sends, pk_send)
    share = their_in / their_out if their_out else 0.0
    out: dict[str, Any] = {
        "partner": league.teams[tid].get("owner_name"),
        "partner_window": league.window(tid),
        "your_window": league.window(asker),
        "for_them": f"they get back {round(100 * share)}% of what they give up, valued their way ({_WINDOW_NOTE[league.window(tid)]}): "
                    + ("likely yes" if share >= CLOSE_SHARE else "worth a try" if share >= OPEN_SHARE else "likely no"),
        "for_you": f"you pay {round(100 * my_out / my_in) if my_in else 0}% of what you get, valued your way ({_WINDOW_NOTE[league.window(asker)]}): "
                   + ("good for you" if my_out <= my_in else "you're overpaying by your own goals"),
        "market_view": _market_view(league, gets, sends, pk_get, pk_send),
        "players": [v.explain(tid, p) for p in gets] + [v.explain(asker, p) for p in sends],
        "your_lineup_gain": league.gain(asker, sends, gets, "dynasty" if league.window(asker) == "rebuilding" else "now"),
        "their_lineup_gain": league.gain(tid, gets, sends, league.mode(tid)),
    }
    schedule = {p.name: p.schedule_note for p in gets + sends if getattr(p, "schedule_note", None)}
    if schedule:
        out["schedule"] = schedule  # already folded into this-season value; quote it when it separates close players
    motives = [m for m in (league.motivation(tid, p) for p in gets) if m]
    if motives:
        out["their_motivation"] = "; ".join(motives)
    # a cheaper opening: drop one piece we send and see if it still clears what they could take
    pieces = [(p.name, v.package(tid, [p]), v.package(asker, [p])) for p in sends] + \
             [(lbl, val * _pick_weight(league.window(tid)), val * _pick_weight(league.window(asker))) for lbl, val in _pick_list(league, picks_you_send)]
    cheaper = [(name, their_in - t) for name, t, _ in pieces if len(pieces) > 1 and their_out and (their_in - t) / their_out >= OPEN_SHARE]
    if cheaper:
        name, rest = max(cheaper, key=lambda x: next(m for n, _, m in pieces if n == x[0]))
        out["cheaper_opening"] = f"open without {name}: they'd still get back {round(100 * rest / their_out)}% by their own goals"
    out["how_to_use"] = HOW_TO_PITCH
    return out


OPEN_SHARE = 0.85   # an opening offer they might take: at least this share of what they give up, by their own goals
CLOSE_SHARE = 1.0   # a deal they should take
WALK_AWAY_SHARE = 1.1  # never pay more than this share of what the player is worth to the asker

_WINDOW_NOTE = {"contending": "contending: this season counts about 80%", "middle": "in the middle: this season and the future count about equally",
                "rebuilding": "rebuilding: the future counts about 80%"}

HOW_TO_PITCH = ("You work for this owner, and every other owner has their own AI too. Lead with the opening offer, say what it should close at, "
                "and give the walk-away as the most to pay. Mention market fair value as context, not as the ask. Say in a clause why that owner says yes "
                "in their own terms (their window, a player not producing for them). If no idea is listed, say so rather than forcing a deal.")


def _ladder(opening: Mapping[str, Any], closing: Mapping[str, Any], walk: Mapping[str, Any]) -> dict[str, Any]:
    out = {"open_with": [_label(p) for p in opening["sends"]]}
    if closing is not opening:
        out["likely_close"] = [_label(p) for p in closing["sends"]]
    if walk is not closing:
        out["walk_away_after"] = [_label(p) for p in walk["sends"]]
    return out


def _market_view(league: League, gets: list[PlayerStrength], sends: list[PlayerStrength], pk_get: float = 0.0, pk_send: float = 0.0) -> str:
    got = sum(market_value(p) for p in gets) + pk_get
    sent = sum(market_value(p) for p in sends) + pk_send
    if not got:
        return "no market values for these players"
    pct = round(100 * sent / got)
    line = f"on the dynasty market alone you'd send {pct}% of what you get"
    if pct < 90:
        pick = nearest_pick(got - sent, league.pick_values)
        line += f"; market fair would add about {pick}" if pick else "; the market would want more added"
        line += ". The other owner's AI may quote that; the case for this deal is what each team needs, not the market"
    return line


def _picks(league: League, labels: list[str] | None) -> float:
    return sum(val for _, val in _pick_list(league, labels))


def _pick_list(league: League, labels: list[str] | None) -> list[tuple[str, float]]:
    out = []
    for label in labels or []:
        words = str(label).lower().replace("first", "1st").replace("second", "2nd").replace("third", "3rd").split()
        cands = [(lbl, val) for lbl, val in league.pick_values if all(w in lbl.lower() for w in words if w.isdigit() and len(w) == 4)]
        rnd = next((w[0] for w in words if w[:1].isdigit() and len(w) <= 3), None)
        cands = [c for c in cands if not rnd or f" {rnd}" in c[0].lower() or f"{rnd}." in c[0].lower()] or cands
        if cands:
            out.append((str(label), sum(v for _, v in cands) / len(cands)))
    return out


def _pick_weight(window: str) -> float:
    return {"contending": 0.5, "middle": 0.8, "rebuilding": 1.0}.get(window, 0.8)


def _norm(s: Any) -> str:
    return "".join(ch for ch in str(s or "").casefold() if ch.isalnum())


def _cap(p: PlayerStrength) -> float:
    return float(getattr(p, "cap_hit", 0.0) or 0.0)


def _label(p: PlayerStrength) -> str:
    rank = f"{p.position}{int(round(p.pos_rank))}" if p.pos_rank else p.position
    cap = getattr(p, "cap_hit", None)
    contract = f", ${cap:g} x {p.years_left}yr" if cap is not None else ""
    ppg = f", {p.season_ppg:.1f} ppg" if p.season_ppg is not None else ""
    return f"{p.name} ({rank}{ppg}{contract})"


def _why(league: League, team_id: str, window: str, sends: list[PlayerStrength], ranks: Mapping[str, Mapping[str, int]], target: PlayerStrength | None = None) -> str:
    n = len(league.teams)
    parts = [f"their {p.position} group ranks {ranks[team_id][p.position]} of {n}" for p in sends]
    window_note = {"contending": "they're contending, so this-season points matter most to them",
                   "rebuilding": "they're rebuilding, so youth, market value and picks matter most to them",
                   "middle": "they're in the middle, so they want both points now and value later"}[window]
    motive = league.motivation(team_id, target) if target else None
    return "; ".join(dict.fromkeys(parts + ([motive] if motive else []) + [window_note]))


def trade_finder_tools(load: Callable[[], tuple[League, Mapping[str, float], str]], history: Any | None = None,
                       outcomes: Callable[..., list[Mapping[str, Any]]] | None = None, current_season: int | None = None,
                       odds: Callable[[], Any] | None = None, free_agent: Callable[[str], PlayerStrength | None] | None = None) -> list[ToolSpec]:
    """load() -> (League, cap space by team id, asker team id); called once per answer that needs it.
    history: LeagueHistory for completed trades; outcomes(partner=None) -> this owner's recorded trade outcomes.
    odds() -> a playoff_odds.OddsModel (or None), so a judged trade comes with both teams' playoff odds before and after.
    free_agent(name) -> that player valued like a rostered one (None if not found), for pickup questions."""
    cache: dict[str, Any] = {}

    def profiles() -> dict[str, dict[str, Any]]:
        if "profiles" not in cache:
            cache["profiles"] = _load_profiles(history, current_season) if history is not None else {}
        return cache["profiles"]

    def my_pitches() -> dict[str, dict[str, int]]:
        if "pitches" not in cache:
            try:
                cache["pitches"] = pitch_record(outcomes()) if outcomes else {}
            except Exception:
                cache["pitches"] = {}
        return cache["pitches"]

    def league(windows: Mapping[str, str] | None) -> League:
        if "league" not in cache:
            cache["league"], cache["cap"], cache["asker"] = load()
            cache["windows"] = {}
            model = odds() if odds is not None else None
            if model is not None and model.remaining():
                cache["league"].set_playoff_odds(model.odds())
        if windows:
            cache["windows"].update({str(k): str(v).lower() for k, v in windows.items()})
            cache["league"].set_windows(cache["windows"])
        return cache["league"]

    def run(position: str | None = None, goal: str | None = None, partner: str | None = None, limit: int = 4,
            windows: Mapping[str, str] | None = None) -> dict[str, Any]:
        lg = league(windows)
        out = find_trades(league=lg, asker_team_id=cache["asker"], cap_space=cache["cap"], want_position=position, goal=goal,
                          partner=partner, limit=max(1, min(int(limit or 4), 8)))
        if history is not None and out.get("ideas"):
            out["league_trade_habits"] = _habits(history, {i["partner"] for i in out["ideas"]}, out.get("upgrading"))
            prof, pitches = profiles(), my_pitches()
            for idea in out["ideas"]:
                p = prof.get(str(idea["partner"]))
                if p:
                    idea["partner_style"] = f"{p['trades']} trades here; {p['style']}; acquires mostly {', '.join(list(p['positions_acquired'])[:2]) or 'nothing yet'}"
                if pitches.get(str(idea["partner"])):
                    idea["your_past_pitches_to_them"] = pitches[str(idea["partner"])]
        return out

    def evaluate(partner: str, you_get: list[str] | None = None, you_send: list[str] | None = None, picks_you_get: list[str] | None = None,
                 picks_you_send: list[str] | None = None, windows: Mapping[str, str] | None = None) -> dict[str, Any]:
        lg = league(windows)
        out = evaluate_trade(lg, cache["asker"], partner, list(you_get or []), list(you_send or []), picks_you_get, picks_you_send)
        if "error" not in out and history is not None:
            p = profiles().get(str(out.get("partner")))
            if p:
                out["partner_style"] = f"{p['trades']} trades here; {p['style']}"
            if my_pitches().get(str(out.get("partner"))):
                out["your_past_pitches_to_them"] = my_pitches()[str(out["partner"])]
        if "error" not in out and odds is not None:
            out["playoff_odds"] = trade_playoff_odds(lg, cache["asker"], str(out["partner"]), you_get or [], you_send or [], odds)
        return out

    def pickup(player: str, drop: str | None = None, bid: float | None = None) -> dict[str, Any]:
        model = odds() if odds is not None else None
        fa = free_agent(player) if free_agent is not None else None
        if model is None or fa is None:
            return {"error": "couldn't value that pickup (player not found, or no playoff odds yet)"}
        lg = league(None)
        asker = cache["asker"]
        if lg.find_player(fa.name) is not None and lg.find_player(fa.name).team_id != asker:
            return {"error": f"{fa.name} is on another roster; use evaluate_trade"}
        sends = [p for p in [lg.find_player(drop, asker)] if p] if drop else []
        gain = lg.gain(asker, sends, [fa], "now")
        me = model.name(str(lg.teams[asker].get("owner_name") or ""))
        out = {"player": f"{fa.position} {fa.name}", "expected_weekly_points": fa.now, "drop": sends[0].name if sends else None,
               "playoff_odds": pickup_odds(model, me, gain)}
        change = out["playoff_odds"]["after"] - out["playoff_odds"]["before"]
        if bid:
            out["odds_per_dollar"] = round(change / float(bid), 2)
        out["how_to_use"] = ("A pickup only changes the odds if he'd start for this owner. Size a cap bid by the odds gained "
                             "(a point or two of playoff odds is a small bid; several points late in a close race is worth paying for). "
                             "He also costs cap space on future years if signed long, so mention the contract.")
        return out

    def playoffs() -> dict[str, Any]:
        model = odds() if odds is not None else None
        if model is None:
            return {"error": "playoff odds aren't available right now (no schedule or power rankings yet)"}
        out = model.summary()
        return {**out, "how_to_use": f"Each team's chance (percent) to finish top {out['spots']} over {SIMS_NOTE}, and the change since last week. "
                                     "It plays out the remaining schedule using each team's expected weekly points from the power rankings."}

    def profile(owner: str) -> dict[str, Any]:
        prof = profiles()
        name = next((k for k in prof if owner.casefold() in k.casefold()), None)
        out: dict[str, Any] = {"owner": name or owner, "profile": prof.get(name) if name else "no completed trades on record"}
        mine = [o for o in (outcomes(partner=name or owner) if outcomes else [])]
        if mine:
            out["your_pitches_to_them"] = pitch_record(mine).get(str(name or owner)) or pitch_record(mine)
            out["what_they_said"] = [o.get("note") for o in mine if o.get("note")][:5]
        return out

    windows_schema = {"type": "object", "additionalProperties": {"type": "string", "enum": list(WINDOWS)},
                      "description": "owner name -> contending / middle / rebuilding, for any team whose plans you know from the owner or league notes; "
                                     "otherwise each window is worked out from playoff odds (or standings and lineup), roster age and trading habits"}
    return [ToolSpec(
        "find_trade_targets",
        "Shortlist trades for this owner that the other owner has a reason to take: each team valued its own way (contenders weigh this season, rebuilders the "
        "dynasty market, contracts count), cap-legal for both, and better for this owner's lineup. Each idea is a ladder: open_with (cheapest they might take), "
        "likely_close, walk_away_after (the most to pay), plus the market view and why they say yes. Use it first for any 'find me a trade' or 'who can I get at X' "
        "question. position defaults to this owner's weakest; goal 'now' (default) or 'future'; partner limits to one owner.",
        {"type": "object", "properties": {
            "position": {"type": "string", "enum": list(POSITIONS)},
            "goal": {"type": "string", "enum": ["now", "future"]},
            "partner": {"type": "string", "description": "owner or team name, to look at one team only"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            "windows": windows_schema,
        }},
        run,
    ), ToolSpec(
        "evaluate_trade",
        "Judge one specific trade the way each owner would: what each side gets back by its own goals (likely yes / worth a try / likely no), the market view, "
        "why the other owner might want to move the player, and a cheaper opening if one piece can come out. Use for any 'is this fair', 'would X take this' "
        "or 'what should I offer for Y' question, and before pitching a trade you built yourself.",
        {"type": "object", "properties": {
            "partner": {"type": "string", "description": "the other owner or team"},
            "you_get": {"type": "array", "items": {"type": "string"}, "description": "players this owner receives"},
            "you_send": {"type": "array", "items": {"type": "string"}, "description": "players this owner sends"},
            "picks_you_get": {"type": "array", "items": {"type": "string"}, "description": "e.g. '2027 1st'"},
            "picks_you_send": {"type": "array", "items": {"type": "string"}},
            "windows": windows_schema,
        }, "required": ["partner"]},
        evaluate,
    ), ToolSpec(
        "get_owner_trade_profile",
        "How one owner trades, from every completed trade in this league on Sleeper: how often, with whom, positions they buy and sell, picks in and out, "
        "whether they buy youth or veterans, plus how this owner's own past pitches to them went (accepted, declined, not completed) and what they said. "
        "Use before pitching or pricing a trade with that owner.",
        {"type": "object", "properties": {"owner": {"type": "string"}}, "required": ["owner"]},
        profile,
    )] + ([ToolSpec(
        "get_playoff_odds",
        "Every team's playoff odds: the remaining regular season played out thousands of times with each team's expected weekly points, scored the "
        "league's way (2 per win, 1 for a top-5 week, then points for). Also the change since last week and weeks left. Use for 'will I make the "
        "playoffs', 'am I a contender' or buy/sell questions. evaluate_trade already includes the odds before and after for both teams.",
        {"type": "object", "properties": {}},
        playoffs,
    )] if odds is not None else []) + ([ToolSpec(
        "get_pickup_odds",
        "What a free-agent pickup does to this owner's playoff odds: the player's expected weekly points, the change to the starting lineup "
        "(after dropping someone, if named) and the playoff odds before and after. With bid (cap dollars), also odds gained per dollar. "
        "Use for waiver, free agent and bid questions.",
        {"type": "object", "properties": {
            "player": {"type": "string", "description": "the free agent"},
            "drop": {"type": "string", "description": "the player this owner would cut, if any"},
            "bid": {"type": "number", "description": "the cap dollars they'd bid"},
        }, "required": ["player"]},
        pickup,
    )] if odds is not None and free_agent is not None else [])


SIMS_NOTE = "5,000 simulated rest-of-seasons"


def trade_playoff_odds(league: League, asker_team_id: str, partner: str, you_get: list[str], you_send: list[str], odds: Callable[[], Any]) -> dict[str, Any]:
    """Both teams' playoff odds with and without the trade, from the change in each starting lineup's weekly points."""
    from league_ai.playoff_odds import trade_odds

    model = odds()
    tid = league.team_id(partner)
    if model is None or not tid:
        return {"note": "playoff odds unavailable"}
    gets = [p for p in (league.find_player(n, tid) for n in you_get) if p]
    sends = [p for p in (league.find_player(n, asker_team_id) for n in you_send) if p]
    me = str(league.teams[asker_team_id].get("owner_name") or "")
    them = str(league.teams[tid].get("owner_name") or partner)
    return trade_odds(model, model.name(me), model.name(them), league.gain(asker_team_id, sends, gets, "now"), league.gain(tid, gets, sends, "now"))


def _load_profiles(history: Any, current_season: int | None) -> dict[str, dict[str, Any]]:
    try:
        rows = (history.client.table("league_history_events").select("event_id, season, owner_name, player_id, player_name, position, details, summary")
                .eq("league_id", history.league_id).eq("kind", "trade").limit(5000).execute().data or [])
    except Exception:
        return {}
    ids = sorted({str(r["player_id"]) for r in rows if r.get("player_id")})
    ages: dict[str, float] = {}
    for i in range(0, len(ids), 150):
        try:
            for r in history.client.table("nfl_players").select("sleeper_id, age").in_("sleeper_id", ids[i:i + 150]).execute().data or []:
                if r.get("age") is not None:
                    ages[str(r["sleeper_id"])] = float(r["age"])
        except Exception:
            pass
    return owner_profiles(rows, ages, current_season)


def _habits(history: Any, partners: set[str], position: str | None) -> dict[str, Any]:
    """How often each partner trades here, and recent trades at this position, to price the offer off real deals."""
    try:
        tendencies = history.trade_partners().get("trades_by_owner", {})
    except Exception:
        tendencies = {}
    try:
        recent = [t for t in history.events(kind="trade", limit=40) if not position or (t.get("position") or "").upper() == position][:4]
    except Exception:
        recent = []
    return {
        "trades_made_here": {p: int(tendencies.get(p, 0)) for p in partners},
        f"recent_{(position or 'all').lower()}_trades_here": [f"{t.get('season')}: {t.get('summary')}" for t in recent if t.get("summary")],
    }


def load_league(client: Any, state: Mapping[str, Any], season: int, roster_positions: list[str], salary_cap: Any, asker_team_id: str,
                build_strengths: Callable[[Any, Mapping[str, Any], int], list[PlayerStrength]], league_id: str | None = None,
                playoff_start: int | None = 15) -> tuple[League, dict[str, float], str]:
    """Wire the finder to the live league: player strengths, contracts, cap space, this season's records and pick values."""
    from services.team_roster_state import calculate_team_financials, state_cap_adjustments, state_roster

    roster = state_roster(state)
    adjustments = state_cap_adjustments(state)
    players = build_strengths(client, state, season)
    hits = {str(r.get("sleeper_player_id") or r.get("player_id") or ""): float(r.get("cap_hit") or r.get("salary") or 0) for r in roster}
    values = _market_values(client, [p.sleeper_id for p in players])
    from league_ai.team_context import attach_schedules
    attach_schedules(players, client, season, playoff_start=playoff_start)
    for p in players:
        p.cap_hit = hits.get(p.sleeper_id, 0.0)  # type: ignore[attr-defined]
        if p.sleeper_id in values:
            p.market_value = values[p.sleeper_id]  # type: ignore[attr-defined]  # superflex dynasty market
    teams = list(state.get("teams", []))
    cap = {str(t["league_team_id"]): float(calculate_team_financials(roster, adjustments, salary_cap=salary_cap, league_team_id=str(t["league_team_id"]))["cap_space"])
           for t in teams}
    by_owner = {str(t.get("owner_name") or ""): str(t["league_team_id"]) for t in teams}
    records: dict[str, tuple[int, int]] = {}
    if league_id:
        try:
            rows = (client.table("league_matchups").select("owner_name, won, is_playoff").eq("league_id", league_id).eq("season", int(season))
                    .limit(500).execute().data or [])
            tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for r in rows:
                if not r.get("is_playoff") and r.get("owner_name") in by_owner:
                    tally[by_owner[r["owner_name"]]][0 if r.get("won") else 1] += 1
            records = {tid: (w, l) for tid, (w, l) in tally.items()}
        except Exception:
            pass
    leanings: dict[str, str] = {}
    if league_id:
        from league_ai.trade_backtest import load_styles
        leanings = load_styles(client, league_id)
    return League(players, teams, roster_positions, records=records, pick_values=_pick_values(client, season), leanings=leanings), cap, str(asker_team_id)


def _market_values(client: Any, ids: list[str]) -> dict[str, float]:
    try:
        latest = client.table("nfl_market_values").select("scrape_date").order("scrape_date", desc=True).limit(1).execute().data or []
    except Exception:
        return {}
    if not latest:
        return {}
    out: dict[str, float] = {}
    ids = sorted({i for i in ids if i})
    for i in range(0, len(ids), 150):
        try:
            for r in (client.table("nfl_market_values").select("sleeper_id, value_2qb").eq("scrape_date", latest[0]["scrape_date"])
                      .in_("sleeper_id", ids[i:i + 150]).execute().data or []):
                if r.get("value_2qb") is not None:
                    out[str(r["sleeper_id"])] = float(r["value_2qb"])
        except Exception:
            pass
    return out


def _pick_values(client: Any, season: int) -> list[tuple[str, float]]:
    """Next two drafts' pick values on the superflex market scale, latest scrape."""
    try:
        rows = client.table("nfl_pick_values").select("pick_label, draft_year, value_2qb, scrape_date").order("scrape_date", desc=True).limit(400).execute().data or []
    except Exception:
        return []
    latest = rows[0]["scrape_date"] if rows else None
    return [(str(r["pick_label"]), float(r["value_2qb"])) for r in rows
            if r.get("scrape_date") == latest and r.get("value_2qb") and int(r.get("draft_year") or 0) in (int(season) + 1, int(season) + 2)]


# ---- owner trade profiles (from every completed trade on Sleeper) ------------------------------------

def owner_profiles(trade_rows: list[Mapping[str, Any]], ages: Mapping[str, float] | None = None, current_season: int | None = None) -> dict[str, dict[str, Any]]:
    """How each owner trades, from league_history_events trade legs (one row per player or pick that moved)."""
    ages = ages or {}
    trades: dict[str, Mapping[str, Any]] = {}
    for r in trade_rows:
        tid = str(r.get("event_id") or "").split(":")[1] if str(r.get("event_id") or "").startswith("sleeper:") else str(r.get("summary") or "")
        trades.setdefault(tid, r)
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "by_season": defaultdict(int), "partners": defaultdict(int), "got": defaultdict(int), "gave": defaultdict(int),
                                                            "picks_in": 0, "picks_out": 0, "ages_in": [], "ages_out": []})
    for tid, r in trades.items():
        d = r.get("details") or {}
        parts = [p for p in d.get("participants") or [] if p]
        season = int(r.get("season") or 0)
        for owner in parts:
            s = stats[owner]
            s["trades"] += 1
            s["by_season"][season] += 1
            for other in parts:
                if other != owner:
                    s["partners"][other] += 1
        for leg in d.get("players") or []:
            pos = leg.get("position") or "?"
            age = _age_at(ages.get(str(_leg_id(trade_rows, tid, leg))), season, current_season)
            if leg.get("to"):
                stats[leg["to"]]["got"][pos] += 1
                if age is not None:
                    stats[leg["to"]]["ages_in"].append(age)
            if leg.get("from"):
                stats[leg["from"]]["gave"][pos] += 1
                if age is not None:
                    stats[leg["from"]]["ages_out"].append(age)
        for pk in d.get("picks") or []:
            if pk.get("to"):
                stats[pk["to"]]["picks_in"] += 1
            if pk.get("from"):
                stats[pk["from"]]["picks_out"] += 1
    out = {}
    for owner, s in stats.items():
        style = []
        net_picks = s["picks_in"] - s["picks_out"]
        if abs(net_picks) >= 2:
            style.append("collects draft picks" if net_picks > 0 else "spends draft picks to win now")
        if len(s["ages_in"]) >= 2 and len(s["ages_out"]) >= 2:
            a_in, a_out = sum(s["ages_in"]) / len(s["ages_in"]), sum(s["ages_out"]) / len(s["ages_out"])
            if a_in - a_out >= 1.5:
                style.append(f"buys veterans (gets players about {a_in:.0f}, sends about {a_out:.0f})")
            elif a_out - a_in >= 1.5:
                style.append(f"buys youth (gets players about {a_in:.0f}, sends about {a_out:.0f})")
        out[owner] = {
            "trades": s["trades"],
            "trades_by_season": dict(sorted(s["by_season"].items())),
            "favorite_partners": dict(sorted(s["partners"].items(), key=lambda x: -x[1])[:3]),
            "positions_acquired": dict(sorted(s["got"].items(), key=lambda x: -x[1])),
            "positions_sent": dict(sorted(s["gave"].items(), key=lambda x: -x[1])),
            "picks": f"{s['picks_in']} in, {s['picks_out']} out",
            "style": "; ".join(style) or "no clear lean yet",
        }
    return out


def _leg_id(rows: list[Mapping[str, Any]], tid: str, leg: Mapping[str, Any]) -> str | None:
    for r in rows:
        if str(r.get("event_id") or "").startswith(f"sleeper:{tid}:p") and r.get("player_name") == leg.get("player"):
            return r.get("player_id")
    return None


def _age_at(age_now: float | None, season: int, current_season: int | None) -> float | None:
    if age_now is None:
        return None
    return float(age_now) - max(0, (current_season or season) - season)


def pitch_record(outcomes: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """This owner's trade pitches by partner: accepted / declined / countered / not_completed counts."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for o in outcomes:
        out[str(o.get("partner") or "unknown")][str(o.get("outcome"))] += 1
    return {k: dict(v) for k, v in out.items()}
