"""Roster moves the league's own rules make worth planning: which contracts to cut (and when), which rookie to taxi,
who goes on IR, and whether to pick up a rookie option. No model call.

Every rule is read from the league's settings (league_rules and the League AI settings), never assumed:
dead cap % per remaining season (and whether $1 deals drop free), IR and taxi slots and what they count against the
cap, whether taxi is rookies-only and skips a contract year, and the rookie option's years and price.

Money is in cap dollars. A player's worth this season is the fair salary for his value rank in this league (the k-th
most valuable rostered player should cost about the k-th biggest cap hit). Later seasons carry that forward by how
players of his position and age held their value over 2012-2025 (RETENTION, fit with league_ai.aging). Freed cap is
worth less than a dollar: in the offseason it buys players at auction (OFFSEASON_REUSE), in season only waiver adds
for the weeks left (IN_SEASON_REUSE).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# Share of this season's value above replacement a player produced 1, 2 and 3 seasons later, by age 21..36
# (league_ai.aging.retention_table on 2012-2025 nflverse seasons, league scoring).
AGES = range(21, 37)
RETENTION = {
    "QB": {1: [.89, .89, .76, .76, .76, .69, .69, .69, .69, .69, .69, .69, .69, .69, .69, .69],
           2: [.71, .71, .71, .71, .71, .71, .71, .71, .71, .71, .71, .62, .56, .56, .56, .56],
           3: [.67, .67, .67, .67, .67, .52, .52, .52, .52, .52, .43, .43, .43, .43, .43, .35]},
    "RB": {1: [1.07, 1.07, .63, .63, .62, .62, .51, .51, .43, .43, .43, .43, .43, .43, .43, .43],
           2: [1.0, .56, .45, .43, .43, .43, .36, .31, .31, .31, .31, .31, .31, .31, .31, .31],
           3: [.61, .53, .39, .39, .29, .29, .2, .2, .2, .2, .2, .2, .2, .2, .2, .2]},
    "WR": {1: [.95, .87, .87, .87, .77, .68, .68, .67, .6, .47, .47, .47, .47, .47, .47, .47],
           2: [.95, .94, .94, .85, .59, .58, .48, .46, .36, .32, .32, .32, .32, .32, .32, .32],
           3: [.86, .86, .77, .65, .52, .45, .4, .3, .21, .21, .21, .21, .21, .21, .21, .21]},
    "TE": {1: [.6, .6, .6, .6, .6, .6, .6, .6, .59, .59, .55, .55, .55, .5, .5, .5],
           2: [.59, .59, .59, .59, .48, .48, .48, .48, .48, .39, .39, .39, .39, .39, .39, .3],
           3: [.45, .45, .45, .45, .45, .45, .41, .41, .38, .38, .31, .31, .3, .3, .3, .25]},
}
OFFSEASON_REUSE = 0.9      # a freed cap dollar buys about this much worth at the auction (sim: owners' auction buys ~break even)
IN_SEASON_REUSE = 0.3      # in season it only buys waiver adds
ROOKIE_OPTION = {1: 25.0, 2: 15.0, 3: 7.0}   # option salary by rookie round (season_engine.rookie_contract_authority)
INJURED = ("IR", "OUT", "PUP", "NFI", "DOUBTFUL")


@dataclass
class Rules:
    dead_cap_pct: float = 50.0
    one_dollar_free: bool = True
    ir_slots: int = 1
    ir_fraction: float = 0.5
    taxi_slots: int = 1
    taxi_fraction: float = 0.5
    taxi_rookies_only: bool = True
    taxi_skips_year: bool = True
    option_years: int = 1
    rookie_years: int = 3


def rules_from(league_rules: Mapping[str, Any] | None, ai: Any | None) -> Rules:
    """The league's settings as they are saved (league_rules row + LeagueAISettings); defaults only where unset."""
    lr, d = dict(league_rules or {}), Rules()

    def num(v: Any, default: float) -> float:
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default
    return Rules(
        dead_cap_pct=num(lr.get("default_dead_cap_pct"), d.dead_cap_pct),
        one_dollar_free=bool(getattr(ai, "one_dollar_deals_no_dead_cap", d.one_dollar_free)),
        ir_slots=int(getattr(ai, "ir_limit", d.ir_slots)),
        ir_fraction=num(getattr(ai, "ir_cap_fraction", None), d.ir_fraction),
        taxi_slots=int(getattr(ai, "taxi_limit", d.taxi_slots)),
        taxi_fraction=num(getattr(ai, "taxi_cap_fraction", None), d.taxi_fraction),
        taxi_rookies_only=bool(getattr(ai, "taxi_rookies_only", d.taxi_rookies_only)),
        taxi_skips_year=bool(getattr(ai, "taxi_skips_contract_year", d.taxi_skips_year)),
        option_years=int(num(lr.get("rookie_option_years"), d.option_years)),
        rookie_years=int(num(lr.get("rookie_contract_years"), d.rookie_years)),
    )


