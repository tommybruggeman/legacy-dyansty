"""The alert job: every 10 minutes it works out who should hear about what, queues it (api.alerts.enqueue), then sends
everything due straight to Apple (api.alerts.deliver).

Producers, each safe to run again and again (every alert has a key, so it is queued once per owner):
  game day   lineup problems (IR/suspended/bye starter, empty slot) from 24 hours before the game, a starter ruled out
             as soon as we see it, and "inactive" 30 minutes before kickoff
  results    each owner's score once the week's last NFL game is over (playoff rounds when the round ends)
  waivers    one line to everyone after a waiver run
  deadlines  taxi pick after the rookie draft and 24 hours before the season, team option 7 days and 24 hours before
             the rollover deadline, trade deadline 7 days and 12 hours out
  cap        every morning while a team is over the cap
  playoffs   clinched a spot; the bracket is set
  offers     trade offers in the app (sent to you, answered)
  rollover   the day the rollover completes
Completed-trade alerts come from scripts/notify_trades.py (after each Sleeper sync) through the same outbox.

Only leagues with at least one phone signed up are worked. The first run in a league records what is already there
without sending (no burst of old news). Prints counts only (the repo and its Actions logs are public).

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, APNS_KEY_P8, APNS_KEY_ID (api/apns.py). Without the APNs key it
still queues, and sends on the next run that has it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import alerts, apns, home, league, lineups, playoffs  # noqa: E402

SEEDED = "alerts-seeded"
OUT = {"out"}
OFF_ROSTER = {"ir": "on IR", "sus": "suspended", "pup": "on PUP", "na": "not active"}
NOT_HEALTHY = OUT | set(OFF_ROSTER) | {"doubtful"}
INACTIVE_LEAD = timedelta(minutes=35)
LINEUP_LEAD = timedelta(hours=24)
GAME_LENGTH = timedelta(hours=4)
RECENT = timedelta(days=3)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class League:
    """One league's shared lookups for this run."""

    def __init__(self, client: Any, league_id: str, now: datetime, seed: bool):
        from season_engine import SeasonResolver

        self.client, self.id, self.now, self.seed = client, league_id, now, seed
        season = SeasonResolver(client).get_active_season(league_id)
        self.season = int(season.season)
        self.sid = season.sleeper_league_id
        self.sleeper_league = (league.sleeper.get(f"league/{self.sid}") or {}) if self.sid else {}
        self.name = _text(self.sleeper_league.get("name")) or "Legacy Dynasty"
        self.week = league.current_week()
        teams = client.table("league_teams").select("id,sleeper_roster_id,owner_name,team_name").eq("league_id", league_id).execute().data or []
        self.roster_of = {str(t["id"]): _int(t.get("sleeper_roster_id")) for t in teams}
        self.team_of_roster = {r: t for t, r in self.roster_of.items() if r is not None}
        self.names = league.roster_names(client, league_id, self.sid) if self.sid else {}
        members = client.table("league_memberships").select("user_id,league_team_id").eq("league_id", league_id).execute().data or []
        self.members = [m for m in members if m.get("user_id")]
        from league_ai.settings_store import load_settings
        self.settings = load_settings(client, league_id)
        self.rules = playoffs.rules_of(self.settings)
        self.queued = 0

    # who hears it
    def everyone(self) -> list[str]:
        return sorted({str(m["user_id"]) for m in self.members})

    def team_users(self, team_id: str | None) -> list[str]:
        return sorted({str(m["user_id"]) for m in self.members if team_id and str(m.get("league_team_id")) == str(team_id)})

    def roster_users(self, roster_id: int | None) -> list[str]:
        return self.team_users(self.team_of_roster.get(roster_id)) if roster_id is not None else []

    def send(self, alert: alerts.Alert, users: Iterable[str], *, not_before: datetime | None = None, expires_at: datetime | None = None) -> None:
        self.queued += alerts.enqueue(self.client, alert, league_id=self.id, user_ids=users, not_before=not_before,
                                      expires_at=expires_at, seed=self.seed)

    def in_season(self) -> bool:
        return _text(self.sleeper_league.get("status")) == "in_season"

    def games(self, week: int) -> list[dict[str, Any]]:
        return (self.client.table("nfl_games").select("gameday,gametime,home_team,away_team")
                .eq("season", self.season).eq("week", week).execute().data or [])

    def kickoffs(self, week: int) -> dict[str, datetime]:
        return home.kickoffs(self.games(week))


