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
        self.assertEqual(old.dynasty, healthy.dynasty)  # the market rank already prices age; no second penalty
        young_unranked = player_strength(row, recent_ppg=20, season_ppg=18, pos_rank=None, age=24, injury_status=None)
        old_unranked = player_strength(row, recent_ppg=20, season_ppg=18, pos_rank=None, age=31, injury_status=None)
        self.assertLess(old_unranked.dynasty, young_unranked.dynasty)
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


class ByeWeekTests(unittest.TestCase):
    def test_bye_weeks_from_games(self):
        from league_ai.nfl_data import bye_weeks_from_games

        games = [{"week": 1, "home_team": "KC", "away_team": "LA"}, {"week": 1, "home_team": "BUF", "away_team": "MIA"},
                 {"week": 2, "home_team": "KC", "away_team": "BUF"},
                 {"week": 3, "home_team": "LA", "away_team": "BUF"}, {"week": 3, "home_team": "MIA", "away_team": "KC"}]
        byes = bye_weeks_from_games(games)
        self.assertEqual(byes["LAR"], 2)  # nflverse "LA" -> Sleeper "LAR"
        self.assertNotIn("KC", byes)  # plays every week
        self.assertEqual(byes["MIA"], 2)
        self.assertEqual(bye_weeks_from_games([]), {})

    def test_bye_players_benched_but_scores_unchanged(self):
        from league_ai.power import PlayerStrength

        def p(sid, pos, now, nfl):
            return PlayerStrength(sleeper_id=sid, name=sid, position=pos, team_id="a", owner="A", now=now, dynasty=now, available=True, nfl_team=nfl)

        players = [p("QB1", "QB", 25, "KC"), p("WR1", "WR", 20, "DAL"), p("WR2", "WR", 12, "NYG"), p("WR3", "WR", 10, "SF")]
        teams = [{"league_team_id": "a", "owner_name": "A"}]
        plain = compute_power(players, teams, ["QB", "WR", "WR"])[0]
        tp = compute_power(players, teams, ["QB", "WR", "WR"], byes={"DAL": 6, "SF": 7}, week=6)[0]
        self.assertEqual(tp.now_score, plain.now_score)
        self.assertEqual([x.name for x in tp.starters], ["QB1", "WR1", "WR2"])
        self.assertEqual([x.name for x in tp.week_starters], ["QB1", "WR2", "WR3"])
        self.assertEqual([x.name for x in tp.on_bye], ["WR1"])
        self.assertEqual([x.name for x in tp.bye_next], ["WR3"])

    def test_week_context_lists_byes(self):
        from datetime import date
        from league_ai.front_office import week_context

        fo = {"week": 6, "season": 2026, "opponent": {"owner_name": "Dylan Burruel", "week": 6}, "bye_teams": ["DAL", "KC"],
              "data_brief": {"injuries": {"items": []}, "matchup": {"headline": "vs Dylan Burruel", "lines": []}},
              "rankings": [{"owner": "Tommy Bruggeman", "now_rank": 1, "dynasty_rank": 1, "starters": ["WR CeeDee Lamb (20.0)"], "week_starters": ["WR Puka Nacua (18.0)"],
                            "on_bye": ["WR CeeDee Lamb (DAL)"], "bye_next": ["QB Brock Purdy (SF)"]},
                           {"owner": "Dylan Burruel", "now_rank": 2, "dynasty_rank": 2, "starters": ["QB Patrick Mahomes (22.0)"], "week_starters": ["QB Jared Goff (18.0)"], "on_bye": ["QB Patrick Mahomes (KC)"]}]}
        text = week_context(fo, "Tommy Bruggeman", today=date(2026, 10, 6))
        self.assertIn("on bye in week 6: DAL, KC", text)
        self.assertIn("ON BYE this week (cannot score; never recommend starting them): WR CeeDee Lamb (DAL)", text)
        self.assertIn("Puka Nacua", text)
        self.assertNotIn("CeeDee Lamb (20.0)", text)
        self.assertIn("NEXT week (week 7): QB Brock Purdy (SF)", text)
        self.assertIn("Dylan Burruel's players on bye this week: QB Patrick Mahomes (KC)", text)

    def test_fingerprint_unchanged_without_byes(self):
        from league_ai.front_office import brief_fingerprint

        base = {"injuries": {"items": []}, "matchup": {"headline": "vs X"}}
        self.assertEqual(brief_fingerprint(base), brief_fingerprint({**base, "byes": []}))
        self.assertNotEqual(brief_fingerprint(base), brief_fingerprint({**base, "byes": ["WR A (DAL)"]}))