def dead_cap(salary: float, years_left: int, rules: Rules) -> tuple[float, float]:
    """(per season, total) dead cap if dropped now: the league's % of each remaining season; $1 deals free if set."""
    salary = float(salary or 0)
    if salary <= 0 or (rules.one_dollar_free and salary <= 1):
        return 0.0, 0.0
    per = round(salary * rules.dead_cap_pct / 100.0, 2)
    return per, round(per * max(int(years_left or 1), 1), 2)


def retention(pos: str, age: float | None, k: int) -> float:
    """Share of this season's worth a player of this position and age (now) keeps k seasons out. Past 3 seasons it
    keeps falling at the year-2 to year-3 rate."""
    if k <= 0 or pos not in RETENTION or age is None:
        return 1.0
    i = min(max(int(round(age)), AGES[0]), AGES[-1]) - AGES[0]
    r = RETENTION[pos][min(k, 3)][i]
    if k > 3:
        step = RETENTION[pos][3][i] / max(RETENTION[pos][2][i], 1e-6)
        r *= min(step, 1.0) ** (k - 3)
    return r


def worth_by_year(fair_now: float, pos: str, age: float | None, years: int) -> list[float]:
    """Fair salary this season and each later season of a deal."""
    return [round(fair_now * retention(pos, age, k), 2) for k in range(max(years, 1))]


def cut_or_keep(*, name: str, pos: str, age: float | None, salary: float, years_left: int, fair_now: float, rules: Rules,
                offseason: bool, season_left: float = 1.0) -> dict[str, Any]:
    """Keep him (his worth each season, his salary paid) or cut him (dead cap paid, the rest of his salary freed and
    reused). season_left: share of this season still to play (in season); the offseason counts next season as year one."""
    years = max(int(years_left or 1), 1)
    worth = worth_by_year(fair_now, pos, age, years)
    per, total = dead_cap(salary, years, rules)
    rows = []
    for k in range(years):
        share = 1.0 if (offseason or k) else season_left
        reuse = OFFSEASON_REUSE if (offseason or k) else IN_SEASON_REUSE
        keep = share * worth[k]                     # what he gives that season
        cut = share * reuse * (salary - per)        # what the freed room buys that season
        rows.append({"season": k + 1, "worth": worth[k], "keep_value": round(keep, 2), "cut_value": round(cut, 2)})
    keep_v, cut_v = sum(r["keep_value"] for r in rows), sum(r["cut_value"] for r in rows)
    # best season to let him go: keep through season j, cut after it (j = 0 cuts now, j = years keeps the whole deal)
    plans = [sum(r["keep_value"] for r in rows[:j]) + sum(r["cut_value"] for r in rows[j:]) for j in range(years + 1)]
    j = max(range(years + 1), key=lambda i: (round(plans[i], 1), i))   # ties keep him longer
    if salary <= 1 and rules.one_dollar_free:
        call, why = "keep or drop freely", "a $1 deal carries no dead cap and ends after this season"
    elif j == years:
        call, why = "keep", f"his worth (${keep_v:.2f} over the deal) beats what the freed room would buy (${cut_v:.2f})"
    else:
        if j == 0:
            call = "cut now"
        elif offseason:
            call = f"keep {j} season{'s' if j > 1 else ''}, then cut in that offseason (before the auction)"
        else:
            call = "cut after this season, before the auction" if j == 1 else f"keep through {j - 1} more season{'s' if j > 2 else ''}, then cut"
        per_left = per * (years - j)
        why = (f"dead cap ${per:.2f} a season for the {years - j} season(s) left at that point (${per_left:.2f}); "
               f"the ${salary - per:.2f} a season it frees buys more than he'd give")
        if j == 1 and not offseason:
            why += ". He still helps this season, and freed room now would only buy waiver adds"
    return {"player": name, "salary": salary, "years_left": years, "dead_cap_per_season": per, "dead_cap_total": total,
            "keep_value": round(keep_v, 2), "cut_value": round(cut_v, 2), "best_plan_value": round(plans[j], 2),
            "call": call, "why": why, "by_season": rows}


