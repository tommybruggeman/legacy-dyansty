from __future__ import annotations

import unittest
from pathlib import Path

from services.free_agents import (
    LeagueFreeAgentState,
    build_commissioner_auction_players,
    build_free_agent_results,
    calculate_lifetime_points,
    canonical_live_owners,
    current_free_agents_for_filters,
    future_free_agents_for_season,
    future_season_options,
    load_lifetime_points,
    load_ranking_ppg,
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
        self.allowed = None

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.allowed = (column, set(values))
        return self

    def range(self, start, end):
        self.allowed_range = (start, end)
        return self

    def execute(self):
        self.client.calls.append((self.table, tuple(self.filters)))
        if self.table in self.client.fail_tables:
            raise RuntimeError("unavailable")
        rows = self.client.rows.get(self.table, [])
        for column, value in self.filters:
            rows = [row for row in rows if row.get(column) == value]
        if self.allowed:
            column, values = self.allowed
            self.client.in_calls.append((self.table, column, frozenset(values)))
            rows = [row for row in rows if row.get(column) in values]
        elif hasattr(self, "allowed_range"):
            start, end = self.allowed_range
            rows = rows[start:end + 1]
        elif self.table == "sleeper_players":
            rows = rows[:1000]
        return FakeResponse(rows)


class FakeClient:
    def __init__(self, rows=None, fail_tables=()):
        self.rows = rows or {}
        self.fail_tables = set(fail_tables)
        self.calls = []
        self.in_calls = []

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


def state(*, contracts=(), roster_rows=(), teams=(), contract_seasons=(), sleeper_players=()):
    return LeagueFreeAgentState(
        tuple(contracts),
        tuple(roster_rows),
        tuple(teams),
        contract_seasons=tuple(contract_seasons),
        sleeper_players=tuple(sleeper_players),
    )


class FreeAgentServiceTest(unittest.TestCase):
    def test_commissioner_auction_identity_search_ignores_market_filters(self):
        identities = [
            player("inactive", "Inactive", active=False, nfl_status="inactive", nfl_team=None),
            player("no-history", "No History", latest_season=None, season_ppg=None),
            {"sleeper_player_id": "fallback", "full_name": "Sleeper Only", "position": "RB",
             "team": None, "status": "Inactive", "is_active": False},
            {"sleeper_player_id": "duplicate", "full_name": "Fresh Name", "position": "TE"},
            player("duplicate", "Stale Name", pos=None),
            player("defense", "Defense", pos="DEF"),
        ]
        rows = build_commissioner_auction_players(identities)
        self.assertEqual(
            [(row.sleeper_player_id, row.player) for row in rows],
            [("duplicate", "Fresh Name"), ("inactive", "Inactive"),
             ("no-history", "No History"), ("fallback", "Sleeper Only")],
        )

    def test_live_ownership_requires_live_agreement_and_obligation(self):
        contracts = [
            {"id": "live", "player_id": "owned", "league_team_id": "team-1",
             "status": "active", "superseded_by_contract_id": None},
            {"id": "dropped", "player_id": "available", "league_team_id": "team-1",
             "status": "released", "superseded_by_contract_id": None},
            {"id": "stale", "player_id": "stale", "league_team_id": "team-1",
             "status": "active", "superseded_by_contract_id": None},
        ]
        seasons = [
            {"contract_id": "live", "season": 2026, "obligation_status": "active"},
            {"contract_id": "dropped", "season": 2026, "obligation_status": "released"},
            {"contract_id": "stale", "season": 2026, "obligation_status": "voided"},
        ]
        owners = canonical_live_owners(
            state(contracts=contracts, contract_seasons=seasons,
                  teams=[{"id": "team-1", "team_name": "Team One"}]),
            active_season=2026,
        )
        self.assertEqual(owners, {"owned": "Team One"})

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
        scoped_calls = [filters for table, filters in client.calls if table != "sleeper_players"]
        self.assertTrue(all(filters == (("league_id", "league-1"),) for filters in scoped_calls))
        self.assertIn(("sleeper_players", ()), client.calls)

    def test_roster_unavailable_falls_back_safely(self):
        client = FakeClient({"contracts": [contract("p-1")], "league_teams": []}, fail_tables={"team_roster_state"})
        loaded = load_league_free_agent_state(client, "league-1")
        self.assertEqual(len(loaded.contracts), 1)
        self.assertIn("ownership fallback", loaded.warnings[0])

    def test_targeted_sleeper_metadata_loads_relevant_player_beyond_first_thousand(self):
        sleeper_rows = [
            {
                "sleeper_player_id": str(index),
                "full_name": f"Player {index}",
                "position": "WR",
                "team": "FA",
                "status": "Active",
                "is_active": True,
            }
            for index in range(1201)
        ]
        sleeper_rows[-1].update(position="RB", team="GB")
        client = FakeClient({
            "contract_agreements": [],
            "contracts": [],
            "contract_seasons": [],
            "league_teams": [],
            "team_roster_state": [],
            "sleeper_players": sleeper_rows,
        })

        relevant_ids = tuple(str(index) for index in range(1201))
        loaded = load_league_free_agent_state(
            client,
            "league-1",
            relevant_ids,
        )

        by_id = {row["sleeper_player_id"]: row for row in loaded.sleeper_players}
        self.assertEqual((by_id["1200"]["position"], by_id["1200"]["team"]), ("RB", "GB"))
        self.assertEqual(len(client.in_calls), 3)
        self.assertTrue(all(len(ids) <= 500 for _table, _column, ids in client.in_calls))

        result = build_free_agent_results(
            [player("1200", "Target", pos="WR", nfl_team=None)],
            loaded,
            active_season=2026,
        )
        self.assertEqual((result.current[0].position, result.current[0].nfl_team), ("RB", "GB"))

    def test_future_free_agents_only_selected_expiration_year(self):
        universe = [player("p-1", "One Year"), player("p-2", "Two Years")]
        contracts = [contract("p-1", contract_years_left=1), contract("p-2", contract_years_left=2)]
        result = build_free_agent_results(universe, state(contracts=contracts), active_season=2026)
        rows = future_free_agents_for_season(result.future, 2028)
        self.assertEqual([row.sleeper_player_id for row in rows], ["p-2"])

    def test_canonical_contract_seasons_override_legacy_remaining_years(self):
        result = build_free_agent_results(
            [player("p-1", "Canonical")],
            state(
                contracts=[contract("p-1", id="contract-1", contract_years_left=1)],
                contract_seasons=[
                    {"contract_id": "contract-1", "season": 2026, "obligation_status": "active"},
                    {"contract_id": "contract-1", "season": 2027, "obligation_status": "scheduled"},
                    {"contract_id": "contract-1", "season": 2028, "obligation_status": "scheduled"},
                ],
            ),
            active_season=2026,
        )
        self.assertEqual(result.future[0].free_agent_season, 2029)

    def test_canonical_one_year_contract_expires_next_season(self):
        result = build_free_agent_results(
            [player("p-1", "One Year")],
            state(
                contracts=[contract("p-1", id="contract-1", contract_years_left=4)],
                contract_seasons=[
                    {"contract_id": "contract-1", "season": 2026, "obligation_status": "active"},
                ],
            ),
            active_season=2026,
        )
        self.assertEqual(result.future[0].free_agent_season, 2027)

    def test_canonical_future_scheduled_season_sets_expiration(self):
        result = build_free_agent_results(
            [player("p-1", "Two Year")],
            state(
                contracts=[contract("p-1", id="contract-1", contract_years_left=1)],
                contract_seasons=[
                    {"contract_id": "contract-1", "season": 2026, "obligation_status": "active"},
                    {"contract_id": "contract-1", "season": 2027, "obligation_status": "scheduled"},
                ],
            ),
            active_season=2026,
        )
        self.assertEqual(result.future[0].free_agent_season, 2028)

    def test_legacy_expiration_is_used_without_usable_contract_seasons(self):
        result = build_free_agent_results(
            [player("p-1", "Legacy")],
            state(
                contracts=[contract("p-1", id="contract-1", contract_years_left=2)],
                contract_seasons=[
                    {"contract_id": "contract-1", "season": 2028, "obligation_status": "voided"},
                ],
            ),
            active_season=2026,
        )
        self.assertEqual(result.future[0].free_agent_season, 2028)

    def test_current_sleeper_metadata_overrides_team_and_position(self):
        result = build_free_agent_results(
            [player("p-1", "Current Player", pos="WR", nfl_team="NYJ")],
            state(sleeper_players=[{
                "sleeper_player_id": "p-1",
                "position": "RB",
                "team": "DEN",
                "status": "Active",
                "is_active": True,
            }]),
            active_season=2026,
        )
        self.assertEqual((result.current[0].position, result.current[0].nfl_team), ("RB", "DEN"))

    def test_current_sleeper_metadata_fills_blank_universe_metadata(self):
        result = build_free_agent_results(
            [player("11560", "Caleb Williams", pos=None, nfl_team=None)],
            state(sleeper_players=[{
                "sleeper_player_id": "11560",
                "position": "QB",
                "team": "CHI",
                "status": "Active",
                "is_active": True,
            }]),
            active_season=2026,
        )
        self.assertEqual((result.current[0].position, result.current[0].nfl_team), ("QB", "CHI"))

    def test_current_sleeper_metadata_replaces_stale_universe_metadata(self):
        result = build_free_agent_results(
            [player("12504", "Kaleb Johnson", pos="WR", nfl_team=None)],
            state(sleeper_players=[{
                "sleeper_player_id": "12504",
                "position": "RB",
                "team": "GB",
                "status": "Active",
                "is_active": True,
            }]),
            active_season=2026,
        )
        self.assertEqual((result.current[0].position, result.current[0].nfl_team), ("RB", "GB"))

    def test_current_sleeper_player_without_team_displays_fa(self):
        result = build_free_agent_results(
            [player("p-1", "NFL Free Agent", nfl_team="No team")],
            state(sleeper_players=[{
                "sleeper_player_id": "p-1",
                "position": "WR",
                "team": None,
                "status": "Active",
                "is_active": True,
            }]),
            active_season=2026,
        )
        self.assertEqual(result.current[0].nfl_team, "FA")
        self.assertNotEqual(result.current[0].nfl_team, "No team")

    def test_canonical_contract_season_cap_hit_overrides_agreement_salary(self):
        result = build_free_agent_results(
            [player("p-1", "Canonical Salary")],
            state(
                contracts=[contract("p-1", id="contract-1", salary=999)],
                contract_seasons=[
                    {
                        "contract_id": "contract-1",
                        "season": 2026,
                        "obligation_status": "active",
                        "salary": 20,
                        "cap_hit": 24,
                    },
                    {
                        "contract_id": "contract-1",
                        "season": 2027,
                        "obligation_status": "scheduled",
                        "salary": 25,
                        "cap_hit": 30,
                    },
                ],
            ),
            active_season=2026,
        )
        self.assertEqual(result.future[0].salary, 24)

    def test_canonical_salary_uses_earliest_future_scheduled_obligation(self):
        result = build_free_agent_results(
            [player("p-1", "Scheduled Salary")],
            state(
                contracts=[contract("p-1", id="contract-1", salary=None)],
                contract_seasons=[
                    {
                        "contract_id": "contract-1",
                        "season": 2027,
                        "obligation_status": "scheduled",
                        "salary": 21,
                        "cap_hit": 23,
                    },
                    {
                        "contract_id": "contract-1",
                        "season": 2028,
                        "obligation_status": "scheduled",
                        "salary": 24,
                        "cap_hit": 26,
                    },
                ],
            ),
            active_season=2026,
        )
        self.assertEqual(result.future[0].salary, 23)

    def test_future_rows_use_canonical_team_and_league_scoped_legacy_fallback(self):
        universe = [player("p-1", "Canonical"), player("p-2", "Legacy")]
        contracts = [contract("p-1"), contract("p-2", league_team_id=None, owner_name="Legacy Team")]
        teams = [{"id": "team-1", "league_id": "league-1", "team_name": "Canonical Team"}]
        result = build_free_agent_results(universe, state(contracts=contracts, teams=teams), active_season=2026)
        names = {row.sleeper_player_id: row.contracted_team for row in result.future}
        self.assertEqual(names, {"p-1": "Canonical Team", "p-2": "Legacy Team"})
        self.assertIn("internal:legacy_contract_team_label_fallback", result.warnings)

    def test_open_market_ranking_uses_previous_season_ppg_before_active_scoring(self):
        universe = [
            player("p-1", "Lower Last PPG"),
            player("p-2", "Higher Last PPG"),
            player("p-3", "No PPG", nfl_team=None),
        ]
        ppg = load_ranking_ppg(FakeClient({"player_season_stats": [
            {"sleeper_id": "p-1", "season": 2025, "games": 17, "fantasy_ppg_ppr": 10},
            {"sleeper_id": "p-2", "season": 2025, "games": 17, "fantasy_ppg_ppr": 20},
            {"sleeper_id": "p-1", "season": 2026, "games": 0, "fantasy_ppg_ppr": 99},
        ]}), active_season=2026)
        self.assertFalse(ppg.active_season_started)
        self.assertEqual(ppg.ranking_ppg, {"p-1": 10.0, "p-2": 20.0})
        result = build_free_agent_results(
            universe,
            state(),
            active_season=2026,
            lifetime_points_by_player={"p-1": 900, "p-2": 5, "p-3": 9999},
            last_season_ppg_by_player=ppg.last_ppg,
            current_season_ppg_by_player=ppg.current_ppg,
            ranking_ppg_by_player=ppg.ranking_ppg,
        )
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-2", "p-1", "p-3"])

    def test_open_market_ranking_switches_to_active_season_ppg(self):
        ppg = load_ranking_ppg(FakeClient({"player_season_stats": [
            {"sleeper_id": "p-1", "season": 2025, "games": 17, "fantasy_ppg_ppr": 10},
            {"sleeper_id": "p-2", "season": 2025, "games": 17, "fantasy_ppg_ppr": 20},
            {"sleeper_id": "p-1", "season": 2026, "games": 1, "fantasy_ppg_ppr": 25},
            {"sleeper_id": "p-2", "season": 2026, "games": 1, "fantasy_ppg_ppr": 5},
        ]}), active_season=2026)
        self.assertTrue(ppg.active_season_started)
        self.assertEqual(ppg.ranking_ppg, {"p-1": 25.0, "p-2": 5.0})
        result = build_free_agent_results(
            [player("p-1", "Current Leader"), player("p-2", "Last Leader")],
            state(), active_season=2026,
            last_season_ppg_by_player=ppg.last_ppg,
            current_season_ppg_by_player=ppg.current_ppg,
            ranking_ppg_by_player=ppg.ranking_ppg,
        )
        self.assertEqual([row.sleeper_player_id for row in result.current], ["p-1", "p-2"])

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

    def test_page_uses_season_ppg_columns_and_clears_loader_before_render(self):
        source = (ROOT / "pages/04_Free_Agent.py").read_text()
        for label in ("Open Market", "Expiring Contracts", "Rookie Class"):
            self.assertIn(label, source)
        self.assertNotIn('f"●  {season}"', source)
        self.assertNotIn("fa-timeline-line", source)
        self.assertIn("position: fixed; inset: 0", source)
        self.assertIn("Last Season PPG", source)
        self.assertIn("Current Season PPG", source)
        self.assertIn("No games yet", source)
        clear_after_load = source.index("loading_placeholder.empty()", source.index("results = build_free_agent_results"))
        self.assertLess(clear_after_load, source.index('<div class="fa-hero">'))

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