class MatchupTests(unittest.TestCase):
    def test_week_matchups_implied_points(self):
        from league_ai.nfl_data import current_week_from_games, week_matchups

        rows = [{"week": 6, "home_team": "SF", "away_team": "LA", "home_score": 24, "spread_line": 3.0, "total_line": 47.0},
                {"week": 7, "home_team": "KC", "away_team": "BUF", "home_score": None, "spread_line": -2.5, "total_line": 50.5, "gameday": "2026-10-18"}]
        self.assertEqual(current_week_from_games(rows), 7)
        games = week_matchups(rows, 7)
        self.assertEqual({k: games["KC"][k] for k in ("opponent", "home", "implied_points", "gameday")}, {"opponent": "BUF", "home": True, "implied_points": 24.0, "gameday": "2026-10-18"})
        self.assertEqual(games["BUF"]["implied_points"], 26.5)
        self.assertEqual(week_matchups(rows, 6)["LAR"]["opponent"], "SF")

    def test_defense_vs_position_ranks_and_shrinks(self):
        from league_ai.nfl_data import defense_vs_position_from_stats, matchup_label, matchup_multiplier

        rows = []
        for week in (1, 2, 3, 4):
            rows += [{"week": week, "position": "WR", "opponent": "SF", "fantasy_points_ppr": 10},
                     {"week": week, "position": "WR", "opponent": "LA", "fantasy_points_ppr": 30},
                     {"week": week, "position": "WR", "opponent": "KC", "fantasy_points_ppr": 20},
                     {"week": week, "position": "WR", "opponent": "BUF", "fantasy_points_ppr": 20}]
        dvp = defense_vs_position_from_stats(rows)
        self.assertEqual(dvp[("SF", "WR")]["rank"], 1)
        self.assertEqual(dvp[("LAR", "WR")]["rank"], 4)
        self.assertEqual(dvp[("SF", "WR")]["ppg_allowed"], 10.0)
        # raw 0.5x average, shrunk with 4 games vs a 3-game prior
        self.assertAlmostEqual(dvp[("SF", "WR")]["factor"], 1 - 0.5 * 4 / 7, places=3)
        self.assertEqual(matchup_label(dvp[("SF", "WR")]), "tough")
        self.assertEqual(matchup_label(dvp[("LAR", "WR")]), "soft")
        self.assertEqual(matchup_multiplier(0.2), 0.85)
        self.assertEqual(matchup_multiplier(None), 1.0)

    def test_tough_matchup_flips_a_close_call_but_not_a_stud(self):
        from league_ai.front_office import apply_matchups
        from league_ai.power import PlayerStrength

        def p(sid, pos, now, nfl):
            return PlayerStrength(sleeper_id=sid, name=sid, position=pos, team_id="a", owner="A", now=now, dynasty=now, available=True, nfl_team=nfl)

        players = [p("Stud", "WR", 22, "DAL"), p("Close1", "WR", 14, "NYG"), p("Close2", "WR", 13.5, "SEA")]
        games = {"DAL": {"opponent": "SF", "home": False, "implied_points": 20.5}, "NYG": {"opponent": "SF", "home": True, "implied_points": 18.0},
                 "SEA": {"opponent": "LAR", "home": True, "implied_points": 26.0}}
        dvp = {("SF", "WR"): {"rank": 1, "teams": 32, "ppg_allowed": 25.0, "factor": 0.8, "games": 6}, ("LAR", "WR"): {"rank": 32, "teams": 32, "ppg_allowed": 45.0, "factor": 1.2, "games": 6}}
        apply_matchups(players, games, dvp)
        tp = compute_power(players, [{"league_team_id": "a", "owner_name": "A"}], ["WR", "WR"], week=7)[0]
        self.assertEqual([x.name for x in tp.week_starters], ["Stud", "Close2"])
        self.assertEqual([x.name for x in tp.starters], ["Stud", "Close1"])  # season view ignores the matchup
        self.assertIn("@ SF, tough: SF allows 1st-fewest PPR to WR", players[0].matchup)
        self.assertIn("soft: LAR allows 1st-most PPR to WR", players[2].matchup)
        self.assertIn("team implied 26.0 pts", players[2].matchup)