# ---- game day ---------------------------------------------------------------------------------------

def main_kickoff(starts: Mapping[str, datetime]) -> datetime | None:
    """Sunday's first kickoff (what a bye or empty slot has to be fixed by); the week's first game if no Sunday."""
    times = sorted(set(starts.values()))
    sunday = [t for t in times if t.astimezone(home.EASTERN).weekday() == 6]
    return (sunday or times or [None])[0]


def fresh_statuses(ids: Iterable[str]) -> dict[str, str]:
    """Injury statuses right now for these players (our nfl_players table refreshes nightly; inactives come out
    90 minutes before kickoff). Raw player news is a feed like stats, read through this one function."""
    try:
        players = league.sleeper.get("players/nfl", ttl=3000) or {}
    except Exception:
        return {}
    return {pid: _text((players.get(pid) or {}).get("injury_status")) for pid in ids}


def lineup_issues(*, week: int, slots: list[str], starters: list[str | None], info: Mapping[str, Mapping[str, Any]],
                  starts: Mapping[str, datetime], now: datetime, bench: list[dict[str, Any]], status: Mapping[str, str],
                  tz: str | None = None) -> list[tuple[alerts.Alert, datetime, datetime]]:
    """(alert, send from, stale at) for every starter problem in one lineup that hasn't kicked off yet."""
    out = []
    sunday = main_kickoff(starts)
    for i, pid in enumerate(starters):
        slot = slots[i] if i < len(slots) else ""
        meta = info.get(pid or "", {})
        name = _text(meta.get("full_name")) or (pid or "")
        team = _text(meta.get("team")) or ("" if (pid or "").isdigit() else (pid or ""))
        kick = starts.get(team)
        sub = alerts.best_sub(slot, bench, lineups.fits)
        if not pid or pid == "0":
            if sunday and now < sunday:
                out.append((alerts.lineup_problem(week=week, day=alerts.day_name(sunday, tz), slot=slot, problem="empty",
                                                  name=None, player_id=None, sub=sub), sunday - LINEUP_LEAD, sunday))
            continue
        if kick is None:
            if sunday and now < sunday:
                out.append((alerts.lineup_problem(week=week, day=alerts.day_name(sunday, tz), slot=slot, problem="bye",
                                                  name=name, player_id=pid, sub=sub), sunday - LINEUP_LEAD, sunday))
            continue
        if now >= kick:
            continue
        tag = (status.get(pid) or _text(meta.get("injury_status"))).lower()
        if tag in OFF_ROSTER:
            out.append((alerts.lineup_problem(week=week, day=alerts.day_name(kick, tz), slot=slot, problem=OFF_ROSTER[tag],
                                              name=name, player_id=pid, sub=sub), kick - LINEUP_LEAD, kick))
        elif tag in OUT:
            if kick - now <= INACTIVE_LEAD:
                out.append((alerts.inactive(week=week, name=name, player_id=pid, sub=sub), now, kick))
            else:
                out.append((alerts.ruled_out(week=week, day=alerts.day_name(kick, tz), name=name, player_id=pid,
                                             body_part=_text(meta.get("injury_body_part")) or None, sub=sub), now, kick))
    return out


