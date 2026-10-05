"""League AI learning loop: log checkable calls, grade them on real results, diagnose, distill lessons.

In a chat, the AI calls `log_prediction` whenever it makes a call that the games will
settle (start A over B, add X over Y, accept/decline a trade). Each week the job in
`league_ai.learning_job` scores those calls in this league's own scoring, asks Claude
why each one went the way it did (usage, injuries, game script, weather, scheme, or
plain variance), and folds the answers into a short list of lessons. The lessons and
the track record go back into every chat, so the next call is made with them in view.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping

from league_ai.tools import ToolSpec

KINDS = ("start_sit", "pickup", "trade")
FACTORS = (
    "recent_usage", "season_production", "matchup", "vegas_total", "game_script", "weather",
    "injury", "scheme_coaching", "market_rank", "contract_value", "age_curve", "team_need",
)
WEIGHTS = ("major", "minor")
CAUSES = (
    "fluke", "usage_shift", "in_game_injury", "injury_status", "game_script", "weather",
    "defense_matchup", "scheme_coaching", "missing_info", "reasoning_error",
)
DEFAULT_HORIZON = {"start_sit": 1, "pickup": 3, "trade": 4}
MAX_LESSONS_IN_PACK = 15


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


# ---- logging (chat side) ---------------------------------------------------------

class Ledger:
    """The chat's handle on the ledger. Runs as the asking owner, so RLS keeps rows private."""

    def __init__(self, client: Any, *, league_id: str, user_id: str, league_team_id: str | None, season: int, week: int,
                 resolve_player: Callable[[str], dict[str, Any] | None] | None = None,
                 conversation_id: Callable[[], str | None] | None = None):
        self.client = client
        self.league_id = league_id
        self.user_id = user_id
        self.league_team_id = league_team_id
        self.season = int(season)
        self.week = int(week or 1)
        self.resolve_player = resolve_player
        self.conversation_id = conversation_id or (lambda: None)

    def _players(self, names: Iterable[Any]) -> list[dict[str, Any]]:
        out = []
        for raw in names or []:
            name = str(raw.get("name") if isinstance(raw, Mapping) else raw or "").strip()
            if not name:
                continue
            row = None
            if self.resolve_player:
                try:
                    row = self.resolve_player(name)
                except Exception:
                    row = None
            out.append({"name": (row or {}).get("full_name") or name, "sleeper_id": str((row or {}).get("sleeper_id") or "") or None,
                        "position": (row or {}).get("position")})
        return out

    def log_prediction(self, kind: str, pick: list[Any], over: list[Any] | None = None, rationale: str = "", factors: Mapping[str, str] | None = None,
                       confidence: float | None = None, week: int | None = None, horizon_weeks: int | None = None, verdict: str | None = None,
                       question: str | None = None) -> dict[str, Any]:
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            return {"error": f"kind must be one of {', '.join(KINDS)}"}
        picks, overs = self._players(pick), self._players(over or [])
        if not picks:
            return {"error": "pick needs at least one player"}
        if kind in ("start_sit", "pickup") and not overs:
            return {"error": "name the player(s) you passed on in 'over' so the call can be graded"}
        if kind == "trade" and str(verdict or "").lower() not in ("accept", "decline"):
            return {"error": "trade calls need verdict 'accept' or 'decline'"}
        clean_factors = {str(k): str(v).lower() for k, v in (factors or {}).items() if str(k) in FACTORS and str(v).lower() in WEIGHTS}
        unresolved = [p["name"] for p in picks + overs if not p["sleeper_id"]]
        row = {
            "league_id": self.league_id, "user_id": self.user_id, "league_team_id": self.league_team_id,
            "conversation_id": self.conversation_id(), "kind": kind, "season": self.season, "status": "open",
            "week": int(week or self.week), "horizon_weeks": int(horizon_weeks or DEFAULT_HORIZON[kind]),
            "pick": picks, "over": overs, "verdict": (str(verdict).lower() if verdict else None),
            "question": (question or "")[:500] or None, "rationale": (rationale or "")[:1200] or None,
            "factors": clean_factors,
            "confidence": None if confidence is None else max(0.0, min(1.0, float(confidence))),
        }
        replaced = self._previous_call(row)
        if replaced == "duplicate":
            return {"logged": False, "note": "this exact call is already logged for that week; nothing added"}
        try:
            self.client.table("league_ai_predictions").insert(row).execute()
        except Exception as exc:
            return {"error": f"could not log the call: {type(exc).__name__}"}
        out: dict[str, Any] = {"logged": True, "grades_after_week": row["week"] + row["horizon_weeks"] - 1}
        if unresolved:
            out["warning"] = "could not match these names to NFL players, so they can't be graded: " + ", ".join(unresolved)
        return out

    def _previous_call(self, row: Mapping[str, Any]) -> str | None:
        """Same players, same week, still open: an exact repeat is skipped; a reversed call voids the old one."""
        def ids(players: Iterable[Mapping[str, Any]]) -> frozenset[str]:
            return frozenset(str(p.get("sleeper_id") or p.get("name")) for p in players)

        pick, over = ids(row["pick"]), ids(row["over"])
        try:
            existing = (self.client.table("league_ai_predictions").select("id, pick, over, verdict").eq("league_id", self.league_id).eq("user_id", self.user_id)
                        .eq("kind", row["kind"]).eq("season", row["season"]).eq("week", row["week"]).eq("status", "open").limit(50).execute().data or [])
        except Exception:
            return None
        for e in existing:
            e_pick, e_over = ids(e.get("pick") or []), ids(e.get("over") or [])
            if e_pick == pick and e_over == over and (e.get("verdict") or None) == row.get("verdict"):
                return "duplicate"
            if (e_pick == over and e_over == pick) or (e_pick == pick and e_over == over):  # changed its mind: the newest call counts
                try:
                    self.client.table("league_ai_predictions").update({"status": "void"}).eq("id", e["id"]).execute()
                except Exception:
                    pass
                return "replaced"
        return None

    def track_record(self, limit: int = 10) -> dict[str, Any]:
        try:
            rows = (self.client.table("league_ai_predictions").select("kind, season, week, pick, over, verdict, correct, pick_points, over_points, cause, process_error, diagnosis, lesson, status")
                    .eq("league_id", self.league_id).eq("user_id", self.user_id).order("created_at", desc=True).limit(200).execute().data or [])
        except Exception:
            return {"error": "track record unavailable"}
        graded = [r for r in rows if r.get("status") == "graded"]
        summary = record_by(graded, lambda r: r.get("kind"))
        recent = [{
            "when": f"{r['season']} wk {r['week']}", "kind": r["kind"],
            "call": _call_text(r), "result": "right" if r.get("correct") else "wrong",
            "points": f"{_num(r.get('pick_points')):.1f} vs {_num(r.get('over_points')):.1f}",
            "why": r.get("diagnosis"), "cause": r.get("cause"), "lesson": r.get("lesson"),
        } for r in graded[:limit]]
        return {"record": summary, "open_calls": sum(1 for r in rows if r.get("status") == "open"), "recent": recent}

    def tools(self) -> list[ToolSpec]:
        player_list = {"type": "array", "items": {"type": "string"}, "description": "player names"}
        return [
            ToolSpec(
                "log_prediction",
                "Log a call the games will settle so it can be graded against real results: start/sit (who to start over whom, this week), "
                "pickup (add X over Y, graded over the next 3 weeks) or trade (accept/decline, graded on the next 4 weeks of points). "
                "Call it once per call you make, after deciding. factors maps each factor that drove the call to 'major' or 'minor' "
                f"(factors: {', '.join(FACTORS)}). confidence is your honest probability the pick outscores the alternative.",
                {"type": "object", "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "pick": {**player_list, "description": "players you favor (trade: players this owner would receive)"},
                    "over": {**player_list, "description": "players you passed on (trade: players this owner would send)"},
                    "verdict": {"type": "string", "enum": ["accept", "decline"], "description": "trade only"},
                    "rationale": {"type": "string", "description": "one or two sentences on why"},
                    "factors": {"type": "object", "additionalProperties": {"type": "string", "enum": list(WEIGHTS)}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "week": {"type": "integer", "description": "NFL week the call is for; defaults to this week"},
                    "question": {"type": "string", "description": "the owner's question, briefly"},
                }, "required": ["kind", "pick", "rationale", "factors", "confidence"]},
                lambda **kw: self.log_prediction(**kw),
            ),
            ToolSpec(
                "get_my_track_record",
                "This owner's graded calls from you: record by kind and the most recent calls with what happened, why, and the lesson. "
                "Use when the owner asks how your advice has done, or before a call similar to one you recently got wrong.",
                {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 25}}},
                lambda limit=10: self.track_record(limit),
            ),
        ]