class ToolMatchupTests(unittest.TestCase):
    def test_profile_shows_this_weeks_matchup_and_byes(self):
        import league_ai.nfl_data as nfl_data
        from league_ai.tools.nfl import NflData, tool_get_player_profile
        from tests.test_league_ai_tools import PLAYERS, FakeQuery

        class PagedQuery(FakeQuery):
            def range(self, start, end):
                self._order = []
                self.rows = self.rows[start:end + 1]
                return self

        games = [{"season": 2026, "game_type": "REG", "week": 1, "home_team": "ATL", "away_team": "CAR", "home_score": 20},
                 {"season": 2026, "game_type": "REG", "week": 1, "home_team": "BUF", "away_team": "TB", "home_score": 17},
                 {"season": 2026, "game_type": "REG", "week": 2, "home_team": "TB", "away_team": "ATL", "home_score": None, "spread_line": 1.0, "total_line": 45.0},
                 {"season": 2026, "game_type": "REG", "week": 3, "home_team": "BUF", "away_team": "ATL", "home_score": None},
                 {"season": 2026, "game_type": "REG", "week": 3, "home_team": "CAR", "away_team": "TB", "home_score": None}]
        stats = [{"season": 2026, "season_type": "REG", "week": 1, "position": "RB", "opponent": "TB", "fantasy_points_ppr": 8, "gsis_id": "x"},
                 {"season": 2026, "season_type": "REG", "week": 1, "position": "RB", "opponent": "CAR", "fantasy_points_ppr": 30, "gsis_id": "y"},
                 {"season": 2026, "season_type": "REG", "week": 1, "position": "RB", "opponent": "ATL", "fantasy_points_ppr": 20, "gsis_id": "z"},
                 {"season": 2026, "season_type": "REG", "week": 1, "position": "RB", "opponent": "BUF", "fantasy_points_ppr": 22, "gsis_id": "w"}]

        class Client:
            tables = {"nfl_players": PLAYERS, "nfl_games": games, "nfl_player_stats": stats, "nfl_market_values": [], "nfl_injuries": [], "nfl_practice_reports": []}
            def table(self, name): return PagedQuery(self.tables.get(name, []))

        nfl_data._CACHE.clear()
        try:
            data = NflData(Client(), league_state=fixture_state())
            bijan = tool_get_player_profile(data, "Bijan Robinson")
            self.assertEqual(bijan["this_week"]["week"], 2)
            self.assertIn("@ TB, tough: TB allows 1st-fewest PPR to RB", bijan["this_week"]["matchup"])
            self.assertIn("team implied 22.0 pts", bijan["this_week"]["matchup"])
            self.assertEqual(bijan["this_week"]["defense_vs_position"]["rank_toughest_first"], "1 of 4")
            allen = tool_get_player_profile(data, "Josh Allen")
            self.assertEqual(allen["bye_week"], 2)
            self.assertIn("ON BYE", allen["this_week"]["matchup"])
        finally:
            nfl_data._CACHE.clear()


