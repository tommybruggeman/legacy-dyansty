"""Our own player status (Healthy, Questionable, Doubtful, Out, IR, PUP, NFI, Suspended) for every NFL player, kept
fresh every 5 minutes by the alert job on the free watcher VM (scripts/push_alerts.py), and every change logged.

Where it comes from: ESPN's 32 public team rosters (every player's current injury entry, the complete list: IR players
included) plus ESPN's injury report (adds the PUP / IR-return designations), the newer entry winning. Both mirror the
teams' official reports. Sleeper isn't used. The feed is read through fetch_feed() only, so another source can be
swapped in behind it.

What we keep:
  nfl_player_status    one row per player we've seen on a report: the confirmed status, and a pending one while a change
                       waits for a second look (a change only counts once two reads at least CONFIRM apart agree, so a
                       feed blip never sends an alert)
  nfl_status_changes   every confirmed change (from, to, when): the history the app never had, for alerts and grading
  nfl_players          injury_status / injury_body_part are written from here, so every page, the website and the GM
                       read the fresh status (the nightly Sleeper players loader no longer overwrites them once this
                       table has rows)

A player on a roster with no injury listed, or on no roster at all, is Healthy. Prints counts only (the repo and its Actions logs are public).
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

ESPN_INJURIES_URLS = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
    "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
)
POSITIONS = {"QB", "RB", "WR", "TE", "K"}
CONFIRM = timedelta(minutes=4)  # the VM runs every 5 minutes, so a change is confirmed on the next run
MIN_FEED = 0.5  # a read listing under half the players we have hurt is treated as a broken feed and skipped

HEALTHY = "Healthy"
OWN_COLUMNS = ("injury_status", "injury_body_part")  # nfl_players columns this module writes
COLUMNS = ("sleeper_id", "espn_id", "status", "body_part", "pending", "pending_since", "since", "checked_at")
# Our labels are the short tags the app, website and GM already read in nfl_players.injury_status.
LABELS = ("Questionable", "Doubtful", "Out", "IR", "PUP", "NFI", "Sus", "DTD")
WORDS = {"Questionable": "Questionable", "Doubtful": "Doubtful", "Out": "Out", "IR": "Injured reserve", "PUP": "PUP list",
         "NFI": "Non-football injury list", "Sus": "Suspended", "DTD": "Day-to-day", HEALTHY: "Healthy"}


def _text(v: Any) -> str:
    return str(v or "").strip()


def label(raw: Any) -> str:
    """Any source's status word -> our label ('' or anything active -> Healthy)."""
    s = re.sub(r"[^a-z]+", " ", _text(raw).lower()).strip()
    if not s or s in {"active", "healthy", "none", "probable"}:
        return HEALTHY
    if "reserve" in s or s in {"ir", "ir r", "injured"} or s.startswith("ir "):
        return "NFI" if "non football" in s or "nfi" in s else "IR"
    if "physically unable" in s or s.startswith("pup"):
        return "PUP"
    if "non football" in s or s.startswith("nfi"):
        return "NFI"
    if s.startswith("sus"):
        return "Sus"
    if "day to day" in s or s == "dtd":
        return "DTD"
    for word in ("questionable", "doubtful", "out"):
        if s.startswith(word):
            return word.capitalize()
    return HEALTHY if s in {"na", "n a"} else _text(raw)[:24]


def body_part(details: Mapping[str, Any]) -> str:
    return _text(details.get("type")) or _text(details.get("location"))