def taxi_choice(rookies: Iterable[Mapping[str, Any]], rules: Rules) -> dict[str, Any]:
    """Which eligible rookie to stash. Taxi costs his production this season and charges taxi_fraction of his salary;
    if the league skips a contract year on taxi, his cheap deal runs one season longer. rookies: name, pos, age,
    salary, years (rookie deal length), fair_now, eligible."""
    rows = []
    for r in rookies:
        if not r.get("eligible", True):
            continue
        salary, years = float(r.get("salary") or 0), int(r.get("years") or rules.rookie_years)
        worth = worth_by_year(float(r.get("fair_now") or 0), str(r.get("pos")), r.get("age"), years + 1)
        lost_now = worth[0] - salary * (1 - rules.taxi_fraction)      # production given up, less cap saved this season
        extra = max(0.0, worth[years] - salary) if rules.taxi_skips_year else 0.0  # one more season at his rookie salary
        rows.append({"player": r.get("name"), "gain": round(extra - lost_now, 2), "extra_year_value": round(extra, 2),
                     "this_season_cost": round(lost_now, 2)})
    rows.sort(key=lambda x: -x["gain"])
    if not rows:
        return {"call": "no eligible rookie", "candidates": []}
    best = rows[0]
    call = f"taxi {best['player']}" if best["gain"] > 0 else "no rookie gains from taxi this year (each helps more on the active roster)"
    return {"call": call, "slots": rules.taxi_slots, "candidates": rows[:5],
            "how_it_works": f"taxi: counts {round(rules.taxi_fraction * 100)}% of salary, can't play this season"
                            + ("; the season doesn't use up a contract year" if rules.taxi_skips_year else "")}


def ir_choice(players: Iterable[Mapping[str, Any]], rules: Rules) -> dict[str, Any]:
    """Who to put in the IR slot: the injured player whose cap credit (1 - ir_fraction of salary) is largest."""
    rows = [{"player": p.get("name"), "status": p.get("injury"), "cap_credit": round(float(p.get("salary") or 0) * (1 - rules.ir_fraction), 2)}
            for p in players if str(p.get("injury") or "").upper().startswith(INJURED)]
    rows.sort(key=lambda x: -x["cap_credit"])
    if not rows:
        return {"call": "nobody needs the IR slot right now", "candidates": []}
    return {"call": f"IR {rows[0]['player']}", "slots": rules.ir_slots, "candidates": rows[:4],
            "how_it_works": f"IR: counts {round(rules.ir_fraction * 100)}% of salary while out, frees a roster spot"}


