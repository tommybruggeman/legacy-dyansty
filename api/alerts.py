"""Push alerts to owners' phones: the wording of every alert, the owner's on/off switches, quiet hours, the weekly
limit, and delivery straight to Apple (api.apns). No paid or third-party push service.

How it flows:
  1. A producer (scripts/push_alerts.py, every 10 minutes) decides an owner should hear about something and calls
     enqueue(): one row in push_outbox per owner, keyed so the same alert is never queued twice.
  2. deliver() sends what is due: it skips switched-off kinds, holds alerts during the owner's quiet hours
     (11 PM to 8 AM in their time zone) until morning, drops alerts that went stale (a lineup alert after kickoff),
     and stops at WEEKLY_LIMIT alerts per owner per 7 days (over-the-cap and urgent alerts don't count).

The wording below is what Tommy approved on 2026-11-04 (mockups in the project files: mobile/ios-app/push-alerts/).
Everything here prints counts only (the repo and its Actions logs are public).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LEAGUE_TZ = ZoneInfo("America/Denver")
QUIET_START, QUIET_END = time(23, 0), time(8, 0)
WEEKLY_LIMIT = 12
MORNING = time(9, 0)

# Every kind an owner can switch off (app Settings > Alerts and the website's Settings > Alerts), in display order.
KINDS: dict[str, str] = {
    "lineup": "Lineup problems",
    "injury": "Injuries",
    "results": "Final scores",
    "stat_fix": "Stat corrections",
    "waivers": "Waivers processed",
    "deadlines": "Deadlines",
    "cap": "Over the cap",
    "trade_offers": "Trade offers",
    "trades": "Trades completed",
    "playoffs": "Playoffs",
    "rollover": "Rollover",
}
KIND_NOTES: dict[str, str] = {
    "lineup": "A starter is out for the season, on bye, or a slot is empty, before kickoff.",
    "injury": "A starter is ruled out, or inactive 30 minutes before kickoff.",
    "results": "Your matchup result once the week's last game ends.",
    "stat_fix": "A stat correction changed your score.",
    "waivers": "Wednesday and Sunday mornings, after waivers run.",
    "deadlines": "Taxi, team option and trade deadline reminders.",
    "cap": "Every morning while you're over the cap.",
    "trade_offers": "Offers sent to you, and answers to yours.",
    "trades": "A trade was completed in the league.",
    "playoffs": "You clinched a spot, and when the bracket is set.",
    "rollover": "The day a new season starts.",
}
NOT_LIMITED = {"cap"}  # never counted toward (or stopped by) the weekly limit


# ---- time helpers -----------------------------------------------------------------------------------

def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else LEAGUE_TZ
    except (ZoneInfoNotFoundError, ValueError):
        return LEAGUE_TZ


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def quiet(now: datetime, tz: str | None) -> bool:
    """11 PM to 8 AM where the owner is."""
    local = now.astimezone(zone(tz)).time()
    return local >= QUIET_START or local < QUIET_END


def next_morning(after: datetime, tz: str | None = None, at: time = MORNING) -> datetime:
    """The next `at` (9 AM) where the owner is, on or after `after`."""
    tzinfo = zone(tz)
    local = after.astimezone(tzinfo)
    target = datetime.combine(local.date(), at, tzinfo)
    if target < local:
        target = datetime.combine(local.date() + timedelta(days=1), at, tzinfo)
    return target.astimezone(timezone.utc)


def day_name(when: datetime, tz: str | None = None) -> str:
    return when.astimezone(zone(tz)).strftime("%A")


def clock(when: datetime, tz: str | None = None) -> str:
    """'8:20 PM' in the owner's time zone."""
    local = when.astimezone(zone(tz))
    return local.strftime("%I:%M %p").lstrip("0")


def short_date(when: datetime, tz: str | None = None) -> str:
    """'Tue, Nov 24'."""
    local = when.astimezone(zone(tz))
    return f"{local.strftime('%a, %b')} {local.day}"


# ---- the wording ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    kind: str
    key: str
    title: str
    body: str
    url: str
    urgent: bool = False


SLOT_WORDS = {"FLEX": "FLEX", "SUPER_FLEX": "Superflex", "REC_FLEX": "WR/TE flex", "WRRB_FLEX": "RB/WR flex", "DEF": "DEF"}
LINEUP_URL = "/my-team"


def slot_word(slot: str) -> str:
    return SLOT_WORDS.get(slot, slot)


