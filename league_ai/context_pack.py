"""Build the league pack: the always-loaded league state Claude reasons from.

Pure functions of their inputs. The roster, dead cap, cap adjustments and
picks come from the same canonical read the Teams page renders
(services.team_roster_state), so the bot's numbers match the app.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

from services.team_roster_state import (
    calculate_team_financials,
    state_cap_adjustments,
    state_roster,
)
from league_ai.settings_store import LeagueAISettings, scoring_summary


@dataclass
class Asker:
    user_id: str
    league_id: str
    league_team_id: str
    owner_name: str
    team_name: str = ""
    role: str = "owner"


@dataclass
class PackInputs:
    league_name: str
    season: int
    state: Mapping[str, Any]
    league_rules: Mapping[str, Any]
    ai_settings: LeagueAISettings
    asker: Asker
    standings: list[dict[str, Any]] = field(default_factory=list)
    past_seasons: list[str] = field(default_factory=list)
    private_memory: list[str] = field(default_factory=list)
    league_notebook: list[str] = field(default_factory=list)
    learning: list[str] = field(default_factory=list)
    activity_limit: int = 40
    data_freshness: dict[str, str] = field(default_factory=dict)


@dataclass
class TeamSummary:
    league_team_id: str
    owner_name: str
    team_name: str
    active_salary: Decimal
    dead_cap: Decimal
    adjustments: Decimal
    cap_used: Decimal
    cap_space: Decimal
    roster_count: int
    ir_count: int
    taxi_count: int
    rookies: int


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _fmt_money(value: Any) -> str:
    d = _money(value)
    return f"${d:.0f}" if d == d.to_integral_value() else f"${d:.2f}"


def _text(value: Any) -> str:
    if value is None or value != value:
        return ""
    return str(value).strip()


def team_summaries(state: Mapping[str, Any], salary_cap: Any) -> list[TeamSummary]:
    roster = state_roster(state)
    adjustments = state_cap_adjustments(state)
    out: list[TeamSummary] = []
    for team in state.get("teams", []):
        tid = _text(team.get("league_team_id"))
        fin = calculate_team_financials(roster, adjustments, salary_cap=salary_cap, league_team_id=tid)
        rows = [r for r in roster if _text(r.get("league_team_id")) == tid]
        out.append(
            TeamSummary(
                league_team_id=tid,
                owner_name=_text(team.get("owner_name")),
                team_name=_text(team.get("team_name")) or _text(team.get("owner_name")),
                active_salary=fin["active_salary"],
                dead_cap=fin["dead_cap"],
                adjustments=fin["adjustments"],
                cap_used=fin["cap_used"],
                cap_space=fin["cap_space"],
                roster_count=len(rows),
                ir_count=sum(1 for r in rows if r.get("roster_designation") == "ir"),
                taxi_count=sum(1 for r in rows if r.get("roster_designation") == "taxi"),
                rookies=sum(1 for r in rows if r.get("is_rookie")),
            )
        )
    return out


def _standings_index(standings: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index = {}
    for row in standings:
        name = _text(row.get("Team") or row.get("team") or row.get("owner_name"))
        if name:
            index[name.casefold()] = row
    return index


def _standing_line(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "no games yet"
    wins = row.get("Wins", row.get("wins", 0))
    losses = row.get("Losses", row.get("losses", 0))
    pts = row.get("Standing Points", row.get("standing_points", 0))
    pf = row.get("PF", row.get("pf", 0))
    pa = row.get("PA", row.get("pa", 0))
    rank = row.get("rank")
    top5 = row.get("Top 5", row.get("top5"))
    parts = [f"{int(wins)}-{int(losses)}", f"{int(pts)} standing pts"]
    if rank:
        parts.insert(0, f"#{int(rank)}")
    if top5 is not None:
        parts.append(f"{int(top5)} top-5 weeks")
    parts.append(f"PF {float(pf):.1f}, PA {float(pa):.1f}")
    return ", ".join(parts)


def _rules_section(rules: Mapping[str, Any], ai: LeagueAISettings) -> str:
    cap = _fmt_money(rules.get("salary_cap", 225))
    dead_pct = rules.get("default_dead_cap_pct", 50)
    lines = [
        f"- Salary cap: {cap}. Cap used = active contract cap hits + dead cap + cap adjustments; cap space = cap - cap used.",
        f"- Dead cap on a drop: {float(dead_pct):g}% of the remaining contract (each remaining season carries its share)."
        + (" A player on a $1 deal carries no dead cap." if ai.one_dollar_deals_no_dead_cap else ""),
        f"- Max contract length {rules.get('max_contract_years', 4)} years; league minimum salary {_fmt_money(rules.get('league_min_salary', 1))}.",
        f"- FAAB pickups sign a {ai.faab_pickup_years}-year deal at ${ai.faab_dollar_per_salary:g} of salary per FAAB dollar; a $0 winning bid counts as {_fmt_money(ai.faab_zero_counts_as)}.",
        f"- Roster max {ai.roster_max} including IR and taxi. IR limit {ai.ir_limit}, cap hit while on IR = {ai.ir_cap_fraction:g} x salary. Taxi limit {ai.taxi_limit}, cap hit while on taxi = {ai.taxi_cap_fraction:g} x salary"
        + (", rookies only" if ai.taxi_rookies_only else "")
        + (", locked for the whole season" if ai.taxi_locked_full_season else "")
        + ("; a taxi season does not consume a contract year." if ai.taxi_skips_contract_year else "."),
        f"- Trades may involve up to {ai.max_trade_teams} teams; players and picks swap"
        + (", and traded FAAB moves cap between the teams (sender loses that cap, receiver gains it)." if ai.traded_faab_moves_cap else "."),
        f"- Standings: {ai.win_points} points per win, +{ai.top_scorer_bonus_points} for finishing in the top {ai.top_scorer_bonus_count} scorers that week; tiebreaker {ai.standings_tiebreaker}. {ai.playoff_teams} playoff teams."
        + (f" Trade deadline {ai.trade_deadline}." if ai.trade_deadline else ""),
    ]
    if rules.get("rookie_contract_years"):
        lines.append(
            f"- Rookie contracts: {rules.get('rookie_contract_years')} years"
            + (f" plus {rules.get('rookie_option_years')} option year(s)" if rules.get("rookie_option_years") else "")
            + (" on a slotted rookie scale." if rules.get("rookie_scale_enabled") else ".")
        )
    lines.append(f"- {scoring_summary(ai)}")
    if ai.house_rules.strip():
        lines.append("- House rules (commissioner notes): " + ai.house_rules.strip().replace("\n", " "))
    return "\n".join(lines)


def _roster_table(rows: list[Mapping[str, Any]]) -> str:
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5, "DST": 5}
    rows = sorted(rows, key=lambda r: (order.get(_text(r.get("pos")).upper(), 9), -float(_money(r.get("cap_hit")))))
    lines = ["| Player | Pos | Cap hit | Yrs left | Type | Flags |", "|---|---|---|---|---|---|"]
    for r in rows:
        flags = []
        d = r.get("roster_designation")
        if d == "ir":
            flags.append("IR")
        elif d == "taxi":
            flags.append("TAXI")
        if r.get("is_rookie"):
            flags.append("rookie")
        acq = _text(r.get("initial_acquisition_type") or r.get("source"))
        ctype = _text(r.get("contract_type"))
        lines.append(
            f"| {_text(r.get('player_name'))} | {_text(r.get('pos')) or '?'} | {_fmt_money(r.get('cap_hit'))} | "
            f"{r.get('contract_years_left', '?')} | {ctype or acq or ''} | {' '.join(flags)} |"
        )
    return "\n".join(lines)


def _dead_cap_lines(state: Mapping[str, Any], league_team_id: str) -> list[str]:
    out = []
    for row in state_cap_adjustments(state):
        if _text(row.get("league_team_id")) != league_team_id:
            continue
        kind = row.get("adjustment_type")
        amt = _money(row.get("amount"))
        if kind == "dropped_player_charge":
            out.append(f"dead cap {_fmt_money(amt)} for {_text(row.get('player_name')) or 'player'} ({row.get('season')})")
        elif kind == "trade_retained_salary":
            out.append(f"retained salary {_fmt_money(amt)} for {_text(row.get('player_name'))} ({row.get('season')})")
        elif kind == "trade_carryover":
            cp = _text(row.get("counterparty_owner"))
            if amt > 0:
                out.append(f"{_fmt_money(amt)} cap traded to {cp or 'another team'}")
            elif amt < 0:
                out.append(f"{_fmt_money(-amt)} cap received from {cp or 'another team'}")
        else:
            label = (kind or "adjustment").replace("_", " ")
            who = _text(row.get("player_name"))
            sign = "+" if amt > 0 else "-"
            out.append(f"{label} {sign}{_fmt_money(abs(amt))}" + (f" ({who})" if who else ""))
    return out


def _picks_by_team(state: Mapping[str, Any]) -> dict[str, list[str]]:
    picks: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for row in state.get("draft_picks", []):
        owner = _text(row.get("current_owner_league_team_id"))
        original = _text(row.get("original_team_name") or row.get("original_owner_name"))
        current_orig_id = _text(row.get("original_league_team_id"))
        try:
            year = int(row.get("draft_year") or row.get("season") or 0)
            rnd = int(row.get("round_number") or row.get("round") or 0)
        except Exception:
            continue
        status = _text(row.get("asset_status")).lower()
        if status in {"consumed", "historical", "voided", "cancelled"}:
            continue  # already used or retired; not a live asset
        via = "" if current_orig_id == owner else f" (via {original})"
        picks[owner].append((year, rnd, f"{year} R{rnd}{via}"))
    return {tid: [label for _, _, label in sorted(items)] for tid, items in picks.items()}


def _activity_lines(state: Mapping[str, Any], limit: int) -> list[str]:
    out = []
    for row in state.get("activity", [])[:limit]:
        when = _text(row.get("effective_at") or row.get("created_at"))[:10]
        action = "signed" if row.get("action") == "add" else "released"
        out.append(f"{when} {_text(row.get('owner_name'))} {action} {_text(row.get('player_name'))}")
    return out


def build_league_pack(inputs: PackInputs) -> str:
    state = inputs.state
    rules = inputs.league_rules or {}
    ai = inputs.ai_settings
    salary_cap = rules.get("salary_cap", 225)
    summaries = team_summaries(state, salary_cap)
    roster = state_roster(state)
    standings_idx = _standings_index(inputs.standings)
    picks = _picks_by_team(state)
    asker = inputs.asker

    parts: list[str] = []
    parts.append(f"# {inputs.league_name} — {inputs.season} season league state")
    parts.append(
        f"You are talking to **{asker.owner_name}**"
        + (f" (team: {asker.team_name})" if asker.team_name and asker.team_name != asker.owner_name else "")
        + f", league role: {asker.role}. \"My team\" means this team."
    )

    parts.append("## League rules and settings\n" + _rules_section(rules, ai))

    # Cap table
    lines = [
        "## Cap and standings by team (sorted by standing)",
        "Cap used = active cap hits + dead cap + other adjustments; cap space = salary cap - cap used. "
        "These figures are the app's own and are authoritative; do not re-derive them. "
        "Roster cap hits below are face value; IR and taxi discounts, retained salary and traded cap appear under each team's cap adjustments.",
        "| Team (owner) | Record | Cap used | Cap space | Active cap hits | Dead cap | Other adj | Roster | IR | Taxi |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def _rank(s: TeamSummary) -> float:
        row = standings_idx.get(s.owner_name.casefold()) or standings_idx.get(s.team_name.casefold())
        return float(row.get("rank", 99)) if row else 99.0

    for s in sorted(summaries, key=_rank):
        row = standings_idx.get(s.owner_name.casefold()) or standings_idx.get(s.team_name.casefold())
        label = s.owner_name if s.team_name == s.owner_name else f"{s.team_name} ({s.owner_name})"
        if s.league_team_id == asker.league_team_id:
            label += " ← you"
        lines.append(
            f"| {label} | {_standing_line(row)} | {_fmt_money(s.cap_used)} | {_fmt_money(s.cap_space)} | "
            f"{_fmt_money(s.active_salary)} | {_fmt_money(s.dead_cap)} | {_fmt_money(s.adjustments - s.dead_cap)} | "
            f"{s.roster_count} | {s.ir_count} | {s.taxi_count} |"
        )
    parts.append("\n".join(lines))

    # Rosters
    parts.append("## Rosters and contracts (cap hit is this season's charge after retained salary)")
    for s in sorted(summaries, key=lambda x: (x.league_team_id != asker.league_team_id, x.owner_name)):
        rows = [r for r in roster if _text(r.get("league_team_id")) == s.league_team_id]
        header = f"### {s.owner_name}" + (f" — {s.team_name}" if s.team_name != s.owner_name else "")
        if s.league_team_id == asker.league_team_id:
            header += " (the asker's team)"
        block = [header, _roster_table(rows) if rows else "_no active contracts_"]
        dead = _dead_cap_lines(state, s.league_team_id)
        if dead:
            block.append("Cap adjustments: " + "; ".join(dead))
        team_picks = picks.get(s.league_team_id) or []
        block.append("Draft picks owned: " + (", ".join(team_picks) if team_picks else "none on record"))
        parts.append("\n".join(block))

    activity = _activity_lines(state, inputs.activity_limit)
    if activity:
        parts.append("## Recent contract activity (newest first)\n" + "\n".join(f"- {a}" for a in activity))

    if inputs.past_seasons:
        parts.append("## Past seasons (regular season; use get_matchup_history for detail)\n" + "\n".join(f"- {l}" for l in inputs.past_seasons))

    if inputs.private_memory:
        parts.append("## What you remember about this owner (private)\n" + "\n".join(f"- {m}" for m in inputs.private_memory))
    if inputs.league_notebook:
        parts.append("## League notebook (shared observations)\n" + "\n".join(f"- {m}" for m in inputs.league_notebook))
    if inputs.learning:
        parts.append("## How your calls have graded (your own playbook, learned from real results in this league)\n" + "\n".join(f"- {m}" for m in inputs.learning))

    if inputs.data_freshness:
        parts.append("## Data freshness\n" + "\n".join(f"- {k}: {v}" for k, v in inputs.data_freshness.items()))

    return "\n\n".join(parts)
