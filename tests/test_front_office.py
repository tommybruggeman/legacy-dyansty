import json
import unittest

from league_ai.front_office import brief_facts, build_player_strengths, build_tiles, current_opponent, generate_brief, head_to_head, next_year_space, parse_brief, power_rankings, season_table, team_record
from league_ai.power import compute_power, fill_lineup, player_strength, tier_points
from league_ai.tools import ToolRegistry
from tests.fixtures.league_ai_state import CHASE, DYLAN, LEAGUE_ID, SEASON, TOMMY, fixture_standings, fixture_state
from tests.test_league_ai_tools import FakeClient
import tests.test_league_history  # noqa: F401  (registers league_matchups fixture rows)


class TierTests(unittest.TestCase):
    def test_tier_points_monotonic(self):
        self.assertGreater(tier_points("RB", 1), tier_points("RB", 12))
        self.assertGreater(tier_points("RB", 12), tier_points("RB", 40))
        self.assertEqual(tier_points("RB", 200), 5.0)
        self.assertIsNone(tier_points("RB", None))

    def test_player_strength_blends_and_flags(self):
        row = {"player_name": "X", "pos": "RB", "league_team_id": TOMMY, "owner_name": "Tommy", "sleeper_player_id": "p", "contract_years_left": 3}
        healthy = player_strength(row, recent_ppg=20, season_ppg=18, pos_rank=5, age=24, injury_status=None)
        self.assertTrue(healthy.available)
        self.assertAlmostEqual(healthy.now, 0.5 * 20 + 0.3 * 18 + 0.2 * tier_points("RB", 5), places=1)
        out = player_strength(row, recent_ppg=20, season_ppg=18, pos_rank=5, age=24, injury_status="Out")
        self.assertFalse(out.available)
        old = player_strength(row, recent_ppg=20, season_ppg=18, pos_rank=5, age=31, injury_status=None)
        self.assertLess(old.dynasty, healthy.dynasty)
        q = player_strength(row, recent_ppg=20, season_ppg=18, pos_rank=5, age=24, injury_status="Questionable")
        self.assertLess(q.now, healthy.now)


def _p(sid, name, pos, team, now, dyn=None, available=True):
    from league_ai.power import PlayerStrength
    return PlayerStrength(sleeper_id=sid, name=name, position=pos, team_id=team, owner=team, now=now, dynasty=dyn if dyn is not None else now, available=available)


