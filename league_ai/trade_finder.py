"""Trade finder: shortlist trades the other owner has a reason to take. No model call.

The AI used to build trade ideas in its head across every roster, and pitched deals the other
side had no reason to accept. This scans the league in code instead: for the position the owner
wants, it finds teams that are deep there and weak somewhere the owner has spare value, keeps only
packages that are close on dynasty value and fit both teams' cap, and ranks them by how much the
deal helps both sides. The AI then judges the shortlist and writes the pitch.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from statistics import median
from typing import Any, Callable, Iterable, Mapping

from league_ai.power import PlayerStrength, compute_power, fill_lineup
from league_ai.tools import ToolSpec

POSITIONS = ("QB", "RB", "WR", "TE")
DEFAULT_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
# the side giving up the target must get at least this share of its dynasty value back,
# and the asker should not pay more than the upper bound
FAIR_LOW, FAIR_HIGH = 0.9, 1.3


def _slots(roster_positions: Iterable[str] | None) -> list[str]:
    return [s for s in (roster_positions or []) if s not in {"BN", "IR", "TAXI"}] or list(DEFAULT_SLOTS)


class League:
    """Each team's lineup strength by position, so a trade's effect can be measured both ways."""

    def __init__(self, players: list[PlayerStrength], teams: list[Mapping[str, Any]], roster_positions: list[str] | None):
        self.slots = _slots(roster_positions)
        self.teams = {str(t["league_team_id"]): t for t in teams}
        self.players = players
        self.by_team: dict[str, list[PlayerStrength]] = defaultdict(list)
        for p in players:
            self.by_team[p.team_id].append(p)
        power = {tp.team_id: tp for tp in compute_power(players, teams, self.slots)}
        self.now_rank = {tid: tp.now_rank for tid, tp in power.items()}
        self.dynasty_rank = {tid: tp.dynasty_rank for tid, tp in power.items()}

    def mode(self, team_id: str) -> str:
        """'now' for teams built to win this year, 'dynasty' for teams whose value sits in the future."""
        n, d = self.now_rank.get(team_id, 99), self.dynasty_rank.get(team_id, 99)
        return "now" if n <= max(4, len(self.teams) // 2 - 1) or n <= d else "dynasty"

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


def find_trades(*, league: League, asker_team_id: str, cap_space: Mapping[str, float], want_position: str | None = None,
                goal: str | None = None, partner: str | None = None, limit: int = 5) -> dict[str, Any]:
    asker = str(asker_team_id)
    key = "dynasty" if goal in ("future", "dynasty", "rebuild") else "now"
    want = (want_position or "").upper() or next(iter(league.needs(asker, key)), None)
    if want not in POSITIONS:
        return {"error": "say which position to upgrade (QB, RB, WR or TE); your lineup is at or above the league median everywhere"}
    ranks = league.position_ranks(key)
    my_starters = league.starters(asker)
    my_roster = league.by_team.get(asker, [])
    ideas: list[dict[str, Any]] = []
    for tid, team in league.teams.items():
        if tid == asker or (partner and partner.casefold() not in {str(team.get("owner_name") or "").casefold(), str(team.get("team_name") or "").casefold()}):
            continue
        their_mode = league.mode(tid)
        their_needs = league.needs(tid, their_mode)
        if not their_needs:
            continue
        their_roster = league.by_team.get(tid, [])
        targets = [p for p in their_roster if p.position == want and p.available]
        # what the asker can offer: players at the partner's weak spots, never the asker's own need position
        offers = [p for p in my_roster if p.position in their_needs and p.position != want]
        for target in targets:
            for size in (1, 2):
                for package in combinations(offers, size):
                    sends = list(package)
                    give = sum(p.dynasty for p in sends)
                    if target.dynasty <= 0 or not (FAIR_LOW * target.dynasty <= give <= FAIR_HIGH * target.dynasty):
                        continue
                    my_gain = league.gain(asker, sends, [target], key)
                    their_gain = league.gain(tid, [target], sends, their_mode)
                    if my_gain <= 0 or their_gain <= 0:
                        continue
                    cap_me = cap_space.get(asker, 0.0) - _cap(target) + sum(_cap(p) for p in sends)
                    cap_them = cap_space.get(tid, 0.0) - sum(_cap(p) for p in sends) + _cap(target)
                    if cap_me < 0 or cap_them < 0:
                        continue
                    ideas.append({
                        "score": round(min(my_gain, their_gain) + 0.5 * my_gain, 2),
                        "partner": team.get("owner_name"),
                        "you_get": [_label(target)],
                        "you_send": [_label(p) for p in sends],
                        "your_lineup_gain": my_gain,
                        "their_lineup_gain": their_gain,
                        "why_they_say_yes": _why(league, tid, their_mode, sends, ranks),
                        "value_check": f"you send {round(100 * give / target.dynasty)}% of what you get on dynasty value (fair band {int(FAIR_LOW * 100)}-{int(FAIR_HIGH * 100)}%)",
                        "cap_after": {"you": round(cap_me, 2), "them": round(cap_them, 2)},
                        "you_lose_a_starter": any(p.sleeper_id in my_starters for p in sends),
                    })
    ideas.sort(key=lambda x: -x["score"])
    seen: set[tuple[str, str]] = set()
    best: list[dict[str, Any]] = []
    for idea in ideas:  # one idea per partner and target, so the list isn't five versions of one deal
        k = (str(idea["partner"]), idea["you_get"][0])
        if k in seen:
            continue
        seen.add(k)
        best.append({k2: v for k2, v in idea.items() if k2 != "score"})
        if len(best) >= limit:
            break
    return {
        "upgrading": want,
        "your_group_rank": f"{want} group ranks {ranks[asker][want]} of {len(league.teams)}",
        "valued_on": "this season's production" if key == "now" else "dynasty value (age, market tier, contract years)",
        "ideas": best,
        "how_to_use": "These pass a value, cap and both-sides-improve check. Pick the best fit, confirm with simulate_trade, and add a pick if the value check is near the low end. "
                      "Name why that owner says yes. If none fit, say so rather than forcing a lopsided deal.",
    }


def _cap(p: PlayerStrength) -> float:
    return float(getattr(p, "cap_hit", 0.0) or 0.0)


def _label(p: PlayerStrength) -> str:
    rank = f"{p.position}{int(round(p.pos_rank))}" if p.pos_rank else p.position
    cap = getattr(p, "cap_hit", None)
    contract = f", ${cap:g} x {p.years_left}yr" if cap is not None else ""
    ppg = f", {p.season_ppg:.1f} ppg" if p.season_ppg is not None else ""
    return f"{p.name} ({rank}{ppg}{contract})"


def _why(league: League, team_id: str, mode: str, sends: list[PlayerStrength], ranks: Mapping[str, Mapping[str, int]]) -> str:
    n = len(league.teams)
    parts = [f"their {p.position} group ranks {ranks[team_id][p.position]} of {n}" for p in sends]
    window = "they're contending, so this-season points matter most to them" if mode == "now" else "their value sits in the future, so they want youth and contract years"
    return "; ".join(dict.fromkeys(parts)) + f"; {window}"


def trade_finder_tools(load: Callable[[], tuple[League, Mapping[str, float], str]], history: Any | None = None,
                       outcomes: Callable[..., list[Mapping[str, Any]]] | None = None, current_season: int | None = None) -> list[ToolSpec]:
    """load() -> (League, cap space by team id, asker team id); called once per answer that needs it.
    history: LeagueHistory for completed trades; outcomes(partner=None) -> this owner's recorded trade outcomes."""
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

    def run(position: str | None = None, goal: str | None = None, partner: str | None = None, limit: int = 5) -> dict[str, Any]:
        if "league" not in cache:
            cache["league"], cache["cap"], cache["asker"] = load()
        out = find_trades(league=cache["league"], asker_team_id=cache["asker"], cap_space=cache["cap"], want_position=position, goal=goal,
                          partner=partner, limit=max(1, min(int(limit or 5), 8)))
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

    def profile(owner: str) -> dict[str, Any]:
        prof = profiles()
        name = next((k for k in prof if owner.casefold() in k.casefold()), None)
        out: dict[str, Any] = {"owner": name or owner, "profile": prof.get(name) if name else "no completed trades on record"}
        mine = [o for o in (outcomes(partner=name or owner) if outcomes else [])]
        if mine:
            out["your_pitches_to_them"] = pitch_record(mine).get(str(name or owner)) or pitch_record(mine)
            out["what_they_said"] = [o.get("note") for o in mine if o.get("note")][:5]
        return out

    return [ToolSpec(
        "find_trade_targets",
        "Shortlist realistic trades for this owner: teams deep at the position they want and weak where this owner has spare value, packages within a fair dynasty-value band, "
        "cap-legal for both sides, and better for both lineups. Each idea says why that owner would say yes. Use it first for any 'find me a trade' or 'who can I get at X' "
        "question, then confirm the pick with simulate_trade. position defaults to this owner's weakest; goal 'now' (default) or 'future'; partner limits to one owner.",
        {"type": "object", "properties": {
            "position": {"type": "string", "enum": list(POSITIONS)},
            "goal": {"type": "string", "enum": ["now", "future"]},
            "partner": {"type": "string", "description": "owner or team name, to look at one team only"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        }},
        run,
    ), ToolSpec(
        "get_owner_trade_profile",
        "How one owner trades, from every completed trade in this league on Sleeper: how often, with whom, positions they buy and sell, picks in and out, "
        "whether they buy youth or veterans, plus how this owner's own past pitches to them went (accepted, declined, not completed) and what they said. "
        "Use before pitching or pricing a trade with that owner.",
        {"type": "object", "properties": {"owner": {"type": "string"}}, "required": ["owner"]},
        profile,
    )]


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
                build_strengths: Callable[[Any, Mapping[str, Any], int], list[PlayerStrength]]) -> tuple[League, dict[str, float], str]:
    """Wire the finder to the live league: player strengths, contracts and each team's cap space."""
    from services.team_roster_state import calculate_team_financials, state_cap_adjustments, state_roster

    roster = state_roster(state)
    adjustments = state_cap_adjustments(state)
    players = build_strengths(client, state, season)
    hits = {str(r.get("sleeper_player_id") or r.get("player_id") or ""): float(r.get("cap_hit") or r.get("salary") or 0) for r in roster}
    for p in players:
        p.cap_hit = hits.get(p.sleeper_id, 0.0)  # type: ignore[attr-defined]
    teams = list(state.get("teams", []))
    cap = {str(t["league_team_id"]): float(calculate_team_financials(roster, adjustments, salary_cap=salary_cap, league_team_id=str(t["league_team_id"]))["cap_space"])
           for t in teams}
    return League(players, teams, roster_positions), cap, str(asker_team_id)


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