def rookie_option(*, name: str, pos: str, age: float | None, rookie_round: int, fair_now: float, rules: Rules) -> dict[str, Any]:
    """Pick up the option (option salary for option_years) or let him walk to free agency."""
    price = ROOKIE_OPTION.get(int(rookie_round or 0))
    if price is None or rules.option_years <= 0:
        return {"player": name, "call": "no option on this deal"}
    worth = worth_by_year(fair_now, pos, age, rules.option_years + 1)[1:]   # the option seasons start next season
    surplus = sum(w - price for w in worth)
    call = "pick up the option" if surplus > 0 else "let him walk"
    return {"player": name, "option_salary": price, "option_years": rules.option_years, "worth_by_season": worth,
            "surplus": round(surplus, 2), "call": call,
            "why": f"worth about ${sum(worth) / len(worth):.0f} a season against a ${price:.0f} option"}


def plan(players: list[Any], team_id: str, rules: Rules, *, offseason: bool, season: int, season_left: float = 1.0,
         fair_cap: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Every roster move worth a look for one team. players: PlayerStrength-like with salary, years_left, designation,
    contract_type, rookie_round, rookie_year, age, position, injury. fair_cap: sleeper_id -> fair salary this season."""
    fair = dict(fair_cap or {})
    mine = [p for p in players if str(p.team_id) == str(team_id)]
    cuts = []
    for p in mine:
        salary = float(getattr(p, "salary", 0) or 0)
        if salary <= 1 or p.sleeper_id not in fair or getattr(p, "contract_type", None) == "rookie":
            continue  # rookie deals are judged at the option decision: their worth now undersells their growth
        r = cut_or_keep(name=p.name, pos=p.position, age=p.age, salary=salary, years_left=int(p.years_left or 1),
                        fair_now=fair[p.sleeper_id], rules=rules, offseason=offseason, season_left=season_left)
        if r["call"] != "keep":
            r["gain_vs_keeping"] = round(r["best_plan_value"] - r["keep_value"], 2)
            cuts.append(r)
    cuts.sort(key=lambda r: -r["gain_vs_keeping"])
    out: dict[str, Any] = {"mode": "offseason" if offseason else "in season", "cut_candidates": cuts[:5],
                           "rules": {"dead_cap_pct": rules.dead_cap_pct, "one_dollar_deals_free": rules.one_dollar_free,
                                     "ir_slots": rules.ir_slots, "ir_counts": rules.ir_fraction, "taxi_slots": rules.taxi_slots,
                                     "taxi_counts": rules.taxi_fraction, "taxi_skips_contract_year": rules.taxi_skips_year,
                                     "rookie_option_years": rules.option_years}}
    # taxi: this year's rookies taken off the draft board (the upcoming season's class in the offseason)
    draft_year = season + 1 if offseason else season
    rookies = [{"name": p.name, "pos": p.position, "age": p.age, "salary": float(getattr(p, "salary", 0) or 0),
                "years": int(p.years_left or rules.rookie_years), "fair_now": fair.get(p.sleeper_id, 0.0),
                "eligible": getattr(p, "rookie_year", None) == draft_year or not rules.taxi_rookies_only}
               for p in mine if getattr(p, "rookie_year", None) == draft_year]
    if rookies:
        out["taxi"] = taxi_choice(rookies, rules)
    if not offseason:
        out["ir"] = ir_choice([{"name": p.name, "injury": p.injury, "salary": float(getattr(p, "salary", 0) or 0)} for p in mine
                               if getattr(p, "designation", None) != "taxi"], rules)
    options = [rookie_option(name=p.name, pos=p.position, age=p.age, rookie_round=int(getattr(p, "rookie_round", 0) or 0),
                             fair_now=fair.get(p.sleeper_id, 0.0), rules=rules)
               for p in mine if getattr(p, "contract_type", None) == "rookie" and getattr(p, "rookie_round", None) and int(p.years_left or 0) <= 1]
    if options:
        out["rookie_options"] = [o for o in options if o.get("call") != "no option on this deal"]
    out["how_to_use"] = ("Cut calls weigh his worth each season (fair salary for his value here, aged by position) against the dead cap and what "
                         "the freed room buys (offseason auction > in-season waivers). Say the dead cap in dollars and the timing. Taxi, IR "
                         "and option calls follow this league's settings, shown under rules.")
    return out