def _call_text(r: Mapping[str, Any]) -> str:
    picks = ", ".join(p.get("name", "?") for p in r.get("pick") or [])
    overs = ", ".join(p.get("name", "?") for p in r.get("over") or [])
    if r.get("kind") == "trade":
        return f"{(r.get('verdict') or '?')} trade: get {picks} for {overs}"
    verb = "start" if r.get("kind") == "start_sit" else "add"
    return f"{verb} {picks} over {overs}"


def record_by(rows: Iterable[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], Any]) -> dict[str, str]:
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        k = key(r)
        if k is None or r.get("correct") is None:
            continue
        tally[str(k)][0 if r["correct"] else 1] += 1
    return {k: f"{w}-{l} ({round(100 * w / (w + l))}%)" for k, (w, l) in sorted(tally.items()) if w + l}


# ---- what the chat reads -------------------------------------------------------------

def learning_pack_lines(client: Any, league_id: str, user_id: str, season: int) -> list[str]:
    """'How your calls have graded' section for the league pack: league scorecard, lessons, this owner's record."""
    lines: list[str] = []
    try:
        card = (client.table("league_ai_scorecards").select("stats, computed_at").eq("league_id", league_id).eq("season", season).limit(1).execute().data or [])
    except Exception:
        card = []
    if card:
        stats = card[0].get("stats") or {}
        if stats.get("by_kind"):
            lines.append("Your record this season across the league: " + "; ".join(f"{k} {v}" for k, v in stats["by_kind"].items()) + ".")
        if stats.get("by_factor"):
            lines.append("When a factor was a MAJOR driver of your call: " + "; ".join(f"{k} {v}" for k, v in stats["by_factor"].items())
                         + ". Lean harder on factors that grade well and be skeptical of ones that don't.")
        if stats.get("by_cause"):
            lines.append("Why your misses happened: " + "; ".join(f"{k} {v}" for k, v in stats["by_cause"].items()) + ".")
        if stats.get("calibration"):
            lines.append("Calibration (your stated confidence vs how often you were right): " + "; ".join(f"{k} -> {v}" for k, v in stats["calibration"].items()) + ".")
    try:
        lessons = (client.table("league_ai_lessons").select("lesson, kind, factor, evidence_count, hits, misses").eq("league_id", league_id).eq("active", True)
                   .order("evidence_count", desc=True).limit(MAX_LESSONS_IN_PACK).execute().data or [])
    except Exception:
        lessons = []
    for l in lessons:
        tag = "/".join(x for x in [l.get("kind"), l.get("factor")] if x)
        track = f", since adopted {l['hits']}-{l['misses']}" if (l.get("hits") or l.get("misses")) else ""
        lines.append(f"Lesson{(' [' + tag + ']') if tag else ''} (seen {l.get('evidence_count') or 1}x{track}): {l['lesson']}")
    try:
        mine = (client.table("league_ai_predictions").select("kind, correct, status").eq("league_id", league_id).eq("user_id", user_id).eq("season", season)
                .eq("status", "graded").limit(500).execute().data or [])
    except Exception:
        mine = []
    if mine:
        lines.append("Your calls for this owner this season: " + "; ".join(f"{k} {v}" for k, v in record_by(mine, lambda r: r.get("kind")).items()) + ".")
    return lines