def espn_entries(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """ESPN injury report -> {espn_id: {status, body_part, date, name, position, team}} for fantasy positions."""
    out: dict[str, dict[str, Any]] = {}
    for team in payload.get("injuries") or []:
        abbr = _text(team.get("abbreviation")) or _text(team.get("displayName"))
        for e in team.get("injuries") or []:
            athlete = e.get("athlete") or {}
            espn_id = _text(athlete.get("id")) or _text(e.get("id"))
            pos = _text((athlete.get("position") or {}).get("abbreviation"))
            if not espn_id or pos not in POSITIONS or espn_id in out:
                continue
            out[espn_id] = {**_entry(e), "name": _text(athlete.get("displayName")), "position": pos, "team": abbr}
    return out


def _entry(e: Mapping[str, Any]) -> dict[str, Any]:
    details = e.get("details") or {}
    # fantasyStatus carries the roster designation (IR, IR-R, PUP-R) where the plain status says "Out"
    fs = details.get("fantasyStatus") or {}
    fantasy = label(fs.get("abbreviation") or fs.get("description"))
    status = label(e.get("status"))
    if fantasy in {"IR", "PUP", "NFI", "Sus"} and status in {"Out", HEALTHY, "IR"}:
        status = fantasy
    return {"status": status, "body_part": body_part(details), "date": _text(e.get("date"))}


def roster_entries(payloads: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """ESPN's team rosters -> the same shape, for every player on a 53 (practice squad left out). A player with no
    injury listed is Healthy. Rosters are the complete list (the injury report alone leaves many IR players out)."""
    from pipeline.nfl_data.espn_rosters import team_code
    out: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        team = team_code((payload.get("team") or {}).get("abbreviation")) or ""
        for group in payload.get("athletes") or []:
            items = group.get("items") if isinstance(group, dict) and "items" in group else [group]
            for a in items or []:
                if not isinstance(a, dict):
                    continue
                pos = _text((a.get("position") or {}).get("abbreviation"))
                espn_id = _text(a.get("id"))
                if pos not in POSITIONS or not espn_id or _text((a.get("status") or {}).get("type")) == "practice-squad":
                    continue
                injuries = [i for i in a.get("injuries") or [] if isinstance(i, dict)]
                latest = max(injuries, key=lambda i: _text(i.get("date")), default=None)
                got = _entry(latest) if latest else {"status": HEALTHY, "body_part": "", "date": ""}
                out[espn_id] = {**got, "name": _text(a.get("fullName")) or _text(a.get("displayName")), "position": pos, "team": team}
    return out


def merge(rosters: Mapping[str, Mapping[str, Any]], report: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every rostered player's status: the newer of his roster entry and his injury report entry (the report on a tie)."""
    out = {e: dict(r) for e, r in rosters.items()}
    for e, r in report.items():
        mine = out.get(e)
        if mine is None or _text(r.get("date")) >= _text(mine.get("date")):
            out[e] = {**(mine or {}), **r, "name": r.get("name") or (mine or {}).get("name"), "team": (mine or {}).get("team") or r.get("team")}
    return out


def fetch_feed(get: Callable[[str], Any]) -> dict[str, dict[str, Any]] | None:
    """Every NFL player's status now, or None when ESPN's rosters didn't all load (nothing changes that run, so a
    missing team never reads as everyone on it getting healthy). The injury report is added when it answers."""
    from pipeline.nfl_data import espn_rosters as er
    try:
        ids = er.espn_team_ids(get(er.ESPN_TEAMS_URL))
        payloads = [get(er.ESPN_ROSTER_URL.format(team_id=t)) for t in ids]
    except Exception:
        return None
    if len(ids) < 32 or not all(payloads):
        return None
    report: dict[str, dict[str, Any]] = {}
    for url in ESPN_INJURIES_URLS:
        try:
            report = espn_entries(get(url) or {})
        except Exception:
            continue
        if report:
            break
    return merge(roster_entries(payloads), report)


def step(rows: Mapping[str, Mapping[str, Any]], feed: Mapping[str, Mapping[str, Any]], now: datetime,
         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One read compared with what we hold. rows/feed are keyed by sleeper_id.

    Returns (row updates, confirmed changes). A new reading becomes pending; it's confirmed (and logged) when a later
    read at least CONFIRM after it still says the same. A reading that goes back to the held status drops the pending one.
    """
    stamp = now.isoformat()
    updates, changes = [], []
    for sid in sorted(set(rows) | set(feed)):
        held = rows.get(sid) or {}
        seen = feed.get(sid)
        now_status = seen["status"] if seen else HEALTHY
        part = (seen or {}).get("body_part") or ""
        cur = _text(held.get("status")) or HEALTHY
        if not held and now_status == HEALTHY:  # a healthy player we haven't stored: keep his id link, nothing to say
            updates.append({"sleeper_id": sid, "status": HEALTHY, "body_part": "", "checked_at": stamp, **_ids(seen)})
            continue
        if not held:
            # first time on a report: pending from Healthy, like any change
            updates.append({"sleeper_id": sid, "status": HEALTHY, "body_part": part, "pending": now_status,
                            "pending_since": stamp, "checked_at": stamp, **_ids(seen)})
            continue
        if now_status == cur:
            if held.get("pending") or part != _text(held.get("body_part")):
                updates.append({"sleeper_id": sid, "pending": None, "pending_since": None, "body_part": part or held.get("body_part") or "",
                                "checked_at": stamp})
            continue
        since = _ts(held.get("pending_since"))
        if _text(held.get("pending")) == now_status and since and now - since >= CONFIRM:
            updates.append({"sleeper_id": sid, "status": now_status, "body_part": part, "pending": None, "pending_since": None,
                            "since": stamp, "checked_at": stamp})
            changes.append({"sleeper_id": sid, "from_status": cur, "to_status": now_status, "body_part": part,
                            "source": "espn", "changed_at": stamp})
        elif _text(held.get("pending")) != now_status:
            updates.append({"sleeper_id": sid, "pending": now_status, "pending_since": stamp, "checked_at": stamp})
    return updates, changes


def _ids(seen: Mapping[str, Any] | None) -> dict[str, Any]:
    return {"espn_id": seen.get("espn_id")} if seen and seen.get("espn_id") else {}


def _ts(v: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def feed_ok(feed: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> bool:
    hurt = sum(1 for r in rows.values() if (_text(r.get("status")) or HEALTHY) != HEALTHY)
    return len(feed) >= MIN_FEED * hurt


# ---- the database side ------------------------------------------------------------------------------

def _all(client: Any, table: str, cols: str, key: str) -> list[dict[str, Any]]:
    out, start = [], 0
    while True:
        rows = client.table(table).select(cols).order(key).range(start, start + 999).execute().data or []
        out += rows
        if len(rows) < 1000:
            return out
        start += 1000


def match_ids(client: Any, entries: Mapping[str, Mapping[str, Any]], known: Mapping[str, str]) -> dict[str, str]:
    """espn_id -> sleeper_id: ids we already hold, then the injuries loader's crosswalk, then a unique name+position
    (and team when two share a name) match against nfl_players."""
    from pipeline.nfl_data.common import normalize_name

    out = {e: s for e, s in known.items() if e in entries}
    left = [e for e in entries if e not in out]
    for i in range(0, len(left), 200):
        for r in client.table("nfl_injuries").select("espn_id,sleeper_id").in_("espn_id", left[i:i + 200]).execute().data or []:
            if r.get("sleeper_id"):
                out[str(r["espn_id"])] = str(r["sleeper_id"])
    left = [e for e in left if e not in out]
    if left:
        cw = crosswalk()
        for e in left:
            sid = cw.sleeper_for_espn(e) if cw else None
            if sid:
                out[e] = str(sid)
    left = [e for e in left if e not in out]
    if not left:
        return out
    by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in _all(client, "nfl_players", "sleeper_id,search_name,position,team", "sleeper_id"):
        if _text(r.get("position")) in POSITIONS:
            by_name.setdefault((_text(r.get("search_name")), _text(r.get("position"))), []).append(r)
    taken = set(out.values())
    for e in left:
        info = entries[e]
        hits = [h for h in by_name.get((normalize_name(info.get("name")), _text(info.get("position"))), []) if str(h["sleeper_id"]) not in taken]
        if len(hits) > 1:
            hits = [h for h in hits if _text(h.get("team")) == _text(info.get("team"))]
        if len(hits) == 1:
            out[e] = str(hits[0]["sleeper_id"])
            taken.add(out[e])
    return out


CROSSWALK_CACHE = os.path.join(tempfile.gettempdir(), "legacy-player-ids.csv")


def crosswalk(max_age: float = 86400.0):
    """The public ESPN/Sleeper/gsis id table (DynastyProcess), kept a day in a local file so a 5-minute job doesn't
    download it each time."""
    try:
        from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk
        if not os.path.exists(CROSSWALK_CACHE) or time.time() - os.path.getmtime(CROSSWALK_CACHE) > max_age:
            import requests
            resp = requests.get(CROSSWALK_URL, timeout=60)
            resp.raise_for_status()
            with open(CROSSWALK_CACHE, "w", encoding="utf-8") as f:
                f.write(resp.text)
        with open(CROSSWALK_CACHE, encoding="utf-8") as f:
            return Crosswalk.from_csv(f.read())
    except Exception:
        return None


def refresh(client: Any, get: Callable[[str], Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """Read the feed, update our statuses, log and return the confirmed changes ([] when the feed is down)."""
    now = now or datetime.now(timezone.utc)
    try:
        held = {str(r["sleeper_id"]): r for r in _all(client, "nfl_player_status",
                                                      ",".join(COLUMNS), "sleeper_id")}
    except Exception:
        print("player status: table missing (sql/player-status.sql); skipping", flush=True)
        return []
    entries = fetch_feed(get)
    if entries is None or not feed_ok(entries, held):
        print(f"player status: feed {'down' if entries is None else 'too short'}; nothing changed", flush=True)
        return []
    ids = match_ids(client, entries, {_text(r.get("espn_id")): sid for sid, r in held.items() if r.get("espn_id")})
    feed = {sid: {**entries[e], "espn_id": e} for e, sid in ids.items()}
    first = not held
    updates, changes = step(held, feed, now)
    if first:  # the very first read: take it as it is, no pending, nothing logged
        updates = [{**u, "status": u.get("pending") or HEALTHY, "pending": None, "pending_since": None, "since": now.isoformat()}
                   for u in updates]
        changes = []
    full = [{c: {**held.get(u["sleeper_id"], {}), **u}.get(c) for c in COLUMNS} for u in updates]  # every row the same columns
    for i in range(0, len(full), 500):
        client.table("nfl_player_status").upsert(full[i:i + 500], on_conflict="sleeper_id").execute()
    if changes:
        logged = client.table("nfl_status_changes").insert(changes).execute().data or changes
        changes = logged
    final = {sid: (_text(r.get("status")) or HEALTHY, _text(r.get("body_part"))) for sid, r in held.items()}
    final.update({u["sleeper_id"]: (u["status"], _text(u.get("body_part"))) for u in updates if "since" in u})
    fixed = reconcile(client, final)
    print(f"player status: {len(entries)} on the report, {len(ids)} matched, {len(changes)} changes, {fixed} player rows set"
          f"{' (first read)' if first else ''}", flush=True)
    return changes


def reconcile(client: Any, final: Mapping[str, tuple[str, str]]) -> int:
    """Make nfl_players.injury_status (what every page reads) match our confirmed statuses; Healthy is stored as none."""
    want = {sid: (None if s == HEALTHY else s) for sid, (s, _) in final.items()}
    parts = {sid: (p or None) for sid, (_, p) in final.items()}
    have: dict[str, Any] = {}
    start = 0
    while True:
        rows = (client.table("nfl_players").select("sleeper_id,injury_status").not_.is_("injury_status", "null")
                .order("sleeper_id").range(start, start + 999).execute().data or [])
        have.update({str(r["sleeper_id"]): _text(r.get("injury_status")) or None for r in rows})
        if len(rows) < 1000:
            break
        start += 1000
    fix = {sid: None for sid in have if want.get(sid) is None}
    fix.update({sid: s for sid, s in want.items() if s and have.get(sid) != s})
    for sid, s in fix.items():
        try:
            client.table("nfl_players").update({"injury_status": s, "injury_body_part": parts.get(sid) if s else None}).eq("sleeper_id", sid).execute()
        except Exception:
            pass
    return len(fix)


def in_use(client: Any) -> bool:
    """True once our status table has rows: the Sleeper players loader then leaves injury_status alone."""
    try:
        return bool(client.table("nfl_player_status").select("sleeper_id").limit(1).execute().data)
    except Exception:
        return False


GAME_TAGS = {"Questionable", "Doubtful", "Out", "DTD"}


def game_tag_expired(change: Mapping[str, Any], team: str, played: set[str]) -> bool:
    """A game-week tag (Questionable, Doubtful, Out) dropping off after his game is just the report resetting for the
    next week, not news: no alert (the change is still logged). A player coming off IR or PUP is always news."""
    return change.get("to_status") == HEALTHY and change.get("from_status") in GAME_TAGS and team in played


def tag(status: str | None) -> str:
    """Our label -> the lower-case tag the lineup alerts use ('out', 'ir', 'pup', 'sus', 'nfi', 'doubtful', ...)."""
    s = _text(status)
    return "" if not s or s == HEALTHY else s.lower()
