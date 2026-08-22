from __future__ import annotations

import unittest

from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.football_intelligence import (
    FootballIntelligenceService,
    normalize_lineup_slot,
    normalize_player_position,
)
from gm_assistant.football_intelligence.rules import (
    AGE_RISK_THRESHOLDS,
    CONTRACT_CLIFF_RATIO,
    PLAYER_SALARY_CONCENTRATION_RATIO,
    POSITION_SALARY_CONCENTRATION_RATIO,
)
from gm_assistant.interpretation import Intent, interpret_question, is_football_intelligence_question
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def in_(self,key,values):
        self.filters.append(("__in__",(key,{str(x) for x in values})));return self

    def execute(self):
        self.client.selects.append((self.table_name, list(self.filters), self.limit_value))
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            if key=="__in__":rows=[row for row in rows if str(row.get(value[0])) in value[1]]
            else:rows = [row for row in rows if str(row.get(key)) == str(value)]
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)


class FakeClient:
    gm_contract_read_mode="legacy"
    def __init__(self):
        self.selects = []
        self.rows = fake_rows()

    def table(self, table_name):
        return FakeTable(self, table_name)


def context(**overrides):
    data = {
        "user_id": "user-1",
        "league_id": "league-1",
        "league_team_id": "team-1",
        "membership_id": "membership-1",
        "role": "owner",
        "current_season": 2026,
        "requested_season": 2026,
        "permission_scopes": (TEAM_ADVICE, LEAGUE_PUBLIC_READ),
        "conversation_id": "conversation-1",
        "team_name": "Condor Dynasty",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


def fake_rows():
    roster = [
        player("p1", "Josh Allen", "QB", age=30, exp=8, salary=45, years=3),
        player("p2", "Quarterback Two", "QB", age=24, exp=1, salary=3, years=2),
        player("p3", "Rookie Runner", "RB", status="taxi", age=21, exp=0, salary=2, years=4),
        player("p4", "Veteran Runner", "RB", age=28, exp=6, salary=18, years=1),
        player("p5", "Depth Runner", "RB", age=25, exp=3, salary=4, years=1),
        player("p6", "Alpha Receiver", "WR", age=26, exp=4, salary=55, years=3),
        player("p7", "Second Receiver", "WR", age=25, exp=3, salary=16, years=2),
        player("p8", "Third Receiver", "WR", age=24, exp=2, salary=8, years=1),
        player("p9", "Young Receiver", "WR", age=22, exp=1, salary=5, years=4),
        player("p10", "Taxi Receiver", "WR", status="taxi", age=21, exp=0, salary=1, years=4),
        player("p11", "Starter Tight End", "TE", age=31, exp=7, salary=14, years=1),
        player("p12", "Injured Tight End", "TE", status="ir", age=29, exp=5, salary=10, years=1),
        player("p13", "Kicker One", "K", age=30, exp=6, salary=1, years=1),
        player("p14", "Defense One", "DEF", salary=1, years=1),
        player("p15", "Linebacker One", "LB", age=27, exp=4, salary=2, years=2),
        player("p16", "Linebacker Two", "LB", age=23, exp=1, salary=1, years=3),
        player("p17", "Defensive Back One", "DB", age=29, exp=5, salary=2, years=1),
        player("p18", "Defensive Line One", "DL", age=26, exp=3, salary=2, years=2),
        player("p19", "Bench Receiver", "WR", age=27, exp=5, salary=3, years=1),
        player("p20", "Bench Runner", "RB", age=24, exp=2, salary=3, years=2),
        player("p21", "Bench Tight End", "TE", age=31, exp=2, salary=3, years=3),
        player("p22", "Bench Quarterback", "QB", age=36, exp=12, salary=6, years=1),
        player("cut", "Released Player", "RB", status="released", age=26, exp=4, salary=7, years=1),
    ]
    contracts = [
        {
            "league_id": row["league_id"],
            "league_team_id": row["team_id"],
            "owner_name": "Owner One",
            "sleeper_player_id": row["sleeper_id"],
            "player_name": row["player_name"],
            "player_position": row["position"],
            "salary": row.get("salary"),
            "contract_years_left": row.get("contract_years_left"),
            "status": row["status"],
        }
        for row in roster
    ]
    player_intel = [
        {"sleeper_id": row["sleeper_id"], "player_name": row["player_name"], "position": row["position"], "age": row.get("age"), "experience": row.get("experience"), "is_rookie": row.get("is_rookie")}
        for row in roster
    ]
    return {
        "league_teams": [
            {"id": "team-1", "league_id": "league-1", "team_name": "Condor Dynasty", "owner_name": "Owner One"},
            {"id": "team-2", "league_id": "league-1", "team_name": "Rival Team", "owner_name": "Owner Two"},
            {"id": "team-x", "league_id": "league-2", "team_name": "Condor Dynasty", "owner_name": "Owner X"},
        ],
        "team_roster_state": roster + [player("leak", "Cross League QB", "QB", league_id="league-2", team_id="team-x")],
        "contracts": contracts + [{"league_id": "league-2", "league_team_id": "team-x", "owner_name": "Owner X", "sleeper_player_id": "leak", "player_name": "Cross League QB", "player_position": "QB", "salary": 99, "contract_years_left": 1}],
        "league_seasons":[{"id":"ls26","league_id":"league-1","season":2026,"is_active":True,"status":"active"}],
        "contract_transition_executions":[],
        "contract_agreements":[{"id":f"a-{r['sleeper_id']}","league_id":"league-1","league_team_id":"team-1","player_id":r["sleeper_id"],"sleeper_player_id":r["sleeper_id"],"status":"active","end_season":2026+max(int(r.get("contract_years_left") or 1)-1,0),"contract_type":"veteran","source_legacy_contract_id":f"c-{r['sleeper_id']}"} for r in roster if r["status"]!="released"],
        "contract_seasons":[{"id":f"s-{r['sleeper_id']}-{year}","contract_id":f"a-{r['sleeper_id']}","league_id":"league-1","league_team_id":"team-1","player_id":r["sleeper_id"],"season":year,"salary":r.get("salary") or 0,"obligation_status":"active" if year==2026 else "scheduled"} for r in roster if r["status"]!="released" for year in range(2026,2026+int(r.get("contract_years_left") or 1))],
        "contract_events":[],
        "player_universe":[{"sleeper_id":r["sleeper_id"],"player_name":r["player_name"],"pos":r["position"]} for r in roster if r["status"]!="released"],
        "season_roster_assignments":[{"league_season_id":"ls26","league_team_id":"team-1","sleeper_player_id":r["sleeper_id"],"roster_designation":r["status"]} for r in roster if r["status"]!="released"],
        "free_agents":[],
        "v_team_caps": [{"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "available_cap": 12}],
        "league_rules": [{"league_id": "league-1", "qb": 1, "rb": 2, "wr": 3, "te": 1, "k": 2, "flex": 2, "superflex": 1, "bench": 10, "taxi": 4, "ir": 3, "salary_cap": 200}],
        "league_settings": [],
        "draft_picks": [
            {"league_id": "league-1", "season": 2028, "round": 1, "current_owner": "Owner One", "original_team": "Owner One", "pick_label": "2028 1st"},
            {"league_id": "league-1", "season": 2028, "round": 2, "current_owner": "Owner One", "original_team": "Rival Team", "pick_label": "2028 2nd"},
            {"league_id": "league-2", "season": 2028, "round": 1, "current_owner": "Owner X", "pick_label": "2028 1st"},
        ],
        "player_intelligence": player_intel,
        "player_strategic_profiles": [{"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": "p1", "player_name": "Josh Allen", "league_value_tier": "stored-tier"}],
        "league_relative_player_values": [],
        "transaction_ledger": [],
        "transactions_enriched": [],
        "team_brain": [],
        "league_brain": [],
        "rookie_draft_board": [],
        "player_prospect_context": [],
        "rookie_class_registry": [],
        "rookie_draft_results": [],
        "draft_selections": [],
        "cap_adjustments": [],
    }


def player(player_id, name, position, *, status="active", age=None, exp=None, salary=None, years=None, league_id="league-1", team_id="team-1"):
    return {
        "league_id": league_id,
        "team_id": team_id,
        "league_team_id": team_id,
        "sleeper_id": player_id,
        "player_name": name,
        "position": position,
        "status": status,
        "age": age,
        "experience": exp,
        "is_rookie": exp == 0 if exp is not None else None,
        "salary": salary,
        "contract_years_left": years,
    }


class FootballIntelligenceTest(unittest.TestCase):
    def test_position_normalization_distinguishes_player_position_from_lineup_slot(self):
        self.assertEqual(normalize_player_position("def"), "DST")
        self.assertEqual(normalize_player_position("CB"), "DB")
        self.assertIsNone(normalize_player_position("superflex"))
        self.assertEqual(normalize_lineup_slot("superflex"), "SUPERFLEX")
        self.assertEqual(normalize_lineup_slot("RB/WR/TE"), "FLEX")

    def test_rule_thresholds_are_documented_constants(self):
        self.assertEqual(CONTRACT_CLIFF_RATIO, 0.50)
        self.assertEqual(POSITION_SALARY_CONCENTRATION_RATIO, 0.35)
        self.assertEqual(PLAYER_SALARY_CONCENTRATION_RATIO, 0.25)
        self.assertEqual(AGE_RISK_THRESHOLDS["RB"], 27)

    def test_service_builds_22_player_roster_without_released_or_cross_league_rows(self):
        ctx = FootballIntelligenceService(FakeClient()).get_context(context=context())
        roster = ctx.roster_construction

        self.assertEqual(ctx.availability, "available")
        self.assertEqual(roster.roster_count, 22)
        self.assertEqual(roster.active_roster_count, 19)
        self.assertFalse(any(player.player_name == "Released Player" for group in roster.position_groups for player in group.players))
        self.assertFalse(any(player.player_name == "Cross League QB" for group in roster.position_groups for player in group.players))

    def test_lineup_requirements_superflex_not_counted_as_direct_qb_requirement(self):
        ctx = FootballIntelligenceService(FakeClient()).get_context(context=context())
        qb = ctx.group("QB")

        self.assertEqual(qb.required_starters, 1)
        self.assertEqual(qb.active_count, 3)
        self.assertTrue(any(item.slot == "SUPERFLEX" and item.eligible_player_positions[0] == "QB" for item in ctx.roster_construction.lineup_rules.starter_slots))

    def test_missing_and_malformed_lineup_rules_degrade_to_partial(self):
        client = FakeClient()
        client.rows["league_rules"] = [{"league_id": "league-1", "qb": -1, "rb": "bad"}]

        ctx = FootballIntelligenceService(client).get_context(context=context())

        self.assertEqual(ctx.roster_construction.lineup_rules.availability, "partial")
        self.assertIn("negative_lineup_count:qb", ctx.warnings)
        self.assertIn("malformed_lineup_count:rb", ctx.warnings)

    def test_position_depth_strengths_and_needs_are_rule_based(self):
        ctx = FootballIntelligenceService(FakeClient()).get_context(context=context())
        labels = {item.label for item in ctx.roster_construction.strengths + ctx.roster_construction.needs}

        self.assertIn("QB depth coverage", labels)
        self.assertIn("TE depth coverage", labels)

    def test_contract_age_salary_and_draft_risks_are_deterministic(self):
        ctx = FootballIntelligenceService(FakeClient()).get_context(context=context())
        risk_ids = {risk.rule_id for risk in ctx.roster_construction.risks}

        self.assertIn("contract_cliff.v1", risk_ids)
        self.assertIn("position_salary_concentration.v1", risk_ids)
        self.assertIn("single_player_salary_concentration.v1", risk_ids)
        self.assertIn("age_concentration.v1", risk_ids)
        self.assertEqual(ctx.roster_construction.draft_flexibility.future_first_count, 1)

    def test_no_future_premium_picks_creates_limited_pick_need(self):
        client = FakeClient()
        client.rows["draft_picks"] = []

        ctx = FootballIntelligenceService(client).get_context(context=context())

        self.assertIn("future_pick_limitation.v1", {need.rule_id for need in ctx.roster_construction.needs})

    def test_partial_player_intelligence_does_not_treat_missing_data_as_bad(self):
        client = FakeClient()
        client.rows["player_intelligence"] = []

        ctx = FootballIntelligenceService(client).get_context(context=context())

        self.assertEqual(ctx.roster_construction.roster_count, 22)
        self.assertIn("player_intelligence", ctx.completeness)

    def test_strategy_goal_changes_framing_not_metrics(self):
        client = FakeClient()
        service = FootballIntelligenceService(client)

        contender = service.get_context(context=context(), owner_goal="win_now")
        rebuild = service.get_context(context=context(), owner_goal="rebuild")

        self.assertEqual(len(contender.roster_construction.needs), len(rebuild.roster_construction.needs))
        self.assertEqual(len(contender.roster_construction.risks), len(rebuild.roster_construction.risks))
        self.assertNotEqual(contender.owner_goal, rebuild.owner_goal)

    def test_compare_contexts_is_read_only_and_structural(self):
        service = FootballIntelligenceService(FakeClient())
        before = service.get_context(context=context())
        after = before.__class__(**{**before.__dict__, "roster_construction": before.roster_construction.__class__(**{**before.roster_construction.__dict__, "roster_count": before.roster_construction.roster_count - 1})})

        comparison = service.compare_contexts(before, after)

        self.assertEqual(comparison["availability"], "available")
        self.assertEqual(comparison["roster_count_delta"], -1)

    def test_service_uses_bounded_read_queries_only(self):
        client = FakeClient()

        FootballIntelligenceService(client).get_context(context=context())

        self.assertLessEqual(len(client.selects), 20)
        self.assertFalse(any(table.startswith("insert") for table, _filters, _limit in client.selects))

    def test_football_questions_classify_as_data_lookup(self):
        questions = [
            "What are my biggest roster needs?",
            "Where is my roster thinnest?",
            "How is my QB depth?",
            "Do I have contract risk?",
            "Is this roster balanced?",
            "What does my roster construction mean?",
        ]
        for question in questions:
            with self.subTest(question=question):
                interpreted = interpret_question(question, context())
                self.assertEqual(interpreted.primary_intent, Intent.DATA_LOOKUP.value)
                self.assertTrue(is_football_intelligence_question(question))

    def test_pipeline_answers_needs_as_direct_fact_without_recommendation(self):
        result = run_assistant_pipeline(
            context=context(),
            question="What are my biggest roster needs?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
        )

        self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
        self.assertIn("structural needs", result.displayed_answer.lower())
        self.assertNotIn("validated recommendation", result.displayed_answer.lower())

    def test_pipeline_answers_position_depth_without_external_rankings(self):
        result = run_assistant_pipeline(
            context=context(),
            question="How is my QB depth?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
        )

        self.assertEqual(result.decision_plan.response_mode, "direct_factual")
        self.assertIn("QB room", result.displayed_answer)
        self.assertNotIn("projection", result.displayed_answer.lower())

    def test_pipeline_contract_answer_excludes_recommendation_language(self):
        result = run_assistant_pipeline(
            context=context(),
            question="Do I have contract risk?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
        )

        self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
        self.assertIn("Verified contract", result.displayed_answer)
        self.assertNotIn("DecisionOutput", result.displayed_answer)

    def test_zero_roster_returns_partial_context(self):
        client = FakeClient()
        client.rows["team_roster_state"] = []
        client.rows["contracts"] = []
        client.rows["contract_agreements"]=[];client.rows["contract_seasons"]=[];client.rows["season_roster_assignments"]=[]
        client.rows["player_strategic_profiles"] = []

        ctx = FootballIntelligenceService(client).get_context(context=context())

        self.assertEqual(ctx.availability, "partial")
        self.assertEqual(ctx.roster_construction.roster_count, 0)


if __name__ == "__main__":
    unittest.main()
