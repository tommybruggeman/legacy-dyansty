import unittest
from decimal import Decimal
from pathlib import Path

from services.team_roster_state import (
    CanonicalTeamStateError, calculate_team_financials,
    dead_cap_display_rows,
    invalidate_team_state_session_cache, load_team_state, merge_activity,
    state_activity, state_cap_adjustments, state_roster,
)

ROOT = Path(__file__).resolve().parents[1]

class Response:
    def __init__(self,data):self.data=data
class Rpc:
    def __init__(self,payload,error=None):self.payload=payload;self.error=error
    def execute(self):
        if self.error:raise self.error
        return Response(self.payload)
class Client:
    def __init__(self,payload,error=None):self.payload=payload;self.error=error;self.calls=[]
    def rpc(self,name,params):self.calls.append((name,params));return Rpc(self.payload,self.error)

def snapshot():
    return {"schema":"canonical-team-state-v1","league_id":"l1","season":2026,"scope_team_id":"t1","teams":[{"league_team_id":"t1","owner_name":"Chase","team_name":"Chase Seyforth"}],"roster":[{"contract_agreement_id":"a1","league_team_id":"t1","player_id":"p1","sleeper_player_id":"p1","player_name":"Added Player","pos":"WR","owner_name":"Chase","team_name":"Chase Seyforth","cap_hit":"7.00","contract_years_left":2}],"dead_cap":[{"id":"d1","league_team_id":"t1","player_id":"11628","player_name":"Marvin Harrison Jr.","owner_name":"Chase","team_name":"Chase Seyforth","season":2026,"amount":"20.50","adjustment_type":"dropped_player_charge"}],"activity":[{"id":"e1","league_team_id":"t1","player_id":"p1","player_name":"Added Player","owner_name":"Chase","team_name":"Chase Seyforth","action":"add","effective_at":"2026-01-01T00:00:00Z"},{"id":"e2","league_team_id":"t1","player_id":"11628","player_name":"Marvin Harrison Jr.","owner_name":"Chase","team_name":"Chase Seyforth","action":"drop","effective_at":"2026-01-02T00:00:00Z"}],"cap_adjustments":[{"id":"legacy","league_team_id":"t1","owner_name":"Chase","player_name":"IR Player","season":2026,"amount":"2","adjustment_type":"ir_adjustment"}]}

class TeamStateTests(unittest.TestCase):
    def test_rpc_is_only_canonical_read_and_is_team_scoped(self):
        client=Client(snapshot());state=load_team_state(client,"l1",2026,"t1")
        self.assertEqual(client.calls,[("read_canonical_team_state_authenticated",{"p_request":{"league_id":"l1","season":2026,"league_team_id":"t1"}})])
        self.assertEqual(state["scope_team_id"],"t1")
    def test_names_roster_terms_dead_cap_and_activity(self):
        state=snapshot();roster=state_roster(state);activity=state_activity(state);adjustments=state_cap_adjustments(state)
        self.assertEqual(roster[0]["player"],"Added Player");self.assertEqual(roster[0]["years"],2)
        self.assertEqual(adjustments[0]["player_name"],"Marvin Harrison Jr.")
        self.assertEqual(dead_cap_display_rows(adjustments),[("Marvin Harrison Jr.",Decimal("20.50"))])
        self.assertEqual([r["action"] for r in activity],["add","drop"])
    def test_financials_use_canonical_cap_hit_and_dead_cap(self):
        f=calculate_team_financials(state_roster(snapshot()),state_cap_adjustments(snapshot()),salary_cap=225,league_team_id="t1")
        self.assertEqual(f["active_salary"],Decimal("7.00"));self.assertEqual(f["dead_cap"],Decimal("20.50"));self.assertEqual(f["cap_used"],Decimal("29.50"));self.assertEqual(f["cap_space"],Decimal("195.50"))
    def test_canonical_activity_deduplicates_legacy_add_drop(self):
        canonical=state_activity(snapshot());legacy=[{"id":"old","league_team_id":"t1","player_id":"p1","action":"add"},{"id":"history","league_team_id":"t1","player_id":"x","action":"trade"}]
        merged=merge_activity(canonical,legacy);self.assertEqual(sum(r.get("action")=="add" for r in merged),1);self.assertIn("history",[r.get("id") for r in merged])
    def test_read_failure_and_invalid_payload_fail_closed(self):
        with self.assertRaises(CanonicalTeamStateError):load_team_state(Client(None,RuntimeError("403")),"l1",2026)
        with self.assertRaises(CanonicalTeamStateError):load_team_state(Client([]),"l1",2026)
    def test_targeted_cache_epoch(self):
        state={};self.assertEqual(invalidate_team_state_session_cache(state),1);self.assertEqual(invalidate_team_state_session_cache(state),2)
    def test_no_direct_canonical_table_reads(self):
        source=(ROOT/"services/team_roster_state.py").read_text()
        for table in("contract_agreements","contract_seasons","contract_events","dead_cap_obligations"):
            self.assertNotIn(f'table("{table}")',source)
    def test_migration_security_shape(self):
        sql=(ROOT/"supabase/migrations/20261030_authenticated_canonical_team_state_read.sql").read_text().lower()
        self.assertIn("security definer",sql);self.assertIn("set search_path=pg_catalog,public",sql)
        self.assertIn("grant execute on function public.read_canonical_team_state_authenticated(jsonb) to authenticated",sql)
        self.assertNotIn("grant select on",sql);self.assertIn("team_state_cross_team_forbidden",sql)
    def test_both_pages_render_only_dead_cap_name_and_amount(self):
        for page in ("pages/02_My_Team.py","pages/03_Teams.py"):
            source=(ROOT/page).read_text();self.assertIn("dead_cap_display_rows",source)
        teams=(ROOT/"pages/03_Teams.py").read_text()
        self.assertNotIn("f'<small>{season}</small>'",teams)

if __name__=="__main__":unittest.main()
