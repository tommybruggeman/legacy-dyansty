import unittest

from league_ai.answer_eval import candidates, plan, rubric, summarize


class AnswerEvalTests(unittest.TestCase):
    def test_rubric(self):
        good = "Start A over B: A projects 14.2 to B's 11.0, and B's matchup is the 3rd toughest."
        r = rubric("start_sit", good, ["compare_players"])
        self.assertTrue(all(r.values()), r)
        bad = "# Verdict\nStart A."
        r = rubric("start_sit", bad, [])
        self.assertFalse(r["no_headings"] or r["used_engine"] or r["quotes_projections"])
        t = rubric("trade_find", "Open with X for Y; walk away after adding Z.", ["find_trade_targets"])
        self.assertTrue(t["has_ladder"] and t["used_finder"])

    def test_plan_spreads_questions_and_summary_counts(self):
        mine = {"team_id": "a", "owner": "A", "weakest_slot": "WR", "week_lineup": [{"id": "1", "name": "P1", "pos": "WR", "pts": 12}],
                "week_bench_detail": [{"id": "2", "name": "P2", "pos": "WR", "pts": 11}]}
        other = {"team_id": "b", "owner": "B", "week_lineup": [{"id": "3", "name": "P3", "pos": "WR", "pts": 20}]}
        per_team = [("owner-a", candidates(mine, [mine, other])), ("owner-b", candidates({**other, "week_bench_detail": []}, [mine, other]))]
        queue = plan(per_team)
        kinds = [k for _, k, _ in queue]
        self.assertIn("start_sit", kinds)
        self.assertIn("trade_target", kinds)
        items = [{"kind": "start_sit", "answers": [{"model": "s", "ok": True, "facts_wrong": 0, "rubric": {"a": True, "b": False}, "cost": 0.1, "seconds": 9}]},
                 {"kind": "trade_find", "winner": "o", "answers": [{"model": "s", "ok": True, "facts_wrong": 2, "rubric": {"a": True}, "cost": 0.2, "seconds": 20},
                                                                  {"model": "o", "ok": True, "facts_wrong": 0, "rubric": {"a": True}, "cost": 0.3, "seconds": 25}]}]
        out = summarize(items)
        self.assertEqual(out["s"]["clean_share"], 0.5)
        self.assertEqual(out["s"]["wrong_per_answer"], 1.0)
        self.assertEqual(out["pairwise_wins"], {"o": 1})
        self.assertEqual(out["o"]["hard_only"]["answers"], 1)