def _sub(sub: Mapping[str, Any] | None) -> str:
    if not sub:
        return ""
    proj = sub.get("proj")
    return f" Look at starting {sub['name']}" + (f" ({float(proj):.1f} proj)." if proj else ".")


def lineup_problem(*, week: int, day: str, slot: str, problem: str, name: str | None, player_id: str | None,
                   sub: Mapping[str, Any] | None) -> Alert:
    """problem: 'bye', 'empty', or a status word ('IR', 'suspended', ...)."""
    where = f"your {slot_word(slot)} spot"
    if problem == "empty":
        first = f"{where[0].upper()}{where[1:]} is empty."
    elif problem == "bye":
        first = f"{name} is on bye and still in {where}."
    else:
        first = f"{name} is {problem} and still in {where}."
    return Alert("lineup", f"lineup:{week}:{player_id or 'empty-' + slot}", f"Lineup problem for {day}", first + _sub(sub), LINEUP_URL)


def ruled_out(*, week: int, day: str, name: str, player_id: str, body_part: str | None, sub: Mapping[str, Any] | None) -> Alert:
    part = (body_part or "").strip()
    first = f"{part.capitalize()}, out for {day}." if part else f"Out for {day}."
    return Alert("injury", f"out:{week}:{player_id}", f"{name} ruled out", first + _sub(sub), LINEUP_URL)


def inactive(*, week: int, name: str, player_id: str, sub: Mapping[str, Any] | None) -> Alert:
    return Alert("injury", f"out:{week}:{player_id}", f"{name} is inactive",
                 "Kickoff in 30 minutes and he's in your lineup." + _sub(sub), LINEUP_URL, urgent=True)


TRASH_TALK = (
    "{opp} won with {opp_score}. Let that sink in.",
    "{opp} takes this one. Bounce back next week.",
    "Tough one. {opp} gets the bragging rights this week.",
    "{opp} got you this time. Run it back.",
)


def _pts(x: float) -> str:
    return f"{x:.1f}"


def final_result(*, season: int, week: int, opp: str, me: float, them: float, top5: bool, rank: int | None,
                 cut: float | None, game_url: str) -> Alert:
    """Monday night's result. Title is the score; one short line under it."""
    key = f"final:{season}:{week}"
    if me > them:
        title = f"You beat {opp} {_pts(me)} – {_pts(them)}"
        body = "You got a win and a top 5 performance." if top5 else "You got a win."
    elif me < them:
        title = f"{opp} beat you {_pts(them)} – {_pts(me)}"
        if top5:
            body = "You got a top 5 performance."
        else:
            pick = int(hashlib.sha256(f"{season}:{week}:{opp}".encode()).hexdigest(), 16) % len(TRASH_TALK)
            body = TRASH_TALK[pick].format(opp=opp, opp_score=f"{them:.0f}")
    else:
        title = f"You tied {opp} {_pts(me)} – {_pts(them)}"
        body = "You got a top 5 performance." if top5 else ""
    if not top5 and rank == 6 and cut is not None and cut > me:
        miss = f"You missed the top 5 by {cut - me:.1f} pts."
        body = miss if me < them else f"{body} {miss}".strip()  # on a loss it replaces the trash talk
    return Alert("results", key, title, body, game_url)


def playoff_result(*, season: int, week: int, opp: str, me: float, them: float, won: bool, next_round: str | None,
                   champion: bool, game_url: str) -> Alert:
    """A playoff round is over. Winners hear where they go next; a loss is just the score (Tommy: no 'season over')."""
    key = f"final:{season}:{week}"
    if won:
        title = f"You beat {opp} {_pts(me)} – {_pts(them)}"
        body = "You won the Legacy championship!" if champion else (f"You're moving on to the {next_round.lower()}." if next_round else "")
    else:
        title, body = f"{opp} beat you {_pts(them)} – {_pts(me)}", ""
    return Alert("results", key, title, body, game_url)


def stat_correction(*, season: int, week: int, correction_id: str, result_changed: bool, winner: str, winner_pts: float,
                    loser: str, loser_pts: float, top5_change: str | None = None) -> Alert:
    """top5_change: 'gained', 'lost' or None. Opens the stat correction card in Activity."""
    body = (f"Result changed: {winner} {_pts(winner_pts)} – {loser} {_pts(loser_pts)}." if result_changed
            else "Score updated, result unchanged.")
    if top5_change == "gained":
        body += " You're now a top 5 week."
    elif top5_change == "lost":
        body += " You lost your top 5 week."
    return Alert("stat_fix", f"statfix:{correction_id}", f"Stat correction, Week {week}", body, f"/activity?correction={correction_id}")


