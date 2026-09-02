import unittest
from decimal import Decimal
from pathlib import Path

from services.team_roster_state import (
    CanonicalTeamStateError, calculate_team_financials, cap_adjustment_display_rows,
    dead_cap_display_rows,
    invalidate_team_state_session_cache, load_team_state, merge_activity,
    roster_designation,
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
    return {"schema":"canonical-team-state-v1","league_id":"l1","season":2026,"scope_team_id":"t1","teams":[{"league_team_id":"t1","owner_name":"Chase","team_name":"Chase Seyforth"}],"roster":[{"contract_agreement_id":"a1","league_team_id":"t1","player_id":"p1","sleeper_player_id":"p1","player_name":"Added Player","pos":"WR","owner_name":"Chase","team_name":"Chase Seyforth","cap_hit":"7.00","contract_years_left":2,"is_rookie":True,"roster_designation":"TAXI"}],"dead_cap":[{"id":"d1","league_team_id":"t1","player_id":"11628","player_name":"Marvin Harrison Jr.","owner_name":"Chase","team_name":"Chase Seyforth","season":2026,"amount":"20.50","adjustment_type":"dropped_player_charge"}],"activity":[{"id":"e1","league_team_id":"t1","player_id":"p1","player_name":"Added Player","owner_name":"Chase","team_name":"Chase Seyforth","action":"add","effective_at":"2026-01-01T00:00:00Z"},{"id":"e2","league_team_id":"t1","player_id":"11628","player_name":"Marvin Harrison Jr.","owner_name":"Chase","team_name":"Chase Seyforth","action":"drop","effective_at":"2026-01-02T00:00:00Z"}],"cap_adjustments":[{"id":"legacy","league_team_id":"t1","owner_name":"Chase","player_name":"IR Player","season":2026,"amount":"2","adjustment_type":"ir_adjustment"}],"draft_picks":[]}

class TeamStateTests(unittest.TestCase):
    def test_retained_salary_splits_cap_without_double_counting(self):
        data=snapshot()
        data["roster"]=[
            {"contract_agreement_id":"c1","league_team_id":"t2","owner_name":"B","cap_hit":30,"salary":30},
        ]
        data["dead_cap"]=[]
        data["cap_adjustments"]=[]
        data["retained_salary"]=[
            {"contract_agreement_id":"c1","retaining_league_team_id":"t1","player_id":"p1","player_name":"Player One","season":2026,"amount":10,"status":"active"},
        ]
        state=load_team_state(Client(data),"l1",2026)
        self.assertEqual(state["roster"][0]["cap_hit"],Decimal("20"))
        retained=[row for row in state["dead_cap"]if row.get("adjustment_type")=="trade_retained_salary"]
        self.assertEqual(retained[0]["amount"],10)
        financials=calculate_team_financials(state_roster(state),state_cap_adjustments(state),salary_cap=100,league_team_id="t2")
        self.assertEqual(financials["active_salary"],Decimal("20"))
        payer=calculate_team_financials(state_roster(state),state_cap_adjustments(state),salary_cap=100,league_team_id="t1")
        self.assertEqual(payer["cap_used"]+financials["cap_used"],Decimal("30"))

    def test_rpc_is_only_canonical_read_and_is_team_scoped(self):
        client=Client(snapshot());state=load_team_state(client,"l1",2026,"t1")
        self.assertEqual(client.calls,[("read_canonical_team_state_authenticated",{"p_request":{"league_id":"l1","season":2026,"league_team_id":"t1"}})])
        self.assertEqual(state["scope_team_id"],"t1")
    def test_names_roster_terms_dead_cap_and_activity(self):
        state=snapshot();roster=state_roster(state);activity=state_activity(state);adjustments=state_cap_adjustments(state)
        self.assertEqual(roster[0]["player"],"Added Player");self.assertEqual(roster[0]["years"],2)
        self.assertTrue(roster[0]["is_rookie"]);self.assertEqual(roster[0]["roster_designation"],"taxi")
        self.assertEqual(adjustments[0]["player_name"],"Marvin Harrison Jr.")
        self.assertEqual(dead_cap_display_rows(adjustments),[("Marvin Harrison Jr.",Decimal("20.50"))])
        self.assertEqual([r["action"] for r in activity],["add","drop"])
    def test_cap_adjustment_display_rows_include_trade_direction(self):
        rows=[
            {"adjustment_type":"trade_carryover","amount":"4","counterparty_owner":"Mekel Sanchez"},
            {"adjustment_type":"trade_carryover","amount":"-4","counterparty_owner":"Nando Munoz"},
            {"adjustment_type":"dropped_player_charge","amount":"2","player_name":"Cole Kmet"},
            {"adjustment_type":"trade_retained_salary","amount":"4.5","player_name":"Player One","season":2026},
        ]
        self.assertEqual(cap_adjustment_display_rows(rows),[
            ("$4 to Mekel Sanchez",None),("+$4 from Nando Munoz",None),
            ("Cole Kmet","$2.00"),("Player One — Retained Salary (2026)","$4.50"),
        ])
    def test_financials_use_canonical_cap_hit_and_dead_cap(self):
        f=calculate_team_financials(state_roster(snapshot()),state_cap_adjustments(snapshot()),salary_cap=225,league_team_id="t1")
        self.assertEqual(f["active_salary"],Decimal("7.00"));self.assertEqual(f["dead_cap"],Decimal("20.50"));self.assertEqual(f["cap_used"],Decimal("29.50"));self.assertEqual(f["cap_space"],Decimal("195.50"))
    def test_signed_trade_carryover_adjusts_cap_used_and_space(self):
        roster=[{"league_team_id":"t1","cap_hit":"183"}]
        adjustments=[
            {"league_team_id":"t1","amount":"2","adjustment_type":"dropped_player_charge"},
            {"league_team_id":"t1","amount":"-4","adjustment_type":"trade_carryover"},
        ]
        credit=calculate_team_financials(roster,adjustments,salary_cap=225,league_team_id="t1")
        self.assertEqual(credit["active_salary"],Decimal("183"));self.assertEqual(credit["cap_used"],Decimal("181"));self.assertEqual(credit["cap_space"],Decimal("44"))
        adjustments[1]["amount"]="4"
        charge=calculate_team_financials(roster,adjustments,salary_cap=225,league_team_id="t1")
        self.assertEqual(charge["cap_used"],Decimal("189"));self.assertEqual(charge["cap_space"],Decimal("36"))
    def test_teams_canonical_state_is_not_streamlit_cached(self):
        source=(ROOT/"pages"/"03_Teams.py").read_text()
        definition="def load_canonical_team_state_current(season: int, cache_epoch: int) -> dict:"
        self.assertIn(definition,source)
        prefix=source[:source.index(definition)].rstrip().splitlines()[-1]
        self.assertFalse(prefix.startswith("@st.cache_data"))
    def test_my_team_canonical_state_is_not_streamlit_cached(self):
        source=(ROOT/"pages"/"02_My_Team.py").read_text()
        definition="def load_canonical_team_state(league_id: str, season: int, team_id: str, cache_epoch: int) -> dict:"
        self.assertIn(definition,source)
        prefix=source[:source.index(definition)].rstrip().splitlines()[-1]
        self.assertFalse(prefix.startswith("@st.cache_data"))
    def test_canonical_activity_deduplicates_legacy_add_drop(self):
        canonical=state_activity(snapshot());legacy=[{"id":"old","league_team_id":"t1","player_id":"p1","action":"add"},{"id":"history","league_team_id":"t1","player_id":"x","action":"trade"}]
        merged=merge_activity(canonical,legacy);self.assertEqual(sum(r.get("action")=="add" for r in merged),1);self.assertIn("history",[r.get("id") for r in merged])
    def test_read_failure_and_invalid_payload_fail_closed(self):
        with self.assertRaises(CanonicalTeamStateError):load_team_state(Client(None,RuntimeError("403")),"l1",2026)
        with self.assertRaises(CanonicalTeamStateError):load_team_state(Client([]),"l1",2026)
    def test_targeted_cache_epoch(self):
        state={};self.assertEqual(invalidate_team_state_session_cache(state),1);self.assertEqual(invalidate_team_state_session_cache(state),2)
    def test_roster_designation_normalizes_only_canonical_values(self):
        self.assertEqual(roster_designation({"roster_designation":" TAXI "}),"taxi")
        self.assertEqual(roster_designation({"roster_designation":"IR"}),"ir")
        self.assertIsNone(roster_designation({"roster_designation":"bench"}))
        self.assertIsNone(roster_designation({}))
    def test_no_direct_canonical_table_reads(self):
        source=(ROOT/"services/team_roster_state.py").read_text()
        for table in("contract_agreements","contract_seasons","contract_events","dead_cap_obligations"):
            self.assertNotIn(f'table("{table}")',source)
    def test_migration_security_shape(self):
        sql=(ROOT/"supabase/migrations/20261030_authenticated_canonical_team_state_read.sql").read_text().lower()
        self.assertIn("security definer",sql);self.assertIn("set search_path=pg_catalog,public",sql)
        self.assertIn("grant execute on function public.read_canonical_team_state_authenticated(jsonb) to authenticated",sql)
        self.assertNotIn("grant select on",sql);self.assertIn("team_state_cross_team_forbidden",sql)
    def test_both_pages_use_shared_cap_adjustment_display_rows(self):
        for page in ("pages/02_My_Team.py","pages/03_Teams.py"):
            source=(ROOT/page).read_text();self.assertIn("cap_adjustment_display_rows",source)
        teams=(ROOT/"pages/03_Teams.py").read_text()
        self.assertNotIn("f'<small>{season}</small>'",teams)
    def test_pages_use_canonical_designation_and_rookie_is_independent(self):
        mine=(ROOT/"pages/02_My_Team.py").read_text();teams=(ROOT/"pages/03_Teams.py").read_text()
        roster_block=mine[mine.index("# ---------- roster ----------"):mine.index("# ---------- right actions ----------")]
        self.assertIn('if bool(r.get("is_rookie"))',roster_block)
        self.assertIn('designation = roster_designation(r)',roster_block)
        self.assertNotIn('eq("taxi_adjustment")',roster_block)
        self.assertNotIn('eq("ir_adjustment")',roster_block)
        self.assertGreaterEqual(teams.count("roster_designation(r)"),2)

if __name__=="__main__":unittest.main()