# ---- grading (job side, pure) ----------------------------------------------------------

def league_points(stats: Mapping[str, Any] | None, scoring: Mapping[str, Any]) -> float:
    """Fantasy points in this league's scoring: Sleeper stat keys match scoring_settings keys."""
    if not stats:
        return 0.0
    return round(sum(_num(stats.get(k)) * _num(w) for k, w in (scoring or {}).items() if k in stats), 2)


def grade_weeks(pred: Mapping[str, Any]) -> list[int]:
    start = int(pred["week"])
    return list(range(start, start + int(pred.get("horizon_weeks") or 1)))


def is_due(pred: Mapping[str, Any], completed_week: int) -> bool:
    return grade_weeks(pred)[-1] <= completed_week


def grade(pred: Mapping[str, Any], points: Callable[[str, int], float]) -> dict[str, Any] | None:
    """Score a call. points(sleeper_id, week) -> league points (0 when the player didn't play)."""
    picks, overs = pred.get("pick") or [], pred.get("over") or []
    if any(not p.get("sleeper_id") for p in picks + overs) or not picks or not overs:
        return None
    weeks = grade_weeks(pred)
    per_player = {p["sleeper_id"]: {"name": p.get("name"), "weeks": {w: points(p["sleeper_id"], w) for w in weeks}} for p in picks + overs}
    pick_pts = round(sum(sum(per_player[p["sleeper_id"]]["weeks"].values()) for p in picks), 2)
    over_pts = round(sum(sum(per_player[p["sleeper_id"]]["weeks"].values()) for p in overs), 2)
    if pred.get("kind") == "start_sit" and len(picks) != len(overs):
        # compare per-player averages when the call isn't one-for-one
        pick_pts, over_pts = round(pick_pts / len(picks), 2), round(over_pts / len(overs), 2)
    favored = pick_pts >= over_pts
    correct = favored if pred.get("kind") != "trade" or pred.get("verdict") == "accept" else not favored
    return {"pick_points": pick_pts, "over_points": over_pts, "correct": correct, "margin": round(pick_pts - over_pts, 2),
            "result": {"weeks": weeks, "players": per_player}}


