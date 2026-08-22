from __future__ import annotations

from decimal import Decimal
import unittest

from contract_engine.compatibility import project_legacy_contracts
from contract_engine.models import money
from contract_engine.planner import build_backfill_plan, covered_seasons
from contract_engine.service import ContractBackfillService
from season_engine.models import LeagueSeason
from dataclasses import replace
from pathlib import Path


ACTIVE=LeagueSeason("s25","l1",2025,"sl25",True)

def legacy(i=1,**changes):
    row={"id":f"c{i}","league_id":"l1","owner_name":"Owner","sleeper_player_id":f"p{i}",
         "player_name":f"Player {i}","contract_years_left":i,"contract_total_years":i,"salary":f"{i}.50","is_rookie":False}
    row.update(changes); return row
def team(**changes):
    row={"id":"t1","league_id":"l1","owner_name":"Owner"}; row.update(changes); return row
def player(i=1): return {"sleeper_id":f"p{i}","player_name":f"Player {i}"}
def plan(rows=None,teams=None,players=None,seasons=None):
    return build_backfill_plan(active_season=ACTIVE,legacy_contracts=[legacy()] if rows is None else rows,
        league_teams=[team()] if teams is None else teams,players=[player()] if players is None else players,
        league_seasons=[{"league_id":"l1","season":2025},{"league_id":"l1","season":2026}] if seasons is None else seasons)

class ContractPlannerTest(unittest.TestCase):
    def test_years_remaining_convention(self):
        self.assertEqual(covered_seasons(2025,1),(2025,))
        self.assertEqual(covered_seasons(2025,2),(2025,2026))
        self.assertEqual(covered_seasons(2025,3),(2025,2026,2027))
    def test_invalid_years_rejected(self):
        with self.assertRaises(ValueError): covered_seasons(2025,0)
    def test_flat_salary_and_fixed_precision(self):
        p=plan([legacy(3)],players=[player(3)])
        self.assertEqual([x["salary"] for x in p.contract_seasons],["3.50"]*3)
        self.assertEqual(money("16.5"),Decimal("16.50"))
    def test_future_2027_is_scheduled_reference_requirement(self):
        p=plan([legacy(3)],players=[player(3)])
        self.assertEqual(p.future_league_seasons,(2027,))
        self.assertEqual([x["obligation_status"] for x in p.contract_seasons],["active","scheduled","scheduled"])
    def test_team_reconciliation(self):
        self.assertEqual(plan().agreements[0]["league_team_id"],"t1")
    def test_missing_player_blocks(self):
        self.assertFalse(plan(players=[]).safe_to_apply)
    def test_duplicate_legacy_source_rejected(self):
        self.assertIn("duplicate_legacy_source",{x["code"] for x in plan([legacy(),legacy()]).blocking_errors})
    def test_overlap_rejected(self):
        p=plan([legacy(),legacy(2,sleeper_player_id="p1")],players=[player()])
        self.assertIn("overlapping_current_contract",{x["code"] for x in p.blocking_errors})
    def test_cross_league_rejected(self):
        self.assertFalse(plan(teams=[team(league_id="l2")]).safe_to_apply)
    def test_import_event_is_minimal_and_deterministic(self):
        event=plan().events[0]
        self.assertEqual(event["event_type"],"imported")
        self.assertEqual(event["idempotency_key"],"contract-import:c1:v1")
    def test_compatibility_projection_has_legacy_terms(self):
        p=plan([legacy(2)],players=[player(2)])
        row=project_legacy_contracts(list(p.agreements),list(p.contract_seasons),season=2025)[0]
        self.assertEqual((row["id"],row["salary"],row["contract_years_left"]),("c2","2.50",2))
    def test_input_is_not_mutated(self):
        rows=[legacy()]; before=dict(rows[0]); plan(rows); self.assertEqual(rows[0],before)

    def test_migration_enforces_append_only_and_controlled_updates(self):
        sql=(Path(__file__).parents[1]/"supabase/migrations/20260731_contract_data_model.sql").read_text()
        self.assertIn("Contract events are append-only",sql)
        self.assertIn("Contract obligations require an audited contract-edit RPC",sql)
        self.assertIn("revoke all on table",sql)
        self.assertIn("211 agreements, 335 contract seasons, and 211 import events",sql)


class Rpc:
    def __init__(self,client): self.client=client
    def execute(self): self.client.executed+=1; return type("R",(),{"data":{"ok":True}})()
class Client:
    def __init__(self): self.rpc_calls=0; self.executed=0
    def rpc(self,*args,**kwargs): self.rpc_calls+=1; return Rpc(self)
class StubService(ContractBackfillService):
    def __init__(self,client,p): super().__init__(client); self.p=p
    def plan(self,league_id): return self.p

class ContractServiceTest(unittest.TestCase):
    def test_dry_run_and_blocked_apply_write_nothing(self):
        blocked=plan([legacy(),legacy(2,sleeper_player_id="p1")],players=[player()]); client=Client(); service=StubService(client,blocked)
        self.assertIs(service.backfill("l1",dry_run=True),blocked)
        with self.assertRaises(ValueError): service.backfill("l1",dry_run=False)
        self.assertEqual(client.rpc_calls,0)
    def test_safe_apply_uses_rpc(self):
        safe=plan(); client=Client(); result=StubService(client,safe).backfill("l1",dry_run=False)
        self.assertEqual((client.rpc_calls,client.executed),(1,1)); self.assertEqual(result,{"ok":True})

if __name__=="__main__": unittest.main()