class WeatherTests(unittest.TestCase):
    def test_kickoff_forecast_averages_first_three_hours_for_open_air_only(self):
        from league_ai.nfl_data import kickoff_forecasts

        games = [{"stadium": "Lambeau Field", "roof": "outdoors", "gameday": "2026-10-18", "gametime": "13:00"},
                 {"stadium": "Ford Field", "roof": "dome", "gameday": "2026-10-18", "gametime": "13:00"},
                 {"stadium": "Mercedes-Benz Stadium", "roof": None, "gameday": "2026-10-18", "gametime": "16:25"}]
        seen = {}

        def fake(url, params):
            seen.update(params)
            times = [f"2026-10-18T{h:02d}:00" for h in range(24)]
            return [{"hourly": {"time": times, "temperature_2m": [40.0] * 24, "wind_speed_10m": [10.0] * 13 + [20.0, 22.0, 24.0] + [5.0] * 8, "precipitation": [0.0] * 13 + [0.1, 0.2, 0.1] + [0.0] * 8}}]

        out = kickoff_forecasts(games, fake)
        self.assertEqual(seen["latitude"], "44.5013")  # only the open-air venue is asked for
        self.assertEqual(out, {"Lambeau Field": {"temp_f": 40, "wind_mph": 22, "precip_in": 0.4, "source": "forecast"}})
        self.assertEqual(kickoff_forecasts(games, lambda *a: (_ for _ in ()).throw(RuntimeError("offline"))), {})

    def test_wind_hurts_passing_and_helps_rushing_but_not_indoors(self):
        from league_ai.nfl_data import game_multiplier, matchup_note, total_multiplier

        windy = {"opponent": "CHI", "home": True, "implied_points": 22.5, "roof": "outdoors", "weather": {"temp_f": 35, "wind_mph": 22, "precip_in": 0.0, "source": "forecast"}}
        dome = {**windy, "roof": "dome"}
        self.assertAlmostEqual(game_multiplier("QB", windy), 0.88)
        self.assertAlmostEqual(game_multiplier("RB", windy), 1.03)
        self.assertEqual(game_multiplier("QB", dome), 1.0)
        shootout = {"opponent": "CHI", "home": True, "implied_points": 31.5, "roof": "dome"}
        self.assertAlmostEqual(game_multiplier("WR", shootout), 1.1)
        self.assertEqual(total_multiplier("QB", {**windy, "implied_points": 14.0}, {"factor": 0.6}), 0.8)  # capped
        note = matchup_note("QB", windy, None)
        self.assertIn("outdoors, 35F, wind 22 mph (forecast)", note)
        self.assertIn("indoors", matchup_note("QB", dome, None))