def game_day(lg: League) -> None:
    if not lg.sid or not lg.in_season():
        return
    from api import projections, teams
    week, now = lg.week, lg.now
    starts = lg.kickoffs(week)
    if not starts or now >= max(starts.values()):
        return
    rows = league.sleeper.get(f"league/{lg.sid}/matchups/{week}", ttl=60) or []
    rosters = {r.get("roster_id"): r for r in league.sleeper.get(f"league/{lg.sid}/rosters", ttl=300) or []}
    slots = lineups.starter_slots(lg.settings.roster_positions or lg.sleeper_league.get("roster_positions") or [])
    saved_rows = (lg.client.table("league_lineups").select("league_team_id,week,slots").eq("league_id", lg.id)
                  .eq("season", lg.season).lte("week", week).order("week", desc=True).execute().data or [])
    every = {p for r in rosters.values() for p in (r.get("players") or [])}
    info = teams.player_info(lg.client, every)
    soon = [t for t in starts.values() if timedelta(0) < t - now <= INACTIVE_LEAD + timedelta(minutes=10)]
    status = fresh_statuses([p for r in rows for p in (r.get("starters") or []) if p and p != "0"]) if soon else {}
    lineup_rows = {r.get("roster_id"): r for r in rows}
    bench_ids = set()
    plans = []
    for rid, roster in rosters.items():
        hold = set(roster.get("taxi") or []) | set(roster.get("reserve") or [])
        active = {p for p in roster.get("players") or [] if p not in hold}
        team_id = lg.team_of_roster.get(rid)
        saved = [r for r in saved_rows if str(r.get("league_team_id")) == str(team_id)][:1]
        sleeper_starters = list((lineup_rows.get(rid) or {}).get("starters") or [])
        ids = lineups.effective(slots, saved, sleeper_starters, active) if slots else sleeper_starters
        bench = [p for p in active if p not in set(ids)]
        bench_ids |= set(bench)
        plans.append((rid, ids, bench))
    proj = projections.week_projections(lg.client, lg.sid, lg.season, week, sorted(bench_ids),
                                        {p: _text(info.get(p, {}).get("position")) for p in bench_ids},
                                        {p: _text(info.get(p, {}).get("team")) for p in bench_ids})
    for rid, ids, bench_list in plans:
        users = lg.roster_users(rid)
        if not users:
            continue
        bench = []
        for p in bench_list:
            meta = info.get(p, {})
            kick = starts.get(_text(meta.get("team")))
            if kick is None or now >= kick or (status.get(p) or _text(meta.get("injury_status"))).lower() in NOT_HEALTHY:
                continue
            bench.append({"player_id": p, "name": _text(meta.get("full_name")) or p, "pos": _text(meta.get("position")), "proj": proj.get(p, 0)})
        for alert, start, stale in lineup_issues(week=week, slots=slots, starters=ids, info=info, starts=starts, now=now,
                                                 bench=bench, status=status, tz=alerts.LEAGUE_TZ.key):
            if now >= start:
                lg.send(alert, users, expires_at=stale)


# ---- results, playoffs ------------------------------------------------------------------------------

def week_over(starts: Mapping[str, datetime], now: datetime) -> bool:
    return bool(starts) and now >= max(starts.values()) + GAME_LENGTH


def results(lg: League) -> None:
    if not lg.sid:
        return
    start = int(lg.rules["playoff_week_start"])
    last = playoffs.last_week(lg.rules)
    for week in sorted({max(lg.week - 1, 1), lg.week}):
        if week > last or not week_over(lg.kickoffs(week), lg.now):
            continue
        raw = league.sleeper.get(f"league/{lg.sid}/matchups/{week}", ttl=300) or []
        expires = lg.now + timedelta(days=2)
        if week < start:
            games = league.matchups(lg.sid, lg.names, week, None)["games"]
            everyone = sorted((t for g in games for t in g["teams"]), key=lambda t: t["rank"])
            cut = everyone[4]["score"] if len(everyone) >= 5 else None
            for g in games:
                if len(g["teams"]) < 2:
                    continue
                for me, them in (g["teams"], g["teams"][::-1]):
                    alert = alerts.final_result(season=lg.season, week=week, opp=them["name"], me=me["score"], them=them["score"],
                                                top5=bool(me.get("top5")), rank=me.get("rank"), cut=cut,
                                                game_url=f"/game/{week}/{g['matchup_id']}")
                    lg.send(alert, lg.roster_users(me["roster_id"]), expires_at=expires)
            continue
        view = playoffs.build(week=week, rules=lg.rules, standings=league.standings(lg.sid, lg.names, None)["rows"], names=lg.names,
                              rows_for=lambda w: raw if w == week else (league.sleeper.get(f"league/{lg.sid}/matchups/{w}", ttl=3600) or []),
                              last_scored=week, current_week=week, my_roster_id=None)
        if not view:
            continue
        for game in view["tree"]:
            if game["state"] != "final" or max(game["weeks"]) != week:
                continue
            t1, t2 = game["teams"]
            if not t1.get("roster_id") or not t2.get("roster_id"):
                continue
            next_round = playoffs.round_name(game["round"] + 1, view["rounds"], None) if game["round"] < view["rounds"] else None
            for me, them in ((t1, t2), (t2, t1)):
                won = game["winner"] == me["roster_id"]
                alert = alerts.playoff_result(season=lg.season, week=week, opp=them["name"], me=me["score"], them=them["score"],
                                              won=won, next_round=next_round, champion=won and game.get("place") == 1,
                                              game_url=f"/game/{week}/{game['matchup_id']}" if game.get("matchup_id") else "/league")
                lg.send(alert, lg.roster_users(me["roster_id"]), expires_at=expires)