def scorecard(graded: list[Mapping[str, Any]]) -> dict[str, Any]:
    """League-wide anonymous totals: by kind, by major factor, by cause of misses, and calibration."""
    by_factor_rows = [(f, r) for r in graded for f, w in (r.get("factors") or {}).items() if w == "major"]
    by_factor: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f, r in by_factor_rows:
        if r.get("correct") is not None:
            by_factor[f][0 if r["correct"] else 1] += 1
    causes: dict[str, int] = defaultdict(int)
    for r in graded:
        if r.get("correct") is False and r.get("cause"):
            causes[str(r["cause"])] += 1
    buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in graded:
        c = r.get("confidence")
        if c is None or r.get("correct") is None:
            continue
        b = "50-60%" if c < 0.6 else "60-70%" if c < 0.7 else "70-80%" if c < 0.8 else "80%+"
        buckets[b][0 if r["correct"] else 1] += 1
    return {
        "by_kind": record_by(graded, lambda r: r.get("kind")),
        "by_factor": {f: f"{w}-{l} ({round(100 * w / (w + l))}%)" for f, (w, l) in sorted(by_factor.items()) if w + l >= 2},
        "by_cause": {k: f"{v} misses" for k, v in sorted(causes.items(), key=lambda x: -x[1])},
        "calibration": {b: f"right {round(100 * w / (w + l))}% of {w + l}" for b, (w, l) in sorted(buckets.items()) if w + l >= 3},
        "graded_total": len(graded),
    }