class TeamContextTests(unittest.TestCase):
    GAMES = [
        {"week": 1, "gameday": "2026-09-13", "home_team": "SEA", "away_team": "PHI", "home_score": 27, "away_score": 20, "home_coach": "Mike Macdonald", "away_coach": "Nick Sirianni"},
        {"week": 2, "gameday": "2026-09-20", "home_team": "PHI", "away_team": "NYG", "home_score": 17, "away_score": 10, "home_coach": "Nick Sirianni", "away_coach": "Brian Daboll"},
        {"week": 3, "gameday": "2026-09-27", "home_team": "SEA", "away_team": "NYG", "home_score": 20, "away_score": 30, "home_coach": "Mike Macdonald", "away_coach": "Brian Daboll"},
        {"week": 4, "gameday": "2026-10-01", "home_team": "NYG", "away_team": "PHI", "home_score": None, "away_score": None, "home_coach": "Brian Daboll", "away_coach": "Nick Sirianni"},
        {"week": 4, "gameday": "2026-10-04", "home_team": "SEA", "away_team": "LA", "home_score": None, "away_score": None, "home_coach": "Mike Macdonald", "away_coach": "Sean McVay"},
    ]

    def test_rest_bye_and_short_week(self):
        from league_ai.team_context import rest_info

        rest = rest_info(self.GAMES, 4)
        self.assertEqual(rest["SEA"], {"rest_days": 7, "off_bye": False, "short_week": False})
        self.assertTrue(rest["PHI"]["off_bye"])  # last played week 2
        self.assertEqual(rest["NYG"], {"rest_days": 4, "off_bye": False, "short_week": True})

    def test_defense_coaches_and_after_bye_history(self):
        from league_ai.team_context import coach_after_bye, coaches, team_defense

        d = team_defense(self.GAMES)
        self.assertEqual(d["PHI"]["rank"], 1)  # 18.5 allowed per game
        self.assertEqual(coaches(self.GAMES)["PHI"], "Nick Sirianni")
        last = [{"week": w, "gameday": f"2025-09-{7 * w:02d}", "home_team": "PHI", "away_team": "DAL", "home_score": pts, "away_score": 20, "home_coach": "Nick Sirianni", "away_coach": "X"}
                for w, pts in ((1, 20), (2, 20), (3, 20), (5, 30))]
        hist = coach_after_bye({2025: last})
        self.assertEqual(hist["Nick Sirianni"], {"games": 1, "wins": 1, "losses": 0, "pts_vs_avg": 7.5, "since": 2025})

    def test_tendencies_and_coaching_change(self):
        from league_ai.team_context import team_tendencies, tendency_text

        rows = [{"week": 1, "team": "SEA", "position": "QB", "attempts": 40, "sacks": 2, "carries": 3},
                {"week": 1, "team": "SEA", "position": "RB", "carries": 15, "targets": 4},
                {"week": 1, "team": "SEA", "position": "WR", "targets": 30},
                {"week": 1, "team": "SEA", "position": "TE", "targets": 6},
                {"week": 1, "team": "NYG", "position": "QB", "attempts": 20, "carries": 2},
                {"week": 1, "team": "NYG", "position": "RB", "carries": 30}]
        t = team_tendencies(rows)
        self.assertEqual(t["SEA"]["pass_rate"], 0.7)
        self.assertEqual(t["SEA"]["plays_per_game"], 60.0)
        self.assertEqual(t["SEA"]["target_share"]["WR"], 0.75)
        self.assertIn("pass rate 70% (pass-heavy), 60.0 plays/g, targets WR 75%/TE 15%/RB 10%", tendency_text(t["SEA"]))
        self.assertIn("(run-heavy)", tendency_text(t["NYG"]))

    def test_team_lines_and_bye_boost(self):
        from league_ai.nfl_data import game_multiplier, matchup_note
        from league_ai.team_context import TeamContext

        ctx = TeamContext.__new__(TeamContext)
        ctx.season, ctx.week = 2026, 4
        from league_ai.team_context import coaches, rest_info, team_defense
        ctx.rest, ctx.defense, ctx.coaches = rest_info(self.GAMES, 4), team_defense(self.GAMES), coaches(self.GAMES)
        ctx.last_coaches = {"PHI": "Nick Sirianni", "NYG": "Someone Else"}
        ctx.tendencies = {"PHI": {"games": 2, "pass_rate": 0.52, "plays_per_game": 62.0, "target_share": {}, "league_pass_rate": 0.58},
                          "NYG": {"games": 3, "pass_rate": 0.6, "plays_per_game": 64.0, "target_share": {}, "league_pass_rate": 0.58}}
        ctx.last_tendencies = {"PHI": {"pass_rate": 0.5, "plays_per_game": 60.0}, "NYG": {"pass_rate": 0.55, "plays_per_game": 61.0}}
        ctx.after_bye = {"Nick Sirianni": {"games": 4, "wins": 3, "losses": 1, "pts_vs_avg": 4.2, "since": 2023}}
        games = ctx.annotate({"PHI": {"opponent": "NYG", "home": False, "implied_points": 24.0}, "NYG": {"opponent": "PHI", "home": True, "implied_points": 20.0}})
        self.assertTrue(games["PHI"]["off_bye"] and games["NYG"]["opp_off_bye"])
        self.assertAlmostEqual(game_multiplier("RB", games["PHI"]), (1 + 0.5 * (24 / 22.5 - 1)) * 1.02)
        self.assertIn("off a bye", matchup_note("WR", games["PHI"], None))
        self.assertIn("PHI off a bye", matchup_note("WR", games["NYG"], None))
        lines = ctx.team_lines(["NYG"], games)
        self.assertIn("NYG, HC Brian Daboll: pass rate 60% (balanced)", lines[0])
        self.assertIn("under Someone Else (coaching change)", lines[0])
        self.assertIn("short week (4 days rest)", lines[0])
        self.assertIn("faces PHI D allows 18.5 pts/g (1st-fewest of 3)", lines[0])
        self.assertIn("PHI off a bye (11 days rest); Nick Sirianni is 3-1 after byes since 2023, +4.2 pts", lines[0])


