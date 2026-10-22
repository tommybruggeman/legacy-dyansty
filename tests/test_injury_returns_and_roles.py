import json
import unittest
from datetime import datetime, timezone

from league_ai import injury_outlook as io
from league_ai.learning import Ledger, play_calibration, scorecard
from league_ai.learning_job import Res, WeeklyStats, run
from league_ai.nfl_data import _CACHE
from league_ai.tools.nfl import build_registry
from pipeline.nfl_data.injury_history import build_episodes, build_play_rates, injury_key
from tests.test_league_ai_learning import SCORING, MemDB, _pred
from tests.test_league_ai_tools import FakeQuery


def _stat(gsis, name, team, season, week, pos="RB", snaps=30, pct=0.5, car=10, tgt=2, ppr=10.0, season_type="REG"):
    return {"gsis_id": gsis, "player_name": name, "team": team, "season": season, "week": week, "position": pos, "season_type": season_type,
            "offense_snaps": snaps, "offense_pct": pct, "carries": car, "targets": tgt, "attempts": 0, "rushing_yards": car * 4, "fantasy_points_ppr": ppr}


def _report(gsis, season, week, injury, status, practice="Did Not Participate In Practice", pos="RB", team="SEA"):
    return {"gsis_id": gsis, "season": season, "week": week, "season_type": "REG", "position": pos, "team": team,
            "report_primary_injury": injury, "practice_primary_injury": injury, "report_status": status, "practice_status": practice}


def _season(team, season, weeks, lead, lead_name, backup, backup_name, skip_lead=(), backup_ppr=6.0):
    rows = []
    for w in weeks:
        if w not in skip_lead:
            rows.append(_stat(lead, lead_name, team, season, w, snaps=45, pct=0.65, car=15, tgt=3, ppr=15.0))
        bump = w in skip_lead
        rows.append(_stat(backup, backup_name, team, season, w, snaps=40 if bump else 20, pct=0.6 if bump else 0.3, car=16 if bump else 5, tgt=3 if bump else 1,
                          ppr=14.0 if bump else backup_ppr))
    return rows


class InjuryHistoryBuildTests(unittest.TestCase):
    def test_injury_key_normalises_and_skips_non_injuries(self):
        self.assertEqual(injury_key("Right Knee, Ankle"), "knee")
        self.assertEqual(injury_key("Gameday Concussion Protocol Evaluation"), "concussion")
        self.assertIsNone(injury_key("Not injury related - resting player"))
        self.assertEqual(io.injury_key("Knee (ACL)"), "knee")

    def test_episode_spans_seasons_and_records_return(self):
        stats = _season("SEA", 2025, range(1, 19), "g1", "Lead Back", "g2", "Backup", skip_lead=(16, 17, 18))
        stats += _season("SEA", 2026, range(1, 7), "g1", "Lead Back", "g2", "Backup", skip_lead=(1, 2, 3))
        reports = [_report("g1", 2025, w, "Knee", "Out") for w in (16, 17, 18)] + [_report("g1", 2026, w, "Knee", "Out") for w in (1, 2, 3)]
        eps = build_episodes(stats, reports, current_season=2026)
        ep = next(e for e in eps if e["gsis_id"] == "g1")
        self.assertEqual((ep["last_game_season"], ep["last_game_week"]), (2025, 15))
        self.assertEqual((ep["return_season"], ep["return_week"]), (2026, 4))
        self.assertEqual(ep["games_missed"], 6)
        self.assertEqual(ep["injury"], "knee")
        self.assertTrue(ep["returned"])
        self.assertAlmostEqual(ep["pre_ppr"], 15.0)
        self.assertAlmostEqual(ep["post1_ppr"], 15.0)

    def test_unlabelled_gap_is_skipped_and_open_gap_is_kept(self):
        stats = _season("SEA", 2026, range(1, 7), "g1", "Lead Back", "g2", "Backup", skip_lead=(5, 6))
        stats += [_stat("g3", "Benched Guy", "SEA", 2026, w) for w in (1, 4)]  # missed 2-3 with no report
        eps = build_episodes(stats, [_report("g1", 2026, 5, "Hamstring", "Out")], current_season=2026)
        self.assertEqual([(e["gsis_id"], e["returned"], e["games_missed"]) for e in eps], [("g1", False, 2)])

    def test_play_rates_count_played_weeks_by_tag(self):
        stats = []
        reports = []
        for w in range(1, 11):
            stats.append(_stat("q1", "Q Guy", "SEA", 2026, w))
            stats.append(_stat("x1", "Teammate", "SEA", 2026, w))
        for w in range(1, 11):
            reports.append(_report("q1", 2026, w, "Ankle", "Questionable", "Limited Participation in Practice"))
        reports.append(_report("o1", 2026, 3, "Ankle", "Out"))  # never played: not a relevant player, left out
        rates = {(r["scope"], r["game_status"], r["practice"]): r for r in build_play_rates(stats, reports)}
        self.assertEqual(rates[("pos:RB", "Questionable", "Limited")]["rate"], 1.0)
        self.assertEqual(rates[("all", "Questionable", "")]["listings"], 10)
        self.assertEqual(rates[("next:all", "Questionable", "")]["listings"], 9)  # week 10 has no next game
        self.assertNotIn(("all", "Out", ""), rates)