# ---- diagnostic and lessons (job side, model calls) --------------------------------------

DIAGNOSE_SYSTEM = """You are the quality reviewer for League AI, the co-GM in a dynasty fantasy football league. You grade the AI's own past calls against what actually happened so it can improve its method. Be candid and specific. Separate a bad PROCESS from a bad OUTCOME: a well-reasoned call that lost to a long touchdown is variance, not a mistake; a call that ignored a clear usage trend is a mistake even if it happened to win."""

DIAGNOSE_PROMPT = """The AI made this call:
{call}
Its reasoning at the time: {rationale}
Factors it weighted: {factors}. Stated confidence: {confidence}.
Result: the pick scored {pick_points} and the alternative scored {over_points} in this league's scoring, so the call was {verdict}.

What happened (box scores, usage vs each player's prior 4-week average, game conditions, injury reports):
{facts}

{search_note}
Answer ONLY with a JSON object, no prose before or after:
{{"cause": one of {causes},
 "process_error": true if the reasoning or information intake was wrong (not just unlucky), else false,
 "diagnosis": "two or three sentences: what decided it, with the specific numbers (snaps, targets, carries, TDs, game script, weather, scheme)",
 "factor_review": {{"<factor>": "helped" | "misled" | "irrelevant"}} for each factor it weighted,
 "lesson": "one generalizable rule about METHOD for future calls, or null if this was variance with nothing to learn. Never mention any owner by name."}}"""

CONSOLIDATE_PROMPT = """You maintain League AI's playbook: the short list of lessons it reads before every answer, learned from grading its own calls against real results in this league.

Current lessons (JSON, with ids):
{lessons}

New lessons from this week's graded calls (with whether each call was right and its cause):
{new}

League-wide scorecard: {scorecard}

Rewrite the playbook. Merge duplicates (add their evidence counts), sharpen vague lessons, retire lessons the evidence now contradicts (active false), and add genuinely new ones. Prefer lessons backed by more than one call; a single fluke is not a lesson. Lessons are about method (what to weigh, what data to check, when to discount a factor), never about a specific owner's plans or names. Keep at most {max_lessons} active lessons, each one sentence.

Answer ONLY with a JSON array, no prose: [{{"id": "<existing id or null>", "lesson": "...", "kind": "start_sit|pickup|trade|null", "factor": "<factor or null>", "evidence_count": n, "active": true|false}}]"""