def waivers_done(*, run_key: str, moves: int, league_name: str) -> Alert:
    word = "move" if moves == 1 else "moves"
    return Alert("waivers", f"waivers:{run_key}", "Waivers processed", f"{moves} {word} went through in {league_name}.", "/activity")


def taxi_after_draft(*, season: int) -> Alert:
    return Alert("deadlines", f"taxi-draft:{season}", "Rookie draft is done",
                 "Pick your Taxi rookie. His taxi year doesn't use up a contract year.", "/my-team")


def taxi_last_day(*, season: int, kickoff: datetime, tz: str | None) -> Alert:
    return Alert("deadlines", f"taxi-24h:{season}", "24 hours to set your Taxi player",
                 f"The season starts tomorrow at {clock(kickoff, tz)}. After kickoff, no rookie can go on taxi.", "/my-team")


def _names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def option_reminder(*, season: int, left: str, names: list[str]) -> Alert:
    """left: '7d' or '24h'. 'Malik Nabers and Brock Bowers to exercise their team option.'"""
    their = "his" if len(names) == 1 else "their"
    title = "7 days left" if left == "7d" else "24 hours left"
    return Alert("deadlines", f"option-{left}:{season}", title, f"{_names(names)} to exercise {their} team option.", "/my-team")


def trade_deadline(*, season: int, left: str, deadline: datetime, tz: str | None) -> Alert:
    if left == "7d":
        return Alert("deadlines", f"trade-7d:{season}", "7 days until the trade deadline",
                     f"Trades lock {short_date(deadline, tz)} at {clock(deadline, tz)}.", "/front-office")
    return Alert("deadlines", f"trade-12h:{season}", "12 hours until the trade deadline",
                 f"Trades lock at {clock(deadline, tz)}.", "/front-office")


def over_cap(*, season: int, day: date, used: float, cap: float, day_number: int) -> Alert:
    over = used - cap
    money = f"${over:,.0f}" if float(over).is_integer() else f"${over:,.2f}"
    used_s = f"${used:,.0f}" if float(used).is_integer() else f"${used:,.2f}"
    if day_number <= 1:
        return Alert("cap", f"cap:{season}:{day.isoformat()}", f"You're {money} over the cap",
                     f"You're at {used_s} of ${cap:,.0f}. Cut, trade or IR someone to get under. "
                     "This alert repeats daily until you're under.", "/my-team")
    return Alert("cap", f"cap:{season}:{day.isoformat()}", f"Still {money} over the cap (day {day_number})",
                 f"You're at {used_s} of ${cap:,.0f}.", "/my-team")


def trade_offer_received(*, offer_id: str, sender: str) -> Alert:
    return Alert("trade_offers", f"offer:{offer_id}", "New trade offer", f"{sender} sent you a trade offer.", "/transactions")


def trade_offer_answered(*, offer_id: str, partner: str, accepted: bool) -> Alert:
    if accepted:
        return Alert("trade_offers", f"offer-answer:{offer_id}", "Trade accepted", f"{partner} accepted your trade offer.", "/activity")
    return Alert("trade_offers", f"offer-answer:{offer_id}", "Trade declined", f"{partner} declined your trade offer.", "/transactions")


def trade_completed(*, trade_id: str, league_name: str | None) -> Alert:
    return Alert("trades", f"trade:{trade_id}", "", f"A trade was completed in {league_name or 'your league'}",
                 f"/activity?focus={trade_id}")


def clinched(*, season: int) -> Alert:
    return Alert("playoffs", f"clinch:{season}", "You clinched a playoff spot", "You're in the playoffs.", "/league?at=standings")


def bracket_set(*, season: int, seed: int, opp: str | None, week: int) -> Alert:
    body = f"You're the {seed} seed." + (f" You play {opp} in Week {week}." if opp else "")
    return Alert("playoffs", f"bracket:{season}", "Playoffs are set", body, "/league")


def rollover_done(*, season: int) -> Alert:
    return Alert("rollover", f"rollover:{season}", "Rollover is done",
                 "A new season has started in Legacy. Take a look at your team.", "/my-team")


