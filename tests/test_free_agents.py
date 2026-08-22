from __future__ import annotations

import unittest
from pathlib import Path

from services.free_agents import (
    LeagueFreeAgentState,
    build_free_agent_results,
    calculate_lifetime_points,
    current_free_agents_for_filters,
    future_free_agents_for_season,
    future_season_options,
    load_lifetime_points,
    load_league_free_agent_state,
    resolve_rookie_ranking_strategy,
    rookie_class_for_position,
    resolve_active_league_season,
    resolve_free_agent_season,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = []

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        self.client.calls.append((self.table, tuple(self.filters)))
        if self.table in self.client.fail_tables:
            raise RuntimeError("unavailable")
        rows = self.client.rows.get(self.table, [])
        for column, value in self.filters:
            rows = [row for row in rows if row.get(column) == value]
        return FakeResponse(rows)


class FakeClient:
    def __init__(self, rows=None, fail_tables=()):
        self.rows = rows or {}
        self.fail_tables = set(fail_tables)
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)


def player(player_id, name, **updates):
    row = {
        "sleeper_id": player_id,
        "player_name": name,
        "pos": "WR",
        "nfl_team": "NYJ",
        "active": True,
        "latest_season": 2026,
        "season_ppg": 12.5,
    }
    row.update(updates)
    return row


def contract(player_id, **updates):
    row = {
        "league_id": "league-1",
        "sleeper_player_id": player_id,
        "player_name": "Contracted Player",
        "league_team_id": "team-1",
        "owner_name": "Legacy Owner",
        "contract_years_left": 1,
        "salary": 12,
        "player_position": "WR",
    }
    row.update(updates)
    return row


def state(*, contracts=(), roster_rows=(), teams=()):
    return LeagueFreeAgentState(tuple(contracts), tuple(roster_rows), tuple(teams))


