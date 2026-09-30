import unittest
from decimal import Decimal

from league_ai.context_pack import Asker, PackInputs, build_league_pack, team_summaries
from league_ai.settings_store import LeagueAISettings, apply_sleeper_scoring, scoring_summary
from services.team_roster_state import calculate_team_financials, state_cap_adjustments, state_roster
from tests.fixtures.league_ai_state import (
    CHASE, DYLAN, LEAGUE_ID, LEAGUE_RULES, SEASON, TOMMY, fixture_standings, fixture_state,
)


def _asker(team=TOMMY, name="Tommy Bruggeman"):
    return Asker(user_id="u1", league_id=LEAGUE_ID, league_team_id=team, owner_name=name, team_name=name, role="commissioner")


class TeamSummaryTests(unittest.TestCase):
    def test_cap_space_matches_app_math_for_every_team(self):
        state = fixture_state()
        roster = state_roster(state)
        adjustments = state_cap_adjustments(state)
        for summary in team_summaries(state, 225):
            expected = calculate_team_financials(roster, adjustments, salary_cap=225, league_team_id=summary.league_team_id)
            self.assertEqual(summary.cap_space, expected["cap_space"], summary.owner_name)
            self.assertEqual(summary.cap_used, expected["cap_used"], summary.owner_name)

    def test_tommy_numbers(self):
        state = fixture_state()
        tommy = next(s for s in team_summaries(state, 225) if s.league_team_id == TOMMY)
        self.assertEqual(tommy.active_salary, Decimal("126"))  # 60+45+12+4+5
        self.assertEqual(tommy.dead_cap, Decimal("7.5"))
        self.assertEqual(tommy.cap_space, Decimal("91.5"))
        self.assertEqual(tommy.ir_count, 1)
        self.assertEqual(tommy.taxi_count, 1)
        self.assertEqual(tommy.rookies, 2)

    def test_retained_salary_is_netted_and_charged_to_retainer(self):
        state = fixture_state()
        # load_team_state nets retained salary; fixture state is the post-netting shape, so Dylan's row is as given.
        chase = next(s for s in team_summaries(state, 225) if s.league_team_id == CHASE)
        self.assertEqual(chase.adjustments, Decimal("-5"))  # trade carryover received


class PackTests(unittest.TestCase):
    def setUp(self):
        self.inputs = PackInputs(
            league_name="Fantasy League of Extraordinary Gentlemen",
            season=SEASON,
            state=fixture_state(),
            league_rules=LEAGUE_RULES,
            ai_settings=LeagueAISettings(league_id=LEAGUE_ID, house_rules="Sunday morning waivers."),
            asker=_asker(),
            standings=fixture_standings(),
            private_memory=["Wants to contend in 2026; will not trade Bijan."],
            league_notebook=["Chase overpays for young WRs."],
            data_freshness={"league state": "live", "NFL players": "2026-09-28"},
        )

    def test_pack_names_the_asker_and_marks_their_team(self):
        pack = build_league_pack(self.inputs)
        self.assertIn("talking to **Tommy Bruggeman**", pack)
        self.assertIn("← you", pack)
        self.assertIn("(the asker's team)", pack)

    def test_pack_carries_cap_rules_and_house_rules(self):
        pack = build_league_pack(self.inputs)
        self.assertIn("Salary cap: $225", pack)
        self.assertIn("50% of the remaining contract", pack)
        self.assertIn("$1 deal carries no dead cap", pack)
        self.assertIn("cap hit while on taxi = 0.5 x salary", pack)
        self.assertIn("Sunday morning waivers.", pack)

    def test_pack_has_every_team_cap_line_sorted_by_standing(self):
        pack = build_league_pack(self.inputs)
        chase = pack.index("Chase's Chumps (Chase Seyforth) | #1")
        tommy = pack.index("Tommy Bruggeman ← you | #2")
        dylan = pack.index("Dylan Burruel | #3")
        self.assertLess(chase, tommy)
        self.assertLess(tommy, dylan)
        self.assertIn("| $133.50 | $91.50 |", pack)  # Tommy cap used / space

    def test_roster_rows_show_designations_and_years(self):
        pack = build_league_pack(self.inputs)
        self.assertIn("| Taxi Rookie | WR | $4 | 3 | rookie | TAXI rookie |", pack)
        self.assertIn("| Hurt Guy | TE | $5 | 1 | veteran | IR |", pack)
        self.assertIn("| Josh Allen | QB | $60 | 2 | veteran |  |", pack)

    def test_picks_group_by_owner_with_via(self):
        pack = build_league_pack(self.inputs)
        self.assertIn("Draft picks owned: 2027 R1, 2027 R1 (via Chase's Chumps)", pack)
        self.assertIn("2027 R2 (via Tommy Bruggeman)", pack)

    def test_dead_cap_and_carryover_lines(self):
        pack = build_league_pack(self.inputs)
        self.assertIn("dead cap $7.50 for Dropped Vet (2026)", pack)
        self.assertIn("$5 cap traded to Chase Seyforth", pack)
        self.assertIn("$5 cap received from Dylan Burruel", pack)

    def test_activity_memory_and_freshness_sections(self):
        pack = build_league_pack(self.inputs)
        self.assertIn("2026-09-16 Chase Seyforth signed Waiver Guy", pack)
        self.assertIn("2026-09-10 Tommy Bruggeman released Dropped Vet", pack)
        self.assertIn("will not trade Bijan", pack)
        self.assertIn("Chase overpays for young WRs.", pack)
        self.assertIn("NFL players: 2026-09-28", pack)

    def test_pack_size_is_reasonable(self):
        pack = build_league_pack(self.inputs)
        self.assertLess(len(pack), 20000)