def test_alert(now: datetime) -> Alert:
    return Alert("test", f"test:{now.strftime('%Y%m%d%H%M%S')}", "Alerts are on", "This is what a Legacy Dynasty alert looks like.", "/settings")


# ---- pure decisions --------------------------------------------------------------------------------

def clinched_teams(points: Mapping[int, float], weeks_left: int, spots: int, per_week: float = 3.0) -> set[int]:
    """Teams sure of a playoff spot: fewer than `spots` other teams can still reach their points.

    A team that could tie is counted as a threat (the tiebreaker is points for, which isn't settled)."""
    best = {rid: pts + weeks_left * per_week for rid, pts in points.items()}
    out = set()
    for rid, pts in points.items():
        threats = sum(1 for other, top in best.items() if other != rid and top >= pts)
        if threats < spots:
            out.add(rid)
    return out


def best_sub(slot: str, bench: Iterable[Mapping[str, Any]], fits) -> dict[str, Any] | None:
    """The bench player with the best projection who can play `slot` (already filtered to healthy and not yet played)."""
    options = [b for b in bench if fits(slot, str(b.get("pos") or "")) and float(b.get("proj") or 0) > 0]
    if not options:
        return None
    top = max(options, key=lambda b: float(b.get("proj") or 0))
    return {"name": top["name"], "proj": round(float(top.get("proj") or 0), 1), "player_id": top.get("player_id")}


def cap_streak(today: date, sent_days: Iterable[date]) -> int:
    """Which day of being over the cap today is: 1 + the unbroken run of earlier daily alerts."""
    days, n, d = set(sent_days), 1, today - timedelta(days=1)
    while d in days:
        n, d = n + 1, d - timedelta(days=1)
    return n


def parse_deadline(value: str | None, week_kickoff) -> datetime | None:
    """The trade deadline from Settings: an exact time ('2026-11-24 23:59', Eastern unless a zone is given) or
    'week N' (trades lock when week N's first game kicks off; week_kickoff(N) gives that time)."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower().startswith("week"):
        try:
            return week_kickoff(int(text.split()[-1]))
        except (ValueError, TypeError):
            return None
    try:
        out = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (out if out.tzinfo else out.replace(tzinfo=ZoneInfo("America/New_York"))).astimezone(timezone.utc)


def due_window(now: datetime, starts_at: datetime, lead: timedelta, ends_at: datetime) -> bool:
    return starts_at - lead <= now < ends_at


@dataclass
class Gate:
    """Why an alert waits or is dropped at delivery time."""
    send: bool
    status: str = ""  # '' (send now / keep waiting), 'off', 'expired', 'limited'


def gate(row: Mapping[str, Any], *, now: datetime, off: set[str], tz: str | None, sent_this_week: int) -> Gate:
    expires = parse_ts(row.get("expires_at"))
    if expires and now >= expires:
        return Gate(False, "expired")
    if row.get("kind") in off:
        return Gate(False, "off")
    if not row.get("urgent") and quiet(now, tz):
        return Gate(False)
    if row.get("kind") not in NOT_LIMITED and not row.get("urgent") and sent_this_week >= WEEKLY_LIMIT:
        return Gate(False, "limited")
    return Gate(True)


def payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """The APNs JSON for one alert."""
    alert: dict[str, Any] = {"body": row.get("body") or ""}
    if row.get("title"):
        alert["title"] = row["title"]
    url = row.get("url") or "/"
    # The app reads the page to open from "body" (where expo-notifications looks for an alert's data) or "url".
    return {"aps": {"alert": alert, "sound": "default"}, "url": url, "body": {"url": url}}


# ---- the outbox -------------------------------------------------------------------------------------

def enqueue(client: Any, alert: Alert, *, league_id: str, user_ids: Iterable[str], not_before: datetime | None = None,
            expires_at: datetime | None = None, seed: bool = False) -> int:
    """Queue one alert for each owner (once per owner and key). seed=True records it as already handled, so the very
    first run in a league doesn't send a burst of old news."""
    now = datetime.now(timezone.utc)
    rows = [{
        "league_id": league_id, "user_id": uid, "kind": alert.kind, "key": alert.key, "title": alert.title,
        "body": alert.body, "url": alert.url, "urgent": alert.urgent,
        "not_before": (not_before or now).isoformat(), "expires_at": expires_at.isoformat() if expires_at else None,
        "status": "seeded" if seed else "queued",
    } for uid in sorted({str(u) for u in user_ids if u})]
    if not rows:
        return 0
    client.table("push_outbox").upsert(rows, on_conflict="user_id,key", ignore_duplicates=True).execute()
    return len(rows)