class FreeAgentServiceTest(unittest.TestCase):
    def test_active_season_resolution_and_options(self):
        context = type("Context", (), {"current_season": 2026})()
        self.assertEqual(resolve_active_league_season(context), 2026)
        self.assertEqual(future_season_options(2026), (2027, 2028, 2029, 2030))
        with self.assertRaises(ValueError):
            resolve_active_league_season(type("Context", (), {"current_season": None})())

    def test_contract_expiration_formula(self):
        self.assertEqual(resolve_free_agent_season(2026, 1), 2027)
        self.assertEqual(resolve_free_agent_season(2026, "4"), 2030)
        self.assertIsNone(resolve_free_agent_season(2026, 0))
        self.assertIsNone(resolve_free_agent_season(2026, "bad"))

    def test_current_free_agents_exclude_owned_players(self):
        universe = [player("p-1", "Owned"), player("p-2", "Available")]
        result = build_free_agent_results(universe, state(contracts=[contract("p-1")]), active_season=2026)
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-2"])

    def test_canonical_roster_state_also_excludes_owned_players(self):
        universe = [player("p-1", "Rostered"), player("p-2", "Available")]
        roster_rows = [{"league_id": "league-1", "sleeper_player_id": "p-1", "status": "active"}]
        result = build_free_agent_results(universe, state(roster_rows=roster_rows), active_season=2026)
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-2"])

    def test_league_state_queries_are_scoped(self):
        client = FakeClient({
            "contracts": [contract("p-1"), contract("p-2", league_id="league-2")],
            "league_teams": [{"id": "team-1", "league_id": "league-1"}, {"id": "team-2", "league_id": "league-2"}],
            "team_roster_state": [{"sleeper_player_id": "p-1", "league_id": "league-1"}],
        })
        loaded = load_league_free_agent_state(client, "league-1")
        self.assertEqual(len(loaded.contracts), 1)
        self.assertTrue(all(filters == (("league_id", "league-1"),) for _table, filters in client.calls))

    def test_roster_unavailable_falls_back_safely(self):
        client = FakeClient({"contracts": [contract("p-1")], "league_teams": []}, fail_tables={"team_roster_state"})
        loaded = load_league_free_agent_state(client, "league-1")
        self.assertEqual(len(loaded.contracts), 1)
        self.assertIn("ownership fallback", loaded.warnings[0])

    def test_future_free_agents_only_selected_expiration_year(self):
        universe = [player("p-1", "One Year"), player("p-2", "Two Years")]
        contracts = [contract("p-1", contract_years_left=1), contract("p-2", contract_years_left=2)]
        result = build_free_agent_results(universe, state(contracts=contracts), active_season=2026)
        rows = future_free_agents_for_season(result.future, 2028)
        self.assertEqual([row.sleeper_player_id for row in rows], ["p-2"])

    def test_future_rows_use_canonical_team_and_league_scoped_legacy_fallback(self):
        universe = [player("p-1", "Canonical"), player("p-2", "Legacy")]
        contracts = [contract("p-1"), contract("p-2", league_team_id=None, owner_name="Legacy Team")]
        teams = [{"id": "team-1", "league_id": "league-1", "team_name": "Canonical Team"}]
        result = build_free_agent_results(universe, state(contracts=contracts, teams=teams), active_season=2026)
        names = {row.sleeper_player_id: row.contracted_team for row in result.future}
        self.assertEqual(names, {"p-1": "Canonical Team", "p-2": "Legacy Team"})
        self.assertIn("internal:legacy_contract_team_label_fallback", result.warnings)

    def test_open_market_ranking_prefers_lifetime_then_roster_then_ppg(self):
        universe = [
            player("p-1", "Lower PPG", season_ppg=10),
            player("p-2", "Higher PPG", season_ppg=20),
            player("p-3", "Lifetime Leader", season_ppg=None, nfl_team=None),
            player("p-4", "Lifetime Tie Active", season_ppg=5),
            player("p-5", "No Evidence", season_ppg=None),
        ]
        result = build_free_agent_results(
            universe,
            state(),
            active_season=2026,
            lifetime_points_by_player={"p-1": 500, "p-2": 5, "p-3": 900, "p-4": 900},
        )
        self.assertEqual(
            [row.sleeper_player_id for row in result.current],
            ["p-4", "p-3", "p-1", "p-2", "p-5"],
        )

    def test_open_market_roster_status_filters(self):
        result = build_free_agent_results(
            [player("p-1", "Rostered"), player("p-2", "Unsigned", nfl_team=None)], state(), active_season=2026
        )
        active = current_free_agents_for_filters(result.current, nfl_roster_status="Active roster")
        unsigned = current_free_agents_for_filters(result.current, nfl_roster_status="Not on active roster")
        self.assertEqual([row.sleeper_player_id for row in active], ["p-1"])
        self.assertEqual([row.sleeper_player_id for row in unsigned], ["p-2"])

    def test_current_ranking_ties_are_deterministic(self):
        result = build_free_agent_results(
            [player("p-2", "Same"), player("p-1", "Same")], state(), active_season=2026
        )
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-1", "p-2"])

    def test_inactive_players_are_excluded_but_active_players_without_team_remain(self):
        universe = [
            player("p-1", "Explicitly Inactive", active=False),
            player("p-2", "Retired", nfl_status="retired"),
            player("p-3", "Active No Team", nfl_team=None),
        ]
        result = build_free_agent_results(universe, state(), active_season=2026)
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-3"])
        self.assertEqual(result.current[0].nfl_team, "—")

    def test_future_metadata_uses_canonical_universe_and_salary_ranking(self):
        universe = [
            player("p-1", "Canonical One", pos="RB", team="DEN", nfl_team=None, season_ppg=9),
            player("p-2", "Canonical Two", pos="TE", season_ppg=15),
            player("p-3", "Canonical Three", season_ppg=15),
        ]
        contracts = [
            contract("p-1", player_name="Stale", player_position="QB", salary=20),
            contract("p-2", salary=30),
            contract("p-3", salary=30),
        ]
        result = build_free_agent_results(universe, state(contracts=contracts), active_season=2026)
        rows = future_free_agents_for_season(result.future, 2027)
        self.assertEqual([row.sleeper_player_id for row in rows], ["p-3", "p-2", "p-1"])
        self.assertEqual((rows[2].player, rows[2].position, rows[2].nfl_team), ("Canonical One", "RB", "DEN"))

    def test_future_rows_with_unresolved_metadata_remain_safe(self):
        result = build_free_agent_results(
            [],
            state(contracts=[contract("missing", player_name=None, player_position=None, nfl_team=None, salary=None)]),
            active_season=2026,
        )
        row = result.future[0]
        self.assertEqual((row.player, row.position, row.nfl_team), ("—", "—", "—"))
        self.assertIsNone(row.salary)

    def test_lifetime_points_use_one_completed_season_summary(self):
        client = FakeClient({"player_scoring_history": [
            {"sleeper_id": "p-1", "season": 2024, "games_played": 17, "total_points": 100.25, "source": "nflverse_player_stats"},
            {"sleeper_id": "p-1", "season": 2024, "games_played": 17, "total_points": 100.25, "source": "nflverse_player_stats"},
            {"sleeper_id": "p-1", "season": 2025, "games_played": 16, "total_points": 50},
            {"sleeper_id": "p-2", "season": 2025, "games_played": 0, "total_points": None},
        ]})
        self.assertEqual(load_lifetime_points(client), {"p-1": 150.25})

    def test_lifetime_checker_uses_unique_weeks_only_when_summary_absent(self):
        calculation = calculate_lifetime_points([
            {"sleeper_id": "p-1", "season": 2025, "week": 1, "fantasy_points_custom": 10},
            {"sleeper_id": "p-1", "season": 2025, "week": 1, "fantasy_points_custom": 10},
            {"sleeper_id": "p-1", "season": 2025, "week": 2, "fantasy_points_custom": 15},
            {"sleeper_id": "p-2", "season": 2025, "games_played": 2, "total_points": 30},
            {"sleeper_id": "p-2", "season": 2025, "week": 1, "fantasy_points_custom": 12},
        ])
        self.assertEqual(calculation.totals, {"p-2": 30.0, "p-1": 25.0})
        self.assertEqual((calculation.season_summaries_used, calculation.weekly_records_used), (1, 2))
        self.assertEqual(calculation.duplicate_records_ignored, 2)

    def test_unavailable_lifetime_history_is_nonfatal(self):
        self.assertEqual(load_lifetime_points(FakeClient(fail_tables={"player_scoring_history"})), {})

    def test_duplicate_names_do_not_collide(self):
        universe = [player("p-1", "Same Name"), player("p-2", "Same Name")]
        result = build_free_agent_results(universe, state(contracts=[contract("p-1")]), active_season=2026)
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-2"])

    def test_missing_ppg_position_and_team_display_safely(self):
        universe = [player("p-1", "Incomplete", pos=None, nfl_team=None, latest_season=None, season_ppg=None)]
        result = build_free_agent_results(universe, state(), active_season=2026)
        row = result.current[0]
        self.assertIsNone(row.current_season_ppg)
        self.assertEqual(row.position, "—")
        self.assertEqual(row.nfl_team, "—")

    def test_stale_ppg_is_not_presented_as_current(self):
        result = build_free_agent_results([player("p-1", "Stale", latest_season=2025, season_ppg=20)], state(), active_season=2026)
        self.assertIsNone(result.current[0].current_season_ppg)

    def test_empty_results_and_filters_are_safe(self):
        result = build_free_agent_results([], state(), active_season=2026)
        self.assertEqual(result.current, ())
        self.assertEqual(result.future, ())
        self.assertEqual(result.rookies, ())
        self.assertEqual(current_free_agents_for_filters(result.current, search="x"), ())

    def test_invalid_contract_rows_are_omitted_deterministically(self):
        universe = [player("p-1", "Valid")]
        contracts = [contract("p-1", contract_years_left="bad"), {"league_id": "league-1", "player_name": "No ID", "contract_years_left": 1}]
        result = build_free_agent_results(universe, state(contracts=contracts), active_season=2026)
        self.assertEqual(result.future, ())
        self.assertTrue(any("incomplete player identity" in warning for warning in result.warnings))

    def test_page_and_service_contain_no_write_calls(self):
        source = "\n".join(
            (ROOT / path).read_text()
            for path in ("pages/04_Free_Agent.py", "services/free_agents.py")
        ).lower()
        for mutation in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc("):
            self.assertNotIn(mutation, source)

    def test_expiring_contracts_position_filter(self):
        result = build_free_agent_results(
            [player("p-1", "Quarterback", pos="QB"), player("p-2", "Receiver", pos="WR")],
            state(contracts=[contract("p-1", player_position="QB"), contract("p-2", player_position="WR")]),
            active_season=2026,
        )
        self.assertEqual([row.sleeper_player_id for row in future_free_agents_for_season(result.future, 2027, position="QB")], ["p-1"])

    def test_rookie_class_uses_active_year_and_draft_order(self):
        universe = [
            player("r-2", "Second", pos="RB", rookie_class_year=2026, draft_round=1, draft_pick=8),
            player("r-u", "Undrafted", pos="WR", draft_year=2026, draft_round=0, draft_pick=0),
            player("r-1", "First", pos="QB", draft_year=2026, draft_round=1, draft_pick=3),
            player("old", "Old Rookie Flag", draft_year=2025, is_rookie=True),
            player("ret", "Retired Rookie", draft_year=2026, nfl_status="retired"),
        ]
        result = build_free_agent_results(universe, state(), active_season=2026)
        self.assertEqual([row.sleeper_player_id for row in result.rookies], ["r-1", "r-2", "r-u"])
        self.assertEqual(result.rookies[-1].drafted, "UDFA")
        self.assertEqual([row.sleeper_player_id for row in rookie_class_for_position(result.rookies, position="QB")], ["r-1"])

    def test_rookie_stage_precedence_and_table_metadata(self):
        universe = [
            player("drafted", "Drafted", active=False, nfl_team="LV", rookie_class_year=2026, draft_round=1, draft_pick=1, college="Indiana", nfl_status="PROSPECT", market_pool="ROOKIE_PROSPECT"),
            player("udfa-b", "Beta UDFA", active=False, nfl_team="DEN", rookie_class_year=2026, college="Boise State"),
            player("udfa-a", "Alpha UDFA", active=False, nfl_team="SEA", rookie_class_year=2026, college="Idaho"),
            player("prospect", "Remaining Prospect", active=False, nfl_team=None, rookie_class_year=2026, nfl_status="PROSPECT", market_pool="ROOKIE_PROSPECT", college="Utah"),
        ]
        rows = build_free_agent_results(universe, state(), active_season=2026).rookies
        self.assertEqual([row.sleeper_player_id for row in rows], ["drafted", "udfa-a", "udfa-b", "prospect"])
        self.assertEqual([row.rookie_status for row in rows], ["Drafted", "UDFA", "UDFA", "Prospect"])
        self.assertEqual((rows[0].college, rows[0].drafted), ("Indiana", "Round 1, Pick 1"))

    def test_confirmed_2026_prospects_appear_and_2025_rookies_do_not(self):
        universe = [
            player("prospect_2026_duce_robinson_wr", "Duce Robinson", active=False, nfl_team=None, nfl_status="PROSPECT", market_pool="ROOKIE_PROSPECT", rookie_class_year=2026, draft_year=2026),
            player("prospect_2026_makhi_hughes_rb", "Makhi Hughes", pos="RB", active=False, nfl_team=None, nfl_status="PROSPECT", market_pool="ROOKIE_PROSPECT", rookie_class_year=2026, draft_year=2026),
            player("cam-ward", "Cam Ward", pos="QB", rookie_class_year=2025, draft_year=2025),
            player("ashton-jeanty", "Ashton Jeanty", pos="RB", rookie_class_year=2025, draft_year=2025),
        ]
        result = build_free_agent_results(universe, state(), active_season=2026)
        self.assertEqual({row.player for row in result.rookies}, {"Duce Robinson", "Makhi Hughes"})

    def test_active_2025_rookie_page_ignores_upcoming_2026_planning_class(self):
        universe = [
            player("rookie-2025", "Current Rookie", rookie_class_year=2025, draft_year=2025, draft_pick=1),
            player("rookie-2026", "Upcoming Rookie", active=False, nfl_team=None, rookie_class_year=2026, draft_year=2026, nfl_status="PROSPECT", market_pool="ROOKIE_PROSPECT"),
        ]
        result = build_free_agent_results(universe, state(), active_season=2025)
        self.assertEqual([row.sleeper_player_id for row in result.rookies], ["rookie-2025"])

    def test_rookie_metadata_and_missing_values_are_safe(self):
        result = build_free_agent_results(
            [player("r-1", "Rookie", pos="TE", nfl_team=None, draft_year=2026, draft_round=None, draft_pick=None)],
            state(),
            active_season=2026,
        )
        row = result.rookies[0]
        self.assertEqual((row.player, row.position, row.nfl_team, row.drafted), ("Rookie", "TE", "—", "—"))

    def test_rookie_ranking_strategy_prepares_future_layers_without_activating_ai(self):
        self.assertEqual(resolve_rookie_ranking_strategy(), ("nfl_draft_position",))
        self.assertEqual(resolve_rookie_ranking_strategy(ai_rankings_available=True), ("ai_rookie_projection", "nfl_draft_position"))

    def test_page_uses_three_market_names_year_text_and_full_viewport_loader(self):
        source = (ROOT / "pages/04_Free_Agent.py").read_text()
        for label in ("Open Market", "Expiring Contracts", "Rookie Class"):
            self.assertIn(label, source)
        self.assertNotIn('f"●  {season}"', source)
        self.assertNotIn("fa-timeline-line", source)
        self.assertIn("position: fixed; inset: 0", source)
        self.assertLess(source.index("loading_placeholder.empty()\n\nst.markdown("), source.index('<div class="fa-hero">'))

    def test_free_agent_and_gm_assistant_use_shared_loader_treatment(self):
        free_agent_source = (ROOT / "pages/04_Free_Agent.py").read_text()
        assistant_source = (ROOT / "pages/05_GM_Assistant.py").read_text()
        for source, message in (
            (free_agent_source, "Loading Free Agent Market..."),
            (assistant_source, "Loading GM Assistant..."),
        ):
            self.assertIn('class="legacy-loader"', source)
            self.assertIn(message, source)
        self.assertNotIn('st.spinner("Loading the league free-agent market', free_agent_source)


if __name__ == "__main__":
    unittest.main()