class OutlookMathTests(unittest.TestCase):
    EPS = [{"gsis_id": f"c{i}", "player_name": f"Comp {i}", "position": "RB", "injury": "knee", "returned": True, "games_missed": gm, "return_season": 2025,
            "pre_snap_pct": 0.6, "pre_opps": 15, "pre_ppr": 14.0, "post1_snap_pct": 0.4, "post1_opps": 10, "post1_ppr": 10.0, "post3_snap_pct": 0.5, "post3_opps": 13, "post3_ppr": 12.0}
           for i, gm in enumerate([1, 2, 2, 3, 6, 7, 8, 8, 9, 10])]

    def test_typical_absence_and_similar_layoff_comps(self):
        t = io.typical_absence(self.EPS, "RB", "knee")
        self.assertEqual(t["absences"], 10)
        self.assertEqual(t["median_games_missed"], 6.5)
        comps = io.return_comps(self.EPS, "RB", "knee", 8)
        self.assertEqual(comps["comps"], 6)  # 4-12 games missed
        self.assertEqual(comps["first_game_vs_before"]["opportunities"]["median"], 0.67)

    def test_projection_scales_by_comps_and_team_volume(self):
        comps = io.return_comps(self.EPS, "RB", "knee", 8)
        proj = io.project_return({"pre_opps": 15, "pre_ppr": 15.0}, comps, {"opps_pg": 30, "ppr_per_opp": 1.0}, {"opps_pg": 30, "ppr_per_opp": 0.8})
        self.assertAlmostEqual(proj["first_game"]["opportunities"], 10.0, delta=0.15)  # 15 old opportunities x comps' 0.67
        self.assertAlmostEqual(proj["first_game"]["ppr"], 10.0, delta=0.15)  # x 1.0 PPR per opportunity

    def test_comment_signal_reads_coach_quote(self):
        sig = io.comment_signal("Smith (knee) was limited Wednesday.", "Coach Jones said Smith is expected to play Sunday.")
        self.assertEqual(sig["lean"], "positive")
        self.assertIn("Coach Jones said", sig["coach_or_beat_quote"])
        self.assertEqual(io.comment_signal("He was ruled out for Sunday.")["lean"], "negative")

    def test_play_chance_uses_official_rate_then_news(self):
        rates = {("pos:RB", "Questionable", "Limited"): {"rate": 0.6, "listings": 200}, ("next:all", "Out", ""): {"rate": 0.33, "listings": 700}}
        base = io.play_chance(position="RB", injury="ankle", official_status="Questionable", official_practice="Limited Participation in Practice", sleeper_status=None,
                              sleeper_practice=None, espn_status=None, return_date=None, game_date="2026-10-25", comments=None, rates=rates)
        self.assertEqual(base["probability"], 0.6)
        good = io.play_chance(position="RB", injury="ankle", official_status="Questionable", official_practice="Limited Participation in Practice", sleeper_status=None,
                              sleeper_practice=None, espn_status=None, return_date=None, game_date="2026-10-25", comments={"lean": "positive"}, rates=rates)
        self.assertGreater(good["probability"], 0.6)
        early = io.play_chance(position="RB", injury="ankle", official_status=None, official_practice=None, sleeper_status="Out", sleeper_practice="DNP",
                               espn_status=None, return_date="2026-11-08", game_date="2026-10-25", comments=None, rates=rates)
        self.assertLess(early["probability"], 0.33)
        out = io.play_chance(position="RB", injury="ankle", official_status="Out", official_practice=None, sleeper_status=None, sleeper_practice=None,
                             espn_status=None, return_date=None, game_date=None, comments={"lean": "positive"}, rates=rates)
        self.assertLessEqual(out["probability"], 0.02)

    def test_room_norms_and_with_without(self):
        rows = [r for r in _season("CHI", 2026, range(1, 7), "s1", "Starter", "b1", "Backup", skip_lead=(5, 6), backup_ppr=4.0)]
        weeks = io.room_weeks(rows)
        players = {p["name"]: p for p in io.room_players(weeks)}
        self.assertEqual(players["Backup"]["recent"]["slots"], [2, 1, 1])
        ww = io.with_without(weeks, "b1", "s1")
        self.assertEqual(ww["with"]["games"], 4)
        self.assertGreater(ww["without"]["share_of_work"], ww["with"]["share_of_work"])
        norms = io.role_norms(weeks)
        self.assertIn("on par", io.compare_to_norm(norms["2"]["share_of_work"], norms["2"]))