def parse_json_block(text: str, opener: str = "{") -> Any:
    closer = "}" if opener == "{" else "]"
    m = re.search(re.escape(opener) + r".*" + re.escape(closer), text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def diagnose_request(pred: Mapping[str, Any], graded: Mapping[str, Any], facts: str, *, web_search: bool) -> tuple[str, str]:
    """(system, prompt) for one call's diagnostic."""
    prompt = DIAGNOSE_PROMPT.format(
        call=f"{pred.get('season')} week {pred.get('week')}: {_call_text(pred)}" + (f" (graded over {pred.get('horizon_weeks')} weeks)" if (pred.get("horizon_weeks") or 1) > 1 else ""),
        rationale=pred.get("rationale") or "(not recorded)",
        factors=json.dumps(pred.get("factors") or {}),
        confidence=pred.get("confidence") if pred.get("confidence") is not None else "not stated",
        pick_points=graded["pick_points"], over_points=graded["over_points"],
        verdict="RIGHT" if graded["correct"] else "WRONG",
        facts=facts or "(no box scores on file)",
        search_note=("You may search the web (at most 3 searches) for game recaps, coaching or scheme changes, weather or injury news from that week if the data above doesn't explain the result." if web_search else ""),
        causes=" | ".join(CAUSES),
    )
    return DIAGNOSE_SYSTEM, prompt


def parse_diagnosis(text: str) -> dict[str, Any] | None:
    data = parse_json_block(text)
    if not isinstance(data, dict):
        return None
    cause = str(data.get("cause") or "").strip()
    lesson = data.get("lesson")
    return {
        "cause": cause if cause in CAUSES else None,
        "process_error": bool(data.get("process_error")),
        "diagnosis": str(data.get("diagnosis") or "")[:1200] or None,
        "lesson": (str(lesson)[:600] if lesson and str(lesson).strip().lower() not in ("null", "none") else None),
        "factor_review": data.get("factor_review") if isinstance(data.get("factor_review"), dict) else {},
    }


def diagnose(pred: Mapping[str, Any], graded: Mapping[str, Any], facts: str, ask: Callable[[str, str, bool], str], *, web_search: bool) -> dict[str, Any] | None:
    """ask(system, prompt, web_search) -> model text. Returns cause/process_error/diagnosis/lesson."""
    system, prompt = diagnose_request(pred, graded, facts, web_search=web_search)
    return parse_diagnosis(ask(system, prompt, web_search))


def consolidate_request(lessons: list[Mapping[str, Any]], new_items: list[Mapping[str, Any]], card: Mapping[str, Any], max_lessons: int = MAX_LESSONS_IN_PACK) -> tuple[str, str]:
    prompt = CONSOLIDATE_PROMPT.format(
        lessons=json.dumps([{k: l.get(k) for k in ("id", "lesson", "kind", "factor", "evidence_count")} for l in lessons], default=str),
        new=json.dumps(new_items, default=str), scorecard=json.dumps(card, default=str), max_lessons=max_lessons,
    )
    return DIAGNOSE_SYSTEM, prompt


def parse_playbook(text: str) -> list[dict[str, Any]] | None:
    data = parse_json_block(text, "[")
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict) or not str(item.get("lesson") or "").strip():
            continue
        kind = item.get("kind") if item.get("kind") in KINDS else None
        factor = item.get("factor") if item.get("factor") in FACTORS else None
        out.append({"id": item.get("id") or None, "lesson": str(item["lesson"]).strip()[:600], "kind": kind, "factor": factor,
                    "evidence_count": max(1, int(_num(item.get("evidence_count")) or 1)), "active": item.get("active") is not False})
    return out


def consolidate(lessons: list[Mapping[str, Any]], new_items: list[Mapping[str, Any]], card: Mapping[str, Any], ask: Callable[[str, str, bool], str],
                max_lessons: int = MAX_LESSONS_IN_PACK) -> list[dict[str, Any]] | None:
    system, prompt = consolidate_request(lessons, new_items, card, max_lessons)
    return parse_playbook(ask(system, prompt, False))


def diagnostic_facts(client: Any, pred: Mapping[str, Any]) -> str:
    """Box scores, usage vs the prior 4 weeks, game conditions and practice status for every player in the call."""
    season = int(pred["season"])
    weeks = grade_weeks(pred)
    lines: list[str] = []
    for side, players in (("PICK", pred.get("pick") or []), ("PASSED ON", pred.get("over") or [])):
        for p in players:
            sid = p.get("sleeper_id")
            if not sid:
                continue
            try:
                rows = (client.table("nfl_player_stats").select("week, team, opponent, is_home, offense_pct, targets, receptions, receiving_yards, receiving_tds, carries, rushing_yards, rushing_tds, "
                                                              "attempts, passing_yards, passing_tds, interceptions, target_share, air_yards_share, fantasy_points_ppr")
                        .eq("sleeper_id", sid).eq("season", season).eq("season_type", "REG").execute().data or [])
            except Exception:
                rows = []
            by_week = {int(r["week"]): r for r in rows}
            prior = [by_week[w] for w in range(weeks[0] - 4, weeks[0]) if w in by_week]
            base = _usage_avg(prior)
            for w in weeks:
                r = by_week.get(w)
                if not r:
                    lines.append(f"- {side} {p.get('name')} week {w}: no stat line (inactive or did not play)")
                    continue
                lines.append(f"- {side} {p.get('name')} week {w} {'vs' if r.get('is_home') else '@'} {r.get('opponent')}: " + _stat_line(r)
                             + (f" | prior-4-week avg: {base}" if base else " | no prior weeks on file"))
                game = _game(client, season, w, r.get("team"))
                if game:
                    lines.append(f"  game: {game}")
            status = _practice(client, sid, season, weeks[0])
            if status:
                lines.append(f"  injury report going in: {status}")
    return "\n".join(lines)