def deliver(client: Any, push: Any, now: datetime | None = None) -> dict[str, int]:
    """Send every due alert that passes the owner's switches, quiet hours and weekly limit."""
    now = now or datetime.now(timezone.utc)
    counts = {"sent": 0, "held": 0, "off": 0, "expired": 0, "limited": 0, "no_phone": 0, "failed": 0}
    due = (client.table("push_outbox").select("*").eq("status", "queued").lte("not_before", now.isoformat())
           .order("not_before").limit(500).execute().data or [])
    if not due:
        return counts
    users = sorted({r["user_id"] for r in due})
    prefs = {(p["user_id"], p["league_id"]): p for p in
             client.table("push_prefs").select("user_id,league_id,off,tz").in_("user_id", users).execute().data or []}
    tokens: dict[str, list[dict[str, Any]]] = {}
    for t in client.table("push_tokens").select("token,user_id,kind,tz").in_("user_id", users).execute().data or []:
        if (t.get("kind") or "expo") == "apns":
            tokens.setdefault(t["user_id"], []).append(t)
    week_ago = (now - timedelta(days=7)).isoformat()
    sent_rows = (client.table("push_outbox").select("user_id,kind,urgent").eq("status", "sent").gte("sent_at", week_ago)
                 .in_("user_id", users).execute().data or [])
    sent_count: dict[str, int] = {}
    for r in sent_rows:
        if r.get("kind") not in NOT_LIMITED and not r.get("urgent"):
            sent_count[r["user_id"]] = sent_count.get(r["user_id"], 0) + 1

    for row in due:
        uid = row["user_id"]
        pref = prefs.get((uid, row["league_id"])) or {}
        phones = tokens.get(uid, [])
        tz = pref.get("tz") or next((t.get("tz") for t in phones if t.get("tz")), None)
        verdict = gate(row, now=now, off=set(pref.get("off") or []), tz=tz, sent_this_week=sent_count.get(uid, 0))
        if not verdict.send:
            if verdict.status:
                _mark(client, row, verdict.status, now)
                counts[verdict.status] += 1
            else:
                counts["held"] += 1
            continue
        if not phones:
            _mark(client, row, "no_phone", now)
            counts["no_phone"] += 1
            continue
        if not _claim(client, row, now):  # another runner (the VM or the GitHub backup) is sending this one
            continue
        ok = False
        for phone in phones:
            result = push.send_alert(phone["token"], payload(row), urgent=bool(row.get("urgent")))
            if result.ok:
                ok = True
            elif result.gone:
                client.table("push_tokens").delete().eq("token", phone["token"]).execute()
            else:
                print(f"APNS {result.status} {result.reason}", flush=True)
        _mark(client, row, "sent" if ok else "failed", now)
        counts["sent" if ok else "failed"] += 1
        if ok and row.get("kind") not in NOT_LIMITED and not row.get("urgent"):
            sent_count[uid] = sent_count.get(uid, 0) + 1
    return counts


def _claim(client: Any, row: Mapping[str, Any], now: datetime) -> bool:
    """Take the row before sending (stamp sent_at while it is still empty), so two runners never send it twice."""
    got = (client.table("push_outbox").update({"sent_at": now.isoformat()}).eq("id", row["id"]).eq("status", "queued")
           .is_("sent_at", "null").execute().data or [])
    return bool(got)


def _mark(client: Any, row: Mapping[str, Any], status: str, now: datetime) -> None:
    client.table("push_outbox").update({"status": status, "sent_at": now.isoformat()}).eq("id", row["id"]).execute()


# ---- owner settings (server + website) ---------------------------------------------------------------

def settings_view(pref: Mapping[str, Any] | None) -> dict[str, Any]:
    off = set((pref or {}).get("off") or [])
    return {
        "kinds": [{"kind": k, "label": label, "note": KIND_NOTES.get(k, ""), "on": k not in off} for k, label in KINDS.items()],
        "tz": (pref or {}).get("tz") or None,
        "quiet_hours": "11 PM to 8 AM",
        "weekly_limit": WEEKLY_LIMIT,
    }


def clean_off(kinds: Iterable[str]) -> list[str]:
    return sorted({k for k in kinds if k in KINDS})