class Client:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        q = FakeQuery(self.tables.get(name, []))
        q.range = lambda a, b: q  # one page is all the fixtures need
        return q


class ToolTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()
        stats = _season("SEA", 2025, range(1, 19), "g1", "Lead Back", "g2", "Backup", skip_lead=(16, 17, 18))
        stats += _season("SEA", 2026, range(1, 7), "g1", "Lead Back", "g2", "Backup", skip_lead=range(1, 7))
        stats += _season("CHI", 2026, range(1, 7), "s1", "Starter", "b1", "Roschon Johnson", skip_lead=(5, 6), backup_ppr=4.0)
        reports = [_report("g1", 2025, w, "Knee", "Out") for w in (16, 17, 18)] + [_report("g1", 2026, w, "Knee", "Out") for w in range(1, 7)]
        games = [{"season": 2026, "game_type": "REG", "week": w, "home_team": h, "away_team": a, "gameday": f"2026-10-{10 + w}", "home_score": None if w >= 7 else 20}
                 for w in range(1, 9) for h, a in (("SEA", "ARI"), ("CHI", "GB"))]
        eps = build_episodes(stats, reports, current_season=2026) + OutlookMathTests.EPS
        self.client = Client({
            "nfl_players": [
                {"sleeper_id": "p1", "full_name": "Lead Back", "search_name": "lead back", "position": "RB", "team": "SEA", "gsis_id": "g1", "injury_status": "Questionable", "search_rank": 1},
                {"sleeper_id": "p2", "full_name": "Roschon Johnson", "search_name": "roschon johnson", "position": "RB", "team": "CHI", "gsis_id": "b1", "search_rank": 2},
                {"sleeper_id": "p3", "full_name": "Starter", "search_name": "starter", "position": "RB", "team": "CHI", "gsis_id": "s1", "injury_status": "Out", "search_rank": 3},
            ],
            "nfl_player_stats": stats, "nfl_games": games, "nfl_injury_episodes": eps, "nfl_injury_play_rates": [r for r in build_play_rates(stats, reports)],
            "nfl_injuries": [{"sleeper_id": "p1", "status": "Questionable", "injury_type": "Knee", "return_date": "2026-10-17", "short_comment": "Coach said Lead Back will play Sunday."}],
            "nfl_practice_reports": [], "nfl_market_values": [], "nfl_situational_splits": [],
        })
        self.reg = build_registry(self.client, league_state={"roster": [], "teams": []})

    def test_return_outlook(self):
        out = json.loads(self.reg.call("get_return_outlook", {"name": "Lead Back"}))
        self.assertEqual(out["absence"]["games_missed"], 9)
        self.assertEqual(out["typical_time_missed"]["absences"], 10)
        self.assertEqual(out["role_before_injury"]["that_season"]["usual_slot"], "RB1")
        self.assertEqual(out["who_filled_in"][0]["name"], "Backup")
        self.assertIn("first_game", out["projection_ppr"])
        self.assertIn("news leans positive", " ".join(out["chance_to_play_this_week"]["factors"]))

    def test_position_room_judges_backup_against_norms(self):
        out = json.loads(self.reg.call("get_position_room", {"name": "Roschon Johnson"}))
        self.assertEqual(out["team_games"], 6)
        self.assertIn("RB1", out["slot_norms"]["this_season"])
        player = out["player"]
        self.assertIn("Starter", player["with_and_without"])
        self.assertEqual(player["with_and_without"]["Starter"]["his_status"], "Out")
        self.assertTrue(player["vs_this_team_this_season"].startswith("RB"))

    def test_injury_report_has_chance_to_play(self):
        out = json.loads(self.reg.call("get_injury_report", {"name": "Lead Back"}))
        self.assertIn("chance_to_play", out["chance_to_play_this_week"])