def _stat_line(r: Mapping[str, Any]) -> str:
    bits = []
    if r.get("offense_pct") is not None:
        bits.append(f"{_num(r['offense_pct']) * (100 if _num(r['offense_pct']) <= 1 else 1):.0f}% snaps")
    if _num(r.get("attempts")):
        bits.append(f"{int(_num(r.get('passing_yards')))} pass yds, {int(_num(r.get('passing_tds')))} TD, {int(_num(r.get('interceptions')))} INT")
    if _num(r.get("carries")):
        bits.append(f"{int(_num(r['carries']))} car-{int(_num(r.get('rushing_yards')))} yds-{int(_num(r.get('rushing_tds')))} TD")
    if _num(r.get("targets")):
        bits.append(f"{int(_num(r['targets']))} tgt ({_num(r.get('target_share')) * 100:.0f}% share), {int(_num(r.get('receptions')))}-{int(_num(r.get('receiving_yards')))}-{int(_num(r.get('receiving_tds')))}")
    bits.append(f"{_num(r.get('fantasy_points_ppr')):.1f} PPR")
    return ", ".join(bits)


def _usage_avg(rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    n = len(rows)
    snaps = [_num(r.get("offense_pct")) for r in rows if r.get("offense_pct") is not None]
    parts = []
    if snaps:
        avg = sum(snaps) / len(snaps)
        parts.append(f"{avg * (100 if avg <= 1 else 1):.0f}% snaps")
    for key, label in (("targets", "tgt"), ("carries", "car"), ("fantasy_points_ppr", "PPR")):
        total = sum(_num(r.get(key)) for r in rows)
        if total:
            parts.append(f"{total / n:.1f} {label}")
    return ", ".join(parts) + f" over {n} games"


def _game(client: Any, season: int, week: int, team: Any) -> str:
    if not team:
        return ""
    try:
        rows = (client.table("nfl_games").select("*").eq("season", season).eq("week", week)
                .or_(f"home_team.eq.{team},away_team.eq.{team}").limit(1).execute().data or [])
    except Exception:
        return ""
    if not rows:
        return ""
    g = rows[0]
    home = g.get("home_team") == team
    us, them = (g.get("home_score"), g.get("away_score")) if home else (g.get("away_score"), g.get("home_score"))
    spread = g.get("spread_line")  # nflverse: positive = home favored
    fav = "" if spread is None else (f", {team} favored by {abs(spread)}" if (spread > 0) == home and spread != 0 else f", {team} underdog by {abs(spread)}")
    weather = "dome/closed roof" if g.get("roof") in ("dome", "closed") else ", ".join(x for x in [f"{g['temp']:.0f}F" if g.get("temp") is not None else "", f"wind {g['wind']:.0f} mph" if g.get("wind") is not None else ""] if x) or "weather not recorded"
    return (f"{team} {us}-{them} final{fav}, total line {g.get('total_line')}; {weather}; coaches {g.get('home_coach')} (home) vs {g.get('away_coach')}; "
            f"QBs {g.get('home_qb_name')} / {g.get('away_qb_name')}")


def _practice(client: Any, sleeper_id: str, season: int, week: int) -> str:
    try:
        rows = (client.table("nfl_practice_reports").select("*").eq("sleeper_id", str(sleeper_id)).eq("season", season).eq("week", week).limit(1).execute().data or [])
    except Exception:
        return ""
    if not rows:
        return ""
    r = rows[0]
    return ", ".join(str(x) for x in [r.get("report_status"), r.get("report_injury") or r.get("practice_injury"), r.get("practice_status")] if x)