class LineupTests(unittest.TestCase):
    def test_fill_lineup_respects_slots_and_flex(self):
        players = [_p("1", "QB1", "QB", "t", 25), _p("2", "QB2", "QB", "t", 18), _p("3", "RB1", "RB", "t", 20), _p("4", "RB2", "RB", "t", 12),
                   _p("5", "WR1", "WR", "t", 19), _p("6", "WR2", "WR", "t", 15), _p("7", "WR3", "WR", "t", 14), _p("8", "TE1", "TE", "t", 9), _p("9", "RB3", "RB", "t", 6, available=False)]
        lineup, bench = fill_lineup(players, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"])
        slots = [s for s, _ in lineup]
        self.assertEqual(slots, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"])
        self.assertEqual(dict(lineup)["FLEX"].name, "WR3")
        self.assertEqual(dict(lineup)["SUPER_FLEX"].name, "QB2")
        self.assertNotIn("RB3", [p.name for p in bench])  # unavailable players excluded entirely

    def test_compute_power_ranks_and_weakest(self):
        teams = [{"league_team_id": "a", "owner_name": "A"}, {"league_team_id": "b", "owner_name": "B"}, {"league_team_id": "c", "owner_name": "C"}]
        players = []
        for t, qb, rb, wr in (("a", 25, 20, 18), ("b", 20, 10, 17), ("c", 15, 15, 8)):
            players += [_p(f"{t}q", "q", "QB", t, qb), _p(f"{t}r1", "r1", "RB", t, rb), _p(f"{t}r2", "r2", "RB", t, rb - 2), _p(f"{t}w1", "w1", "WR", t, wr), _p(f"{t}w2", "w2", "WR", t, wr - 1), _p(f"{t}te", "te", "TE", t, 8)]
        out = compute_power(players, teams, ["QB", "RB", "RB", "WR", "WR", "TE", "BN"])
        by = {tp.owner: tp for tp in out}
        self.assertEqual(by["A"].now_rank, 1)
        self.assertEqual(by["B"].weakest_slot, "RB")
        self.assertEqual(by["C"].weakest_slot, "WR")
        self.assertTrue(by["B"].weakest_note.startswith("-"))


class TilesTests(unittest.TestCase):
    def test_next_year_space(self):
        self.assertEqual(next_year_space(fixture_state(), TOMMY, 225), 225 - (60 + 45 + 12 + 4))  # players with >=2 years

    def test_tiles_shape_and_order(self):
        state = fixture_state()
        rankings = power_rankings(FakeClient(), state, SEASON, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"])
        tiles = build_tiles(state=state, team_id=TOMMY, owner="Tommy Bruggeman", salary_cap=225, standings=fixture_standings(), rankings=rankings,
                            opponent={"roster_id": 2, "owner_name": "Chase Seyforth", "week": 4}, h2h={"games": 4, "wins": 3, "losses": 1}, movement=1)
        self.assertEqual(len(tiles), 6)
        self.assertEqual(tiles[0]["value"], "2-1")
        self.assertIn("2nd of 3", tiles[0]["label"])
        self.assertIn("Weakest spot", tiles[1]["label"])
        self.assertEqual(tiles[2]["value"], "vs Seyforth")
        self.assertIn("Head to head 3-1", tiles[2]["label"])
        self.assertEqual(tiles[3]["value"], "$91.5")
        self.assertEqual(tiles[4]["value"], "$104")
        self.assertTrue(tiles[5]["value"].startswith("#"))
        self.assertIn("up 1", tiles[5]["label"])

    def test_record_and_h2h_helpers(self):
        self.assertEqual(team_record(fixture_standings(), "tommy bruggeman")["rank"], 2)
        h = head_to_head(FakeClient(), LEAGUE_ID, "Tommy Bruggeman", "Chase Seyforth")
        self.assertIn("games", h)

    def test_current_opponent(self):
        rows = [{"matchup_id": 1, "roster_id": 1, "points": 0}, {"matchup_id": 1, "roster_id": 2, "points": 0}, {"matchup_id": 2, "roster_id": 7, "points": 0}]
        opp = current_opponent("sl1", 4, {1: "Tommy", 2: "Chase", 7: "Dylan"}, 1, get_json=lambda url: rows)
        self.assertEqual(opp["owner_name"], "Chase")
        self.assertIsNone(current_opponent("sl1", 4, {}, None))


class BriefTests(unittest.TestCase):
    def test_parse_brief(self):
        text = 'Here you go:\n{"injuries": {"items": [], "call": "All healthy."}, "matchup": {"headline": "vs Chase", "lines": ["2-1"], "stakes": "Win and lead."}}'
        b = parse_brief(text)
        self.assertEqual(b["matchup"]["stakes"], "Win and lead.")
        self.assertIsNone(parse_brief("no json here"))

    def test_generate_brief_uses_fake_client(self):
        from tests.test_league_ai_service import CONFIG, FakeSDK, _resp, _text
        from league_ai.client import ClaudeClient
        payload = {"injuries": {"items": [{"player": "Hurt Guy", "status": "IR"}], "call": "Start X."}, "matchup": {"headline": "vs Chase Seyforth", "lines": ["3-0"], "stakes": "Big."}}
        sdk = FakeSDK([_resp([_text(json.dumps(payload))])])
        out = generate_brief(pack="PACK", tools=None, owner="Tommy", week=4, opponent="Chase Seyforth", facts="- INJURY x", client=ClaudeClient(CONFIG, sdk_client=sdk))
        self.assertEqual(out["injuries"]["items"][0]["player"], "Hurt Guy")
        self.assertIn("Owner: Tommy", sdk.calls[0]["messages"][0]["content"])
        self.assertIn("- INJURY x", sdk.calls[0]["messages"][0]["content"])
        self.assertNotIn("tools", sdk.calls[0])

    def test_brief_facts_and_season_table(self):
        state = fixture_state()
        table = season_table(FakeClient(), LEAGUE_ID, 2025)
        self.assertEqual(table[0]["Team"], "Tommy Bruggeman")
        self.assertEqual(table[0]["Wins"], 1)
        facts = brief_facts(FakeClient(), state=state, team_id=TOMMY, owner="Tommy Bruggeman", opponent={"owner_name": "Chase Seyforth", "week": 4},
                            h2h={"games": 2, "wins": 1, "losses": 1, "last": "2025 wk 2: L 90-110"}, standings=fixture_standings(), weakest_slot="RB", salary_cap=225)
        self.assertIn("INJURY Bijan Robinson: Out", facts)
        self.assertIn("ME Tommy Bruggeman: 2-1", facts)
        self.assertIn("HEAD TO HEAD vs Chase Seyforth: 1-1", facts)
        self.assertIn("CAP space $91.5", facts)
        self.assertIn("FREE AGENTS at weakest spot RB", facts)
        self.assertIn("Free Agent Back", facts)

    def test_power_blends_actual_ppg(self):
        from league_ai.power import compute_power
        teams = [{"league_team_id": "a", "owner_name": "A"}, {"league_team_id": "b", "owner_name": "B"}]
        players = [_p("aq", "q", "QB", "a", 20), _p("bq", "q", "QB", "b", 22)]
        proj = compute_power(players, teams, ["QB"])
        self.assertEqual(next(t for t in proj if t.owner == "B").now_rank, 1)
        blended = compute_power(players, teams, ["QB"], {"A": 140.0, "B": 100.0})
        self.assertEqual(next(t for t in blended if t.owner == "A").now_rank, 1)


if __name__ == "__main__":
    unittest.main()


class PromptRotationTests(unittest.TestCase):
    def test_rotating_prompts(self):
        from league_ai.front_office import rotating_prompts
        self.assertEqual(len(rotating_prompts(weekday=1, week=4)), 4)
        self.assertIn("Waiver targets and prices", rotating_prompts(weekday=1, week=4))
        self.assertIn("Injury check on my starters", rotating_prompts(weekday=4, week=4))
        self.assertIn("Find me a deadline trade", rotating_prompts(weekday=1, week=10))


class DataBriefTests(unittest.TestCase):
    def test_data_brief_is_instant_and_mergeable(self):
        from league_ai.front_office import data_brief, merge_brief
        state = fixture_state()
        b = data_brief(FakeClient(), state=state, team_id=TOMMY, owner="Tommy Bruggeman", opponent={"owner_name": "Chase Seyforth", "week": 4},
                       h2h={"games": 2, "wins": 1, "losses": 1, "last": "2025 wk 2: L 90-110"}, standings=fixture_standings())
        names = [i["player"] for i in b["injuries"]["items"]]
        self.assertEqual(names[0], "Bijan Robinson")  # Out sorts first
        self.assertIn("Hurt Guy", names)               # on IR in the league state
        self.assertIn("est. Oct 11", b["injuries"]["items"][0]["status"])
        self.assertEqual(b["matchup"]["headline"], "vs Chase Seyforth")
        self.assertTrue(any("Head to head: you're 1-1" in l for l in b["matchup"]["lines"]))
        merged = merge_brief(b, {"injuries": {"call": "Start X."}, "matchup": {"stakes": "Big game."}})
        self.assertEqual(merged["injuries"]["call"], "Start X.")
        self.assertEqual(merged["matchup"]["stakes"], "Big game.")
        self.assertEqual(merged["injuries"]["items"], b["injuries"]["items"])


class MobileStatusTests(unittest.TestCase):
    def test_one_line_from_tiles(self):
        from league_ai.front_office import mobile_status

        tiles = [
            {"value": "3-0", "label": "Record · 2nd of 10", "tone": "good"},
            {"value": "RB", "label": "Weakest spot · -4.1 vs median", "tone": "warn"},
            {"value": "vs Burruel", "label": "Week 4 · H2H 1-3", "tone": None},
            {"value": "$7", "label": "Cap space", "tone": "warn"},
        ]
        self.assertEqual(mobile_status(4, tiles), "Week 4 · 3-0, 2nd of 10 · $7 cap · vs Burruel")

    def test_handles_missing_tiles(self):
        from league_ai.front_office import mobile_status

        self.assertEqual(mobile_status(0, None), "")


class WeekContextTests(unittest.TestCase):
    def _fo(self):
        return {
            "week": 5, "season": 2026, "opponent": {"owner_name": "Dylan Burruel", "week": 5},
            "data_brief": {"injuries": {"items": [{"player": "Breece Hall", "status": "Questionable · knee"}]},
                           "matchup": {"headline": "vs Dylan Burruel", "lines": ["Burruel 3-1 at 120.5 ppg vs your 4-0 at 131.2 ppg"]}},
            "rankings": [
                {"owner": "Dylan Burruel", "now_rank": 1, "dynasty_rank": 3, "starters": ["QB Josh Allen (24.1)"]},
                {"owner": "Tommy Bruggeman", "now_rank": 2, "dynasty_rank": 1, "weakest_slot": "TE", "weakest_note": "-12% vs league median",
                 "strongest_slot": "WR", "starters": ["WR Ja'Marr Chase (21.0)"]},
            ],
        }

    def test_includes_week_opponent_injuries_and_ranks(self):
        from datetime import date
        from league_ai.front_office import week_context

        text = week_context(self._fo(), "Tommy Bruggeman", today=date(2026, 10, 2))
        self.assertIn("Fri Oct 2, 2026", text)
        self.assertIn("NFL week 5", text)
        self.assertIn("vs Dylan Burruel", text)
        self.assertIn("Breece Hall (Questionable · knee)", text)
        self.assertIn("#1 Dylan Burruel, #2 Tommy Bruggeman", text)
        self.assertIn("weakest slot TE (-12% vs league median)", text)
        self.assertIn("Opponent Dylan Burruel's best lineup", text)

    def test_empty_without_data(self):
        from league_ai.front_office import week_context

        self.assertEqual(week_context(None, "Tommy"), "")


class BriefFingerprintTests(unittest.TestCase):
    def test_changes_when_injuries_or_opponent_change(self):
        from league_ai.front_office import brief_fingerprint

        base = {"injuries": {"items": [{"player": "A", "status": "Questionable"}]}, "matchup": {"headline": "vs X"}}
        same = {"injuries": {"items": [{"player": "A", "status": "Questionable"}]}, "matchup": {"headline": "vs X", "lines": ["new line"]}}
        worse = {"injuries": {"items": [{"player": "A", "status": "Out"}]}, "matchup": {"headline": "vs X"}}
        other_opp = {"injuries": {"items": [{"player": "A", "status": "Questionable"}]}, "matchup": {"headline": "vs Y"}}
        self.assertEqual(brief_fingerprint(base), brief_fingerprint(same))
        self.assertNotEqual(brief_fingerprint(base), brief_fingerprint(worse))
        self.assertNotEqual(brief_fingerprint(base), brief_fingerprint(other_opp))