class LearningHookTests(unittest.TestCase):
    def test_ledger_stores_play_chance_on_player(self):
        db = MemDB()
        people = {"player a": {"sleeper_id": "111", "full_name": "Player A"}, "player b": {"sleeper_id": "222", "full_name": "Player B"}}
        ledger = Ledger(db, league_id="L1", user_id="u1", league_team_id="T1", season=2026, week=4, resolve_player=lambda n: people.get(n.lower()))
        out = ledger.log_prediction("start_sit", ["Player A"], ["Player B"], "back from injury", {"injury_return": "major"}, 0.6, play_chances={"Player A": 0.7})
        self.assertTrue(out["logged"])
        row = db.tables["league_ai_predictions"][0]
        self.assertEqual(row["pick"][0]["play_prob"], 0.7)
        self.assertEqual(row["factors"], {"injury_return": "major"})

    def test_grading_checks_play_chances_and_scorecard_calibrates(self):
        a = {"name": "Player A", "sleeper_id": "111", "play_prob": 0.7}
        db = MemDB({
            "nfl_games": [{"season": 2026, "game_type": "REG", "week": 4, "home_score": 20}],
            "league_ai_settings": [{"league_id": "L1", "scoring_settings": SCORING}],
            "league_ai_predictions": [_pred(pick=[a])], "league_ai_lessons": [], "league_ai_scorecards": [],
        })
        stats = WeeklyStats(get_json=lambda url: {"111": {"gp": 1, "rec": 2}, "222": {"gp": 1, "rec": 5}})
        runner = lambda reqs: {r.id: Res('{"cause": "injury_status", "process_error": false, "diagnosis": "x", "lesson": null}' if r.feature != "playbook" else "[]") for r in reqs}
        run(db, runner, now=datetime(2026, 10, 6, tzinfo=timezone.utc), stats=stats, main_model="m")
        pred = db.tables["league_ai_predictions"][0]
        self.assertEqual(pred["result"]["play_checks"], [{"name": "Player A", "play_prob": 0.7, "played": True}])
        self.assertEqual(play_calibration([pred]), {"60-85%": "played 100% of 1"})
        self.assertIn("play_calibration", scorecard([pred]))


if __name__ == "__main__":
    unittest.main()