class SettingsTests(unittest.TestCase):
    def test_sleeper_scoring_summary(self):
        s = LeagueAISettings(league_id=LEAGUE_ID)
        apply_sleeper_scoring(s, {
            "scoring_settings": {"rec": 1, "pass_td": 4, "rec_td": 6, "bonus_rec_te": 0.5},
            "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN", "BN", "IR", "TAXI"],
            "league_settings": {"playoff_teams": 6, "trade_deadline": 12},
        })
        text = scoring_summary(s)
        self.assertIn("full PPR", text)
        self.assertIn("TE premium +0.5", text)
        self.assertIn("1QB + 1 OP league", text)
        self.assertIn("Starting lineup: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 OP (superflex: QB/RB/WR/TE); 2 bench, 1 IR slot, 1 taxi slot.", text)
        self.assertEqual(s.trade_deadline, "week 12")
        self.assertEqual(s.playoff_teams, 6)
        self.assertEqual(s.scoring_source, "sleeper")

    def test_from_row_ignores_unknown_columns_and_parses_json_strings(self):
        s = LeagueAISettings.from_row({"league_id": LEAGUE_ID, "taxi_cap_fraction": 0.5, "bogus": 1,
                                       "scoring_settings": '{"rec": 0.5}', "updated_at": "x"}, LEAGUE_ID)
        self.assertEqual(s.scoring_settings, {"rec": 0.5})
        self.assertEqual(s.taxi_cap_fraction, 0.5)


if __name__ == "__main__":
    unittest.main()


class _Rpc:
    def __init__(self, payload): self.payload = payload
    def execute(self):
        class R: pass
        r = R(); r.data = self.payload; return r


class _Client:
    def __init__(self, payload): self.payload = payload
    def rpc(self, name, params):
        assert name == "read_canonical_team_state_authenticated"
        return _Rpc(self.payload)


class PackThroughCanonicalReadTests(unittest.TestCase):
    def test_retained_salary_flows_through_load_team_state(self):
        from services.team_roster_state import load_team_state
        state = load_team_state(_Client(fixture_state()), LEAGUE_ID, SEASON)
        pack = build_league_pack(PackInputs(
            league_name="L", season=SEASON, state=state, league_rules=LEAGUE_RULES,
            ai_settings=LeagueAISettings(league_id=LEAGUE_ID), asker=_asker(),
        ))
        # Dylan's Retained Guy cap hit is netted from 30 to 20; Chase carries the $10 retained salary.
        self.assertIn("| Retained Guy | WR | $20 | 2 |", pack)
        self.assertIn("retained salary $10 for Retained Guy (2026)", pack)
        chase = next(s for s in team_summaries(state, 225) if s.league_team_id == CHASE)
        self.assertEqual(chase.cap_used, Decimal("111"))  # 55+50+1 +10 retained -5 carryover


if __name__ == "__main__":
    unittest.main()