SHOTGUN_AT = 17  # Monday, 5 PM Eastern


def shotgun_window(starts: Mapping[str, datetime], now: datetime) -> datetime | None:
    """Monday's first kickoff when it's Monday from 5 PM ET until then; None otherwise (or no Monday game)."""
    local = now.astimezone(home.EASTERN)
    monday = sorted(t for t in starts.values() if t.astimezone(home.EASTERN).date() == local.date() and local.weekday() == 0)
    if not monday or local.hour < SHOTGUN_AT or now >= monday[0]:
        return None
    return monday[0]


def shotgun(lg: League) -> None:
    """Monday's Shotgun watch, only to owners who can still finish last (home.still_alive)."""
    if not lg.sid or not lg.in_season() or lg.week >= int(lg.rules["playoff_week_start"]):
        return
    starts = lg.kickoffs(lg.week)
    kick = shotgun_window(starts, lg.now)
    if kick is None:
        return
    from api import projections, teams
    raw = league.sleeper.get(f"league/{lg.sid}/matchups/{lg.week}", ttl=60) or []
    ids = [p for r in raw for p in (r.get("starters") or []) if p and p != "0"]
    info = teams.player_info(lg.client, ids)
    proj = projections.week_projections(lg.client, lg.sid, lg.season, lg.week, ids,
                                        {p: _text(info.get(p, {}).get("position")) for p in ids},
                                        {p: _text(info.get(p, {}).get("team")) for p in ids})
    live = home.team_live(raw, info, starts, lg.now, proj)
    for rid, me_last, other, gap in alerts.shotgun_targets(live, home.still_alive(live)):
        alert = alerts.shotgun_watch(season=lg.season, week=lg.week, me_last=me_last, gap=gap,
                                     other=lg.names.get(other) or f"Roster {other}",
                                     my_left=live[rid]["left"], other_left=live[other]["left"])
        lg.send(alert, lg.roster_users(rid), expires_at=kick)


def playoff_spots(lg: League) -> None:
    if not lg.sid or not lg.in_season():
        return
    start = int(lg.rules["playoff_week_start"])
    spots = int(lg.rules["playoff_teams"])
    table = league.standings(lg.sid, lg.names, None)
    played, rows = int(table["weeks_played"]), table["rows"]
    if not rows:
        return
    reg_end = start - 1
    per_week = float(lg.settings.win_points + lg.settings.top_scorer_bonus_points)
    if played < reg_end:
        points = {int(r["roster_id"]): float(r["standing_points"]) for r in rows}
        for rid in alerts.clinched_teams(points, reg_end - played, spots, per_week):
            lg.send(alerts.clinched(season=lg.season), lg.roster_users(rid))
        return
    if lg.week > start:
        return
    view = playoffs.build(week=start, rules=lg.rules, standings=rows, names=lg.names, rows_for=lambda w: [],
                          last_scored=reg_end, current_week=reg_end, my_roster_id=None)
    first = [g for g in (view or {}).get("tree", []) if g["round"] == 1]
    for seed, r in enumerate(rows[:spots], start=1):
        rid = int(r["roster_id"])
        opp = None
        for g in first:
            ids = [t.get("roster_id") for t in g["teams"]]
            if rid in ids:
                opp = next((t.get("name") for t in g["teams"] if t.get("roster_id") and t.get("roster_id") != rid), None)
        lg.send(alerts.bracket_set(season=lg.season, seed=seed, opp=opp, week=start), lg.roster_users(rid))


