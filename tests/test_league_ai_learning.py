import json
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from league_ai.learning import (
    Ledger, consolidate, diagnose, diagnostic_facts, grade, is_due, league_points, learning_pack_lines, record_by, scorecard,
)
from league_ai.learning_job import Res, WeeklyStats, completed_week, run


class MemQuery:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters, self.op, self.payload, self._limit, self._order = [], "select", None, None, []

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def in_(self, k, vals):
        self.filters.append(lambda r: r.get(k) in vals)
        return self

    def or_(self, expr):
        opts = [part.split(".eq.") for part in expr.split(",")]
        self.filters.append(lambda r: any(str(r.get(k)) == v for k, v in opts))
        return self

    def order(self, k, desc=False):
        self._order.append((k, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload, self.conflict = "upsert", row, on_conflict
        return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        if self.op == "insert":
            new = {"id": str(uuid.uuid4()), **self.payload}
            rows.append(new)
            return SimpleNamespace(data=[new])
        if self.op == "upsert":
            keys = self.conflict.split(",")
            for r in rows:
                if all(r.get(k) == self.payload.get(k) for k in keys):
                    r.update(self.payload)
                    return SimpleNamespace(data=[r])
            rows.append(dict(self.payload))
            return SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=hit)
        for k, desc in reversed(self._order):
            hit = sorted(hit, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        return SimpleNamespace(data=hit[: self._limit] if self._limit else hit)


class MemDB:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def table(self, name):
        return MemQuery(self.tables, name)


SCORING = {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "rec_td": 6.0, "rush_td": 6.0}
A = {"sleeper_id": "111", "name": "Player A", "position": "WR"}
B = {"sleeper_id": "222", "name": "Player B", "position": "WR"}


def _pred(**kw):
    base = {"id": "p1", "league_id": "L1", "user_id": "u1", "kind": "start_sit", "season": 2026, "week": 4, "horizon_weeks": 1,
            "pick": [A], "over": [B], "factors": {"matchup": "major", "recent_usage": "minor"}, "confidence": 0.65,
            "rationale": "A has the softer matchup.", "status": "open"}
    base.update(kw)
    return base


class GradingTests(unittest.TestCase):
    def test_league_points_uses_league_scoring(self):
        self.assertEqual(league_points({"rec": 5, "rec_yd": 80, "rec_td": 1, "pts_ppr": 99}, SCORING), 19.0)
        self.assertEqual(league_points(None, SCORING), 0.0)

    def test_start_sit_wrong_when_alternative_outscores(self):
        pts = {("111", 4): 8.0, ("222", 4): 21.5}
        out = grade(_pred(), lambda sid, w: pts.get((sid, w), 0.0))
        self.assertFalse(out["correct"])
        self.assertEqual(out["margin"], -13.5)
        self.assertEqual(out["result"]["players"]["222"]["weeks"][4], 21.5)

    def test_trade_decline_is_right_when_received_side_scores_less(self):
        pts = {("111", w): 5.0 for w in range(4, 8)} | {("222", w): 12.0 for w in range(4, 8)}
        out = grade(_pred(kind="trade", verdict="decline", horizon_weeks=4), lambda sid, w: pts.get((sid, w), 0.0))
        self.assertEqual(out["pick_points"], 20.0)
        self.assertEqual(out["over_points"], 48.0)
        self.assertTrue(out["correct"])

    def test_unresolved_players_are_ungradeable(self):
        self.assertIsNone(grade(_pred(over=[{"name": "Mystery", "sleeper_id": None}]), lambda sid, w: 0.0))

    def test_due_only_after_last_week_is_final(self):
        self.assertFalse(is_due(_pred(kind="pickup", horizon_weeks=3), 5))
        self.assertTrue(is_due(_pred(kind="pickup", horizon_weeks=3), 6))

    def test_scorecard_by_kind_factor_cause_and_calibration(self):
        rows = [
            {"kind": "start_sit", "factors": {"matchup": "major"}, "confidence": 0.75, "correct": False, "cause": "usage_shift"},
            {"kind": "start_sit", "factors": {"matchup": "major"}, "confidence": 0.72, "correct": False, "cause": "fluke"},
            {"kind": "start_sit", "factors": {"recent_usage": "major"}, "confidence": 0.71, "correct": True},
            {"kind": "pickup", "factors": {"recent_usage": "major"}, "confidence": 0.6, "correct": True},
        ]
        card = scorecard(rows)
        self.assertEqual(card["by_kind"]["start_sit"], "1-2 (33%)")
        self.assertEqual(card["by_factor"]["matchup"], "0-2 (0%)")
        self.assertEqual(card["by_factor"]["recent_usage"], "2-0 (100%)")
        self.assertEqual(card["by_cause"], {"usage_shift": "1 misses", "fluke": "1 misses"})
        self.assertEqual(card["calibration"]["70-80%"], "right 33% of 3")

    def test_record_by_skips_ungraded(self):
        self.assertEqual(record_by([{"kind": "x", "correct": None}], lambda r: r["kind"]), {})


class LedgerTests(unittest.TestCase):
    def _ledger(self, db):
        lookup = {"player a": {"sleeper_id": "111", "full_name": "Player A", "position": "WR"}}
        return Ledger(db, league_id="L1", user_id="u1", league_team_id="T1", season=2026, week=5,
                      resolve_player=lambda n: lookup.get(n.lower()), conversation_id=lambda: "c1")

    def test_logs_resolved_call_with_defaults(self):
        db = MemDB()
        out = self._ledger(db).log_prediction("start_sit", ["Player A"], ["Nobody Known"], "usage", {"recent_usage": "major", "vibes": "major"}, 0.7)
        self.assertTrue(out["logged"])
        self.assertIn("Nobody Known", out["warning"])
        row = db.tables["league_ai_predictions"][0]
        self.assertEqual(row["week"], 5)
        self.assertEqual(row["horizon_weeks"], 1)
        self.assertEqual(row["pick"][0]["sleeper_id"], "111")
        self.assertEqual(row["factors"], {"recent_usage": "major"})
        self.assertEqual(row["conversation_id"], "c1")

    def test_repeat_is_skipped_and_reversal_voids_the_old_call(self):
        db = MemDB()
        ledger = self._ledger(db)
        lookup_b = {"player b": {"sleeper_id": "222", "full_name": "Player B"}}
        ledger.resolve_player = lambda n: {"player a": {"sleeper_id": "111", "full_name": "Player A"}, **lookup_b}.get(n.lower())
        self.assertTrue(ledger.log_prediction("start_sit", ["Player A"], ["Player B"], "x", {}, 0.6)["logged"])
        self.assertFalse(ledger.log_prediction("start_sit", ["Player A"], ["Player B"], "again", {}, 0.7)["logged"])
        self.assertTrue(ledger.log_prediction("start_sit", ["Player B"], ["Player A"], "flipped", {}, 0.55)["logged"])
        statuses = [r["status"] for r in db.tables["league_ai_predictions"]]
        self.assertEqual(statuses, ["void", "open"])
        self.assertEqual(db.tables["league_ai_predictions"][0]["status"], "void")

    def test_rejects_ungradeable_calls(self):
        ledger = self._ledger(MemDB())
        self.assertIn("error", ledger.log_prediction("start_sit", ["Player A"], [], "x", {}, 0.6))
        self.assertIn("error", ledger.log_prediction("trade", ["Player A"], ["Player A"], "x", {}, 0.6))
        self.assertIn("error", ledger.log_prediction("vibes", ["Player A"], ["Player A"], "x", {}, 0.6))

    def test_tool_specs(self):
        names = [t.name for t in self._ledger(MemDB()).tools()]
        self.assertEqual(names, ["log_prediction", "get_my_track_record"])


class ModelStepTests(unittest.TestCase):
    def test_diagnose_parses_and_sanitizes(self):
        seen = {}

        def ask(system, prompt, web):
            seen["web"], seen["prompt"] = web, prompt
            return 'Here: {"cause": "usage_shift", "process_error": true, "diagnosis": "B ran 92% of routes.", "factor_review": {"matchup": "misled"}, "lesson": "Weight route share over matchup for WR2s."}'

        out = diagnose(_pred(), {"pick_points": 8.0, "over_points": 21.5, "correct": False}, "- facts", ask, web_search=True)
        self.assertEqual(out["cause"], "usage_shift")
        self.assertTrue(out["process_error"])
        self.assertEqual(out["factor_review"], {"matchup": "misled"})
        self.assertTrue(seen["web"])
        self.assertIn("WRONG", seen["prompt"])
        self.assertIn("search the web", seen["prompt"])

    def test_diagnose_null_lesson_and_bad_json(self):
        out = diagnose(_pred(), {"pick_points": 20, "over_points": 3, "correct": True}, "", lambda *a: '{"cause": "nonsense", "lesson": null}', web_search=False)
        self.assertIsNone(out["cause"])
        self.assertIsNone(out["lesson"])
        self.assertIsNone(diagnose(_pred(), {"pick_points": 1, "over_points": 2, "correct": False}, "", lambda *a: "no json", web_search=False))

    def test_consolidate_keeps_valid_items(self):
        text = json.dumps([
            {"id": "x1", "lesson": "Trust route share.", "kind": "start_sit", "factor": "recent_usage", "evidence_count": 3, "active": True},
            {"id": None, "lesson": "", "kind": None},
            {"id": None, "lesson": "Discount wind under 15 mph.", "kind": "bogus", "factor": "weather", "active": False},
        ])
        out = consolidate([], [], {}, lambda *a: text)
        self.assertEqual(len(out), 2)
        self.assertIsNone(out[1]["kind"])
        self.assertFalse(out[1]["active"])


class FactsAndPackTests(unittest.TestCase):
    def test_diagnostic_facts_include_usage_baseline_game_and_practice(self):
        db = MemDB({
            "nfl_player_stats": [
                {"sleeper_id": "111", "season": 2026, "season_type": "REG", "week": w, "team": "CIN", "opponent": "BAL", "is_home": True,
                 "offense_pct": 0.9, "targets": 9, "receptions": 6, "receiving_yards": 80, "receiving_tds": 0, "target_share": 0.28, "fantasy_points_ppr": 14.0}
                for w in (1, 2, 3)
            ] + [{"sleeper_id": "111", "season": 2026, "season_type": "REG", "week": 4, "team": "CIN", "opponent": "BAL", "is_home": True,
                  "offense_pct": 0.55, "targets": 3, "receptions": 2, "receiving_yards": 20, "receiving_tds": 0, "target_share": 0.1, "fantasy_points_ppr": 4.0}],
            "nfl_games": [{"season": 2026, "week": 4, "home_team": "CIN", "away_team": "BAL", "home_score": 10, "away_score": 31, "spread_line": 1.5,
                           "total_line": 48.5, "roof": "outdoors", "temp": 41.0, "wind": 22.0, "home_coach": "Zac Taylor", "away_coach": "John Harbaugh",
                           "home_qb_name": "Joe Burrow", "away_qb_name": "Lamar Jackson"}],
            "nfl_practice_reports": [{"sleeper_id": "111", "season": 2026, "week": 4, "report_status": "Questionable", "report_injury": "Hamstring", "practice_status": "Limited"}],
        })
        text = diagnostic_facts(db, _pred())
        self.assertIn("55% snaps", text)
        self.assertIn("prior-4-week avg: 90% snaps, 9.0 tgt, 14.0 PPR over 3 games", text)
        self.assertIn("CIN 10-31 final, CIN favored by 1.5", text)
        self.assertIn("wind 22 mph", text)
        self.assertIn("Questionable, Hamstring, Limited", text)
        self.assertIn("PASSED ON Player B week 4: no stat line", text)

    def test_pack_lines(self):
        db = MemDB({
            "league_ai_scorecards": [{"league_id": "L1", "season": 2026, "stats": {"by_kind": {"start_sit": "6-4 (60%)"}, "by_factor": {"matchup": "1-4 (20%)"}}}],
            "league_ai_lessons": [{"league_id": "L1", "active": True, "lesson": "Trust route share over matchup.", "kind": "start_sit", "factor": "recent_usage", "evidence_count": 4, "hits": 0, "misses": 0}],
            "league_ai_predictions": [{"league_id": "L1", "user_id": "u1", "season": 2026, "status": "graded", "kind": "start_sit", "correct": True}],
        })
        lines = learning_pack_lines(db, "L1", "u1", 2026)
        self.assertIn("start_sit 6-4 (60%)", lines[0])
        self.assertIn("matchup 1-4 (20%)", lines[1])
        self.assertIn("Lesson [start_sit/recent_usage] (seen 4x): Trust route share over matchup.", lines[2])
        self.assertIn("start_sit 1-0 (100%)", lines[3])


class JobTests(unittest.TestCase):
    def _db(self):
        games = [{"season": 2026, "game_type": "REG", "week": w, "home_score": (None if w == 5 else 20)} for w in (3, 4, 5) for _ in range(2)]
        return MemDB({
            "nfl_games": games,
            "league_ai_settings": [{"league_id": "L1", "scoring_settings": SCORING}],
            "league_ai_predictions": [_pred(), _pred(id="p2", week=5), _pred(id="p3", over=[{"name": "?", "sleeper_id": None}])],
            "league_ai_lessons": [{"id": "old", "league_id": "L1", "active": True, "lesson": "Old lesson.", "evidence_count": 1}],
            "league_ai_scorecards": [],
        })

    def test_completed_week_stops_at_unfinished_week(self):
        self.assertEqual(completed_week(self._db(), 2026), 4)

    def test_run_grades_due_calls_and_rewrites_playbook(self):
        db = self._db()
        stats = WeeklyStats(get_json=lambda url: {"111": {"rec": 2, "rec_yd": 20}, "222": {"rec": 7, "rec_yd": 110, "rec_td": 1}})
        seen = []

        def runner(reqs):
            seen.append([(r.id, r.model, r.web_search, r.feature) for r in reqs])
            out = {}
            for r in reqs:
                if r.feature == "playbook":
                    text = json.dumps([{"id": None, "lesson": "Weight route share over matchup for WRs.", "kind": "start_sit", "factor": "recent_usage", "evidence_count": 2, "active": True}])
                else:
                    text = '{"cause": "usage_shift", "process_error": true, "diagnosis": "B saw 11 targets.", "lesson": "Weight route share over matchup."}'
                out[r.id] = Res(text, {"input_tokens": 10000, "output_tokens": 2000}, batch=True)
            return out

        summary = run(db, runner, now=datetime(2026, 10, 6, tzinfo=timezone.utc), stats=stats, main_model="claude-sonnet-5-5")
        preds = {p["id"]: p for p in db.tables["league_ai_predictions"]}
        self.assertEqual(summary["graded"], 1)
        self.assertEqual(preds["p1"]["status"], "graded")
        self.assertFalse(preds["p1"]["correct"])
        self.assertEqual(preds["p1"]["pick_points"], 4.0)
        self.assertEqual(preds["p1"]["over_points"], 24.0)
        self.assertEqual(preds["p1"]["cause"], "usage_shift")
        self.assertEqual(preds["p2"]["status"], "open")  # week 5 not final yet
        self.assertEqual(preds["p3"]["status"], "ungradeable")
        self.assertEqual(seen[0], [("p1", "claude-sonnet-5-5", True, "grading")])  # the miss: main model + search
        self.assertEqual(seen[1][0][3], "playbook")
        lessons = {l["lesson"]: l for l in db.tables["league_ai_lessons"]}
        self.assertFalse(lessons["Old lesson."]["active"])
        self.assertTrue(lessons["Weight route share over matchup for WRs."]["active"])
        self.assertEqual(db.tables["league_ai_scorecards"][0]["stats"]["by_kind"]["start_sit"], "0-1 (0%)")
        usage = db.tables["league_ai_usage"]
        self.assertEqual([u["feature"] for u in usage], ["grading", "playbook"])
        self.assertTrue(all(u["batch"] for u in usage))
        self.assertAlmostEqual(usage[0]["cost_usd"], (10000 * 2 + 2000 * 10) / 1e6 / 2)  # half price
        self.assertAlmostEqual(summary["cost_usd"], 0.04)

    def test_correct_calls_are_reviewed_on_haiku_without_search(self):
        db = self._db()
        stats = WeeklyStats(get_json=lambda url: {"111": {"rec": 9, "rec_yd": 120}, "222": {"rec": 1, "rec_yd": 5}})
        seen = []

        def runner(reqs):
            seen.extend((r.model, r.web_search, r.feature) for r in reqs)
            return {r.id: Res('{"cause": "fluke", "lesson": null}' if r.feature != "playbook" else "[]") for r in reqs}

        run(db, runner, stats=stats, main_model="claude-sonnet-5-5")
        self.assertEqual(seen[0], ("claude-haiku-4-5", False, "grading_review"))

    def test_dry_run_writes_nothing(self):
        db = self._db()
        stats = WeeklyStats(get_json=lambda url: {})
        run(db, lambda reqs: {}, dry_run=True, stats=stats, main_model="claude-sonnet-5-5")
        self.assertTrue(all(p["status"] == "open" for p in db.tables["league_ai_predictions"]))
        self.assertEqual(db.tables["league_ai_scorecards"], [])


if __name__ == "__main__":
    unittest.main()