class ScheduleOutlookTests(unittest.TestCase):
    def test_defense_profile_ranks_pass_and_run(self):
        from league_ai.team_context import defense_profile, defense_profile_text

        rows = [{"week": 1, "position": "QB", "opponent": "SF", "passing_yards": 180, "passing_epa": -6, "attempts": 30, "sacks": 4, "rushing_yards": 10},
                {"week": 1, "position": "RB", "opponent": "SF", "rushing_yards": 120},
                {"week": 1, "position": "QB", "opponent": "NYG", "passing_yards": 320, "passing_epa": 9, "attempts": 38, "sacks": 1, "rushing_yards": 5},
                {"week": 1, "position": "RB", "opponent": "NYG", "rushing_yards": 60}]
        prof = defense_profile(rows)
        self.assertEqual(prof["SF"]["pass_yds_pg_rank"], 1)
        self.assertEqual(prof["SF"]["sacks_pg_rank"], 1)
        self.assertEqual(prof["NYG"]["rush_yds_pg_rank"], 1)
        self.assertAlmostEqual(prof["SF"]["epa_per_dropback"], -6 / 34, places=3)
        self.assertIn("pass D 180 yds/g (1st of 2)", defense_profile_text(prof["SF"]))

    def test_outlook_grades_weeks_and_playoffs(self):
        from league_ai.team_context import schedule_outlook

        rows = [{"week": 8, "home_team": "DAL", "away_team": "SF"}, {"week": 8, "home_team": "NYG", "away_team": "PHI"},
                {"week": 9, "home_team": "NYG", "away_team": "DAL"}, {"week": 9, "home_team": "SF", "away_team": "PHI"},
                {"week": 10, "home_team": "SF", "away_team": "NYG"}, {"week": 10, "home_team": "PHI", "away_team": "DAL"}]
        dvp = {("SF", "WR"): {"factor": 0.8, "rank": 1, "teams": 4}, ("NYG", "WR"): {"factor": 1.2, "rank": 4, "teams": 4}, ("PHI", "WR"): {"factor": 1.0, "rank": 2, "teams": 4}}
        out = schedule_outlook(rows, "DAL", "WR", 8, 10, dvp, {}, playoff_start=10)
        self.assertEqual(out["weeks"], ["W8 vs SF tough", "W9 @ NYG soft", "W10 @ PHI neutral"])
        self.assertEqual((out["soft"], out["tough"], out["games"]), (1, 1, 3))
        self.assertEqual(out["avg_matchup_effect"], "+0.0%")
        self.assertEqual(out["fantasy_playoffs"], {"weeks": "10-10", "avg_matchup_effect": "+0.0%", "games": 1})