# ---- waivers ------------------------------------------------------------------------------------------

def app_transactions_on(lg: League) -> bool:
    rows = lg.client.table("league_app_transactions").select("enabled").eq("league_id", lg.id).limit(1).execute().data or []
    return bool(rows and rows[0].get("enabled"))


def waiver_runs(transactions: Iterable[Mapping[str, Any]], now: datetime, window: timedelta = timedelta(hours=12)) -> dict[str, int]:
    """Sleeper waiver claims that went through in the last `window`, counted per run (by Eastern date)."""
    runs: dict[str, int] = {}
    for t in transactions:
        if t.get("type") != "waiver" or t.get("status") != "complete":
            continue
        try:
            at = datetime.fromtimestamp(int(t.get("status_updated") or 0) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        if now - at <= window:
            key = at.astimezone(home.EASTERN).date().isoformat()
            runs[key] = runs.get(key, 0) + 1
    return runs


def waivers(lg: League) -> None:
    since = lg.now - timedelta(hours=12)
    if app_transactions_on(lg):
        rows = (lg.client.table("app_waiver_claims").select("status,process_at").eq("league_id", lg.id)
                .eq("status", "won").gte("decided_at", since.isoformat()).execute().data or [])
        runs: dict[str, int] = {}
        for r in rows:
            at = alerts.parse_ts(r.get("process_at"))
            if at:
                key = at.astimezone(home.EASTERN).date().isoformat()
                runs[key] = runs.get(key, 0) + 1
    elif lg.sid:
        tx = []
        for w in sorted({max(lg.week - 1, 1), lg.week}):
            tx += league.sleeper.get(f"league/{lg.sid}/transactions/{w}", ttl=300) or []
        runs = waiver_runs(tx, lg.now)
    else:
        runs = {}
    for key, moves in runs.items():
        lg.send(alerts.waivers_done(run_key=key, moves=moves, league_name=lg.name), lg.everyone(), expires_at=lg.now + timedelta(days=1))


# ---- deadlines, cap, rollover --------------------------------------------------------------------------

def taxi_teams(lg: League, draft_season: int) -> set[str]:
    """Teams that drafted a rookie in this draft and haven't put one on taxi."""
    rookies = (lg.client.table("contract_agreements").select("league_team_id,player_id").eq("league_id", lg.id)
               .eq("contract_type", "rookie").eq("signed_season", draft_season).in_("status", ["active", "scheduled"])
               .execute().data or [])
    if not rookies:
        return set()
    taxi = {r["player_id"] for r in lg.client.table("rookie_taxi_assignments").select("player_id").eq("league_id", lg.id)
            .in_("player_id", sorted({r["player_id"] for r in rookies})).execute().data or []}
    done = {str(r["league_team_id"]) for r in rookies if r["player_id"] in taxi}
    return {str(r["league_team_id"]) for r in rookies} - done


def deadlines(lg: League) -> None:
    now = lg.now
    # Taxi: the morning after the rookie draft, and 24 hours before the season's first game.
    drafts = (lg.client.table("league_draft_lifecycles").select("season,status,completed_at").eq("league_id", lg.id)
              .eq("status", "completed").execute().data or [])
    for d in drafts:
        done_at = alerts.parse_ts(d.get("completed_at"))
        if done_at and now - done_at <= RECENT:
            for team in taxi_teams(lg, int(d["season"])):
                lg.send(alerts.taxi_after_draft(season=int(d["season"])), lg.team_users(team),
                        not_before=alerts.next_morning(done_at), expires_at=done_at + RECENT)
    week1 = lg.kickoffs(1)
    if week1:
        kick = min(week1.values())
        if alerts.due_window(now, kick, timedelta(hours=24), kick):
            draft = next((d for d in drafts if int(d["season"]) == lg.season), None)
            if draft:
                for team in taxi_teams(lg, lg.season):
                    lg.send(alerts.taxi_last_day(season=lg.season, kickoff=kick, tz=alerts.LEAGUE_TZ.key), lg.team_users(team), expires_at=kick)

    # Team options: 7 days and 24 hours before the owners' deadline, to owners with calls still open.
    runs = (lg.client.table("rollover_executions").select("id,target_season,status,owner_deadline").eq("league_id", lg.id)
            .not_.in_("status", ["completed", "cancelled", "failed_precommit", "failed_postcommit_validation"]).execute().data or [])
    for run in runs:
        deadline = alerts.parse_ts(run.get("owner_deadline"))
        if not deadline or now >= deadline:
            continue
        left = "24h" if deadline - now <= timedelta(hours=24) else "7d" if deadline - now <= timedelta(days=7) else None
        if not left:
            continue
        open_calls = (lg.client.table("rollover_owner_decisions").select("league_team_id,player_id")
                      .eq("rollover_execution_id", run["id"]).eq("decision_status", "waiting_for_owner").execute().data or [])
        if not open_calls:
            continue
        from api import teams
        info = teams.player_info(lg.client, [c["player_id"] for c in open_calls])
        by_team: dict[str, list[str]] = {}
        for c in open_calls:
            by_team.setdefault(str(c["league_team_id"]), []).append(_text(info.get(c["player_id"], {}).get("full_name")) or "A rookie")
        for team, names in by_team.items():
            lg.send(alerts.option_reminder(season=int(run["target_season"]), left=left, names=sorted(names)), lg.team_users(team), expires_at=deadline)

    # Trade deadline from Settings.
    if lg.in_season():
        def week_kickoff(n: int) -> datetime | None:
            starts = lg.kickoffs(n)
            return min(starts.values()) if starts else None
        deadline = alerts.parse_deadline(lg.settings.trade_deadline, week_kickoff)
        if deadline and now < deadline:
            if deadline - now <= timedelta(hours=12):
                lg.send(alerts.trade_deadline(season=lg.season, left="12h", deadline=deadline, tz=alerts.LEAGUE_TZ.key), lg.everyone(), expires_at=deadline)
            elif deadline - now <= timedelta(days=7):
                lg.send(alerts.trade_deadline(season=lg.season, left="7d", deadline=deadline, tz=alerts.LEAGUE_TZ.key), lg.everyone(),
                        expires_at=deadline - timedelta(hours=12))


def team_state(client: Any, league_id: str, season: int) -> dict[str, Any]:
    """The canonical team state through the jobs RPC (service key), as the app sees it."""
    from services.team_roster_state import load_team_state

    class Jobs:
        def rpc(self, name: str, params: Mapping[str, Any]) -> Any:
            req = params["p_request"]
            return client.rpc("read_canonical_team_state_for_jobs", {"p_league_id": req["league_id"], "p_season": int(req["season"])})

    return load_team_state(Jobs(), league_id, season)


def over_the_cap(lg: League) -> None:
    from api import teams
    from services.team_roster_state import calculate_team_financials, state_cap_adjustments
    today = lg.now.astimezone(alerts.LEAGUE_TZ).date()
    cap = teams.salary_cap(lg.client, lg.id)
    state = team_state(lg.client, lg.id, lg.season)
    adjustments = state_cap_adjustments({"dead_cap": state.get("dead_cap", []), "cap_adjustments": state.get("cap_adjustments", [])})
    end_of_day = datetime.combine(today + timedelta(days=1), datetime.min.time(), alerts.LEAGUE_TZ).astimezone(timezone.utc)
    for team_id in lg.roster_of:
        money = calculate_team_financials(state.get("roster", []), adjustments, salary_cap=cap, league_team_id=team_id)
        used = float(money["cap_used"])
        if used <= cap + 0.004:
            continue
        users = lg.team_users(team_id)
        if not users:
            continue
        before = (lg.client.table("push_outbox").select("key").in_("user_id", users).like("key", f"cap:{lg.season}:%")
                  .execute().data or [])
        days = []
        for r in before:
            try:
                days.append(datetime.fromisoformat(r["key"].rsplit(":", 1)[1]).date())
            except ValueError:
                pass
        alert = alerts.over_cap(season=lg.season, day=today, used=used, cap=cap, day_number=alerts.cap_streak(today, days))
        lg.send(alert, users, not_before=alerts.next_morning(datetime.combine(today, datetime.min.time(), alerts.LEAGUE_TZ)),
                expires_at=end_of_day)


def rollover(lg: League) -> None:
    rows = (lg.client.table("rollover_executions").select("target_season,status,completed_at").eq("league_id", lg.id)
            .eq("status", "completed").execute().data or [])
    for r in rows:
        done = alerts.parse_ts(r.get("completed_at"))
        if done and lg.now - done <= RECENT:
            lg.send(alerts.rollover_done(season=int(r["target_season"])), lg.everyone(), expires_at=done + RECENT)


def trade_offers(lg: League) -> None:
    since = (lg.now - RECENT).isoformat()
    rows = (lg.client.table("trade_offers").select("id,status,from_league_team_id,to_league_team_id,created_at,decided_at")
            .eq("league_id", lg.id).gte("created_at", since).execute().data or [])
    def name(team: str) -> str:
        return lg.names.get(lg.roster_of.get(str(team)), "Another owner")
    for o in rows:
        if o["status"] == "pending":
            lg.send(alerts.trade_offer_received(offer_id=o["id"], sender=name(o["from_league_team_id"])), lg.team_users(o["to_league_team_id"]))
        elif o["status"] in ("accepted", "declined"):
            lg.send(alerts.trade_offer_answered(offer_id=o["id"], partner=name(o["to_league_team_id"]), accepted=o["status"] == "accepted"),
                    lg.team_users(o["from_league_team_id"]))


PRODUCERS: list[tuple[str, Callable[[League], None]]] = [
    ("game day", game_day), ("results", results), ("playoffs", playoff_spots), ("waivers", waivers),
    ("deadlines", deadlines), ("cap", over_the_cap), ("rollover", rollover), ("offers", trade_offers),
    ("shotgun", shotgun),
]


def run(client: Any, now: datetime) -> None:
    leagues = sorted({r["league_id"] for r in client.table("push_tokens").select("league_id,kind").eq("kind", "apns").execute().data or []})
    for n, league_id in enumerate(leagues, start=1):
        marks = {r["key"] for r in client.table("push_sent").select("key").eq("league_id", league_id).eq("key", SEEDED).execute().data or []}
        seed = SEEDED not in marks
        try:
            lg = League(client, league_id, now, seed)
        except Exception as exc:
            print(f"league {n}: skipped ({type(exc).__name__})", flush=True)
            continue
        for label, producer in PRODUCERS:
            try:
                producer(lg)
            except Exception as exc:
                print(f"league {n}: {label} failed ({type(exc).__name__})", flush=True)
        if seed:
            client.table("push_sent").upsert({"league_id": league_id, "key": SEEDED}, on_conflict="league_id,key").execute()
        print(f"league {n}: {lg.queued} alert rows {'recorded (first run)' if seed else 'queued'}", flush=True)


def main() -> int:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    try:
        client.table("push_outbox").select("id").limit(1).execute()
    except Exception:
        print("push alert tables not set up yet (sql/push-alerts.sql); skipping", flush=True)
        return 0
    now = datetime.now(timezone.utc)
    run(client, now)
    if not apns.configured():
        print("APNs key not set (APNS_KEY_P8 / APNS_KEY_ID): queued only", flush=True)
        return 0
    push = apns.Client.from_env()
    try:
        print(f"delivery: {alerts.deliver(client, push, now)}", flush=True)
    finally:
        push.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
