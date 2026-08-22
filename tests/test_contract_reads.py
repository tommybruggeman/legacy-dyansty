from copy import deepcopy
from types import SimpleNamespace
import unittest

from contract_engine.contract_read_service import ContractReadService,ContractReadValidationError
from contract_engine.internal_reads import load_internal_contract_rows
from contract_engine.operational_season import ContractOperationalSeasonError,resolve_contract_operational_season


class Query:
    def __init__(self,client,table):self.client=client;self.name=table;self.filters=[];self.allowed=None;self.order_key=None;self.bounds=None
    def select(self,*a,**k):return self
    def eq(self,key,value):self.filters.append((key,value));return self
    def in_(self,key,values):self.allowed=(key,{str(x) for x in values});return self
    def order(self,key,*a,**k):self.order_key=key;return self
    def range(self,start,end):self.bounds=(start,end);return self
    def execute(self):
        rows=[dict(x) for x in self.client.rows.get(self.name,[])]
        if self.order_key == "id":
            for index,row in enumerate(rows):row.setdefault("id",f"{self.name}:{index:08d}")
        for key,value in self.filters:rows=[x for x in rows if str(x.get(key))==str(value)]
        if self.allowed:rows=[x for x in rows if str(x.get(self.allowed[0])) in self.allowed[1]]
        if self.order_key:rows.sort(key=lambda row:str(row.get(self.order_key) or ""))
        count=len(rows)
        if self.bounds:rows=rows[self.bounds[0]:self.bounds[1]+1]
        return SimpleNamespace(data=rows,count=count)


class Client:
    def __init__(self):
        key="contract-transition:l1:2025:2026:v1"
        self.rows={
            "league_seasons":[{"id":"ls25","league_id":"l1","season":2025,"status":"active","is_active":True},{"id":"ls26","league_id":"l1","season":2026,"status":"scheduled","is_active":False},{"id":"ls27","league_id":"l1","season":2027,"status":"scheduled","is_active":False}],
            "contract_transition_executions":[{"id":"e1","league_id":"l1","source_league_season_id":"ls25","target_league_season_id":"ls26","source_season":2025,"target_season":2026,"transition_key":key,"status":"validated","result":{"persisted":{"agreements":3,"active_agreements":2,"expired_agreements":1,"satisfied_2025":3,"active_2026":2,"scheduled_2027":1}}}],
            "contract_agreements":[{"id":"a1","league_id":"l1","league_team_id":"t1","player_id":"p1","sleeper_player_id":"p1","status":"active","end_season":2026,"contract_type":"veteran","source_legacy_contract_id":"c1"},{"id":"a2","league_id":"l1","league_team_id":"t1","player_id":"p2","sleeper_player_id":"p2","status":"active","end_season":2027,"contract_type":"veteran","source_legacy_contract_id":"c2"},{"id":"a3","league_id":"l1","league_team_id":"t1","player_id":"p3","sleeper_player_id":"p3","status":"expired","end_season":2025,"contract_type":"unknown","source_legacy_contract_id":"c3"}],
            "contract_seasons":[{"id":"s1-25","contract_id":"a1","league_id":"l1","league_team_id":"t1","player_id":"p1","season":2025,"salary":"10.00","obligation_status":"satisfied"},{"id":"s1-26","contract_id":"a1","league_id":"l1","league_team_id":"t1","player_id":"p1","season":2026,"salary":"11.00","obligation_status":"active"},{"id":"s2-25","contract_id":"a2","league_id":"l1","league_team_id":"t1","player_id":"p2","season":2025,"salary":"20.00","obligation_status":"satisfied"},{"id":"s2-26","contract_id":"a2","league_id":"l1","league_team_id":"t1","player_id":"p2","season":2026,"salary":"21.00","obligation_status":"active"},{"id":"s2-27","contract_id":"a2","league_id":"l1","league_team_id":"t1","player_id":"p2","season":2027,"salary":"22.00","obligation_status":"scheduled"},{"id":"s3-25","contract_id":"a3","league_id":"l1","league_team_id":"t1","player_id":"p3","season":2025,"salary":"5.00","obligation_status":"satisfied"}],
            "contract_events":[],"league_teams":[{"id":"t1","league_id":"l1","team_name":"Team One","owner_name":"Owner"}],
            "player_universe":[{"sleeper_id":"p1","player_name":"One","pos":"QB"},{"sleeper_id":"p2","player_name":"Two","pos":"RB"},{"sleeper_id":"p3","player_name":"Three","pos":"WR"}],
            "contracts":[{"id":"c1","league_id":"l1","sleeper_player_id":"p1","salary":10.0,"contract_years_left":99,"is_rookie":False},{"id":"c2","league_id":"l1","sleeper_player_id":"p2","salary":20.0,"contract_years_left":99},{"id":"c3","league_id":"l1","sleeper_player_id":"p3","salary":5.0,"contract_years_left":99}]}
        self.calls=[]
    def table(self,name):self.calls.append(name);return Query(self,name)


class ContractReadTests(unittest.TestCase):
    def test_operational_season_is_transition_target(self):self.assertEqual(resolve_contract_operational_season(Client(),"l1"),2026)
    def test_no_execution_uses_active_league_season(self):
        c=Client();c.rows["contract_transition_executions"]=[];self.assertEqual(resolve_contract_operational_season(c,"l1"),2025)
    def test_missing_target_and_conflicting_execution_block(self):
        c=Client();c.rows["league_seasons"]=[c.rows["league_seasons"][0]]
        with self.assertRaises(ContractOperationalSeasonError):resolve_contract_operational_season(c,"l1")
    def test_cross_league_execution_reference_blocks(self):
        c=Client();c.rows["contract_transition_executions"][0]["target_league_season_id"]="foreign-season"
        with self.assertRaises(ContractOperationalSeasonError):resolve_contract_operational_season(c,"l1")
        c=Client();c.rows["contract_transition_executions"].append({**c.rows["contract_transition_executions"][0],"id":"e2"})
        with self.assertRaises(ContractOperationalSeasonError):resolve_contract_operational_season(c,"l1")
    def test_invalid_persisted_execution_blocks(self):
        c=Client();c.rows["contract_transition_executions"][0]["result"]["persisted"]["active_agreements"]=3
        with self.assertRaises(ContractOperationalSeasonError):resolve_contract_operational_season(c,"l1")

    def test_certified_reconciliation_supersedes_legacy_persisted_counts(self):
        c=Client()
        c.rows["contract_transition_executions"][0]["result"]["persisted"]["active_agreements"]=999
        c.rows["contract_transition_reconciliations"]=[{
            "id":"r1",
            "league_id":"l1",
            "source_season":2025,
            "target_season":2026,
            "legacy_transition_id":"e1",
            "reconciliation_status":"certified",
            "actual_counts":{
                "agreements_active":2,
                "agreements_expired":1,
                "target_active":2,
                "season_2027_scheduled":1,
            },
        }]
        self.assertEqual(resolve_contract_operational_season(c,"l1"),2025)

    def test_uncertified_reconciliation_does_not_supersede_legacy_counts(self):
        c=Client()
        c.rows["contract_transition_executions"][0]["result"]["persisted"]["active_agreements"]=999
        c.rows["contract_transition_reconciliations"]=[{
            "id":"r1",
            "league_id":"l1",
            "source_season":2025,
            "target_season":2026,
            "legacy_transition_id":"e1",
            "reconciliation_status":"applying",
            "actual_counts":{
                "agreements_active":2,
                "agreements_expired":1,
            },
        }]
        with self.assertRaises(ContractOperationalSeasonError):
            resolve_contract_operational_season(c,"l1")

    def test_certified_reconciliation_current_state_mismatch_blocks(self):
        c=Client()
        c.rows["contract_transition_reconciliations"]=[{
            "id":"r1",
            "league_id":"l1",
            "source_season":2025,
            "target_season":2026,
            "legacy_transition_id":"e1",
            "reconciliation_status":"certified",
            "actual_counts":{
                "agreements_active":999,
                "agreements_expired":1,
                "target_active":2,
                "season_2027_scheduled":1,
            },
        }]
        with self.assertRaises(ContractOperationalSeasonError):
            resolve_contract_operational_season(c,"l1")

    def test_reconciliation_for_other_transition_does_not_override(self):
        c=Client()
        c.rows["contract_transition_executions"][0]["result"]["persisted"]["active_agreements"]=999
        c.rows["contract_transition_reconciliations"]=[{
            "id":"r1",
            "league_id":"l1",
            "source_season":2025,
            "target_season":2026,
            "legacy_transition_id":"other",
            "reconciliation_status":"certified",
            "actual_counts":{
                "agreements_active":2,
                "agreements_expired":1,
            },
        }]
        with self.assertRaises(ContractOperationalSeasonError):
            resolve_contract_operational_season(c,"l1")
    def test_expired_active_source_obligation_allowed_only_with_certified_reconciliation(self):
        c=Client()
        c.rows["contract_transition_executions"]=[]
        c.rows["contract_seasons"]=[
            {**row, "obligation_status":"active"}
            if row["id"] in {"s1-25","s2-25","s3-25"} else row
            for row in c.rows["contract_seasons"]
        ]

        with self.assertRaises(ContractReadValidationError):
            ContractReadService(c).get_contracts("l1")

        c.rows["contract_transition_reconciliations"]=[{
            "id":"r1",
            "league_id":"l1",
            "source_season":2025,
            "target_season":2026,
            "legacy_transition_id":"e1",
            "reconciliation_status":"certified",
            "actual_counts":{
                "agreements_active":2,
                "agreements_expired":1,
            },
        }]

        records,_=ContractReadService(c).get_contracts("l1")
        expired=[x for x in records if x.agreement_id=="a3"]

        self.assertEqual(len(expired),1)
        self.assertEqual(expired[0].agreement_status,"expired")
        self.assertEqual(expired[0].operational_season,2025)
        self.assertEqual(expired[0].operational_obligation_status,"active")
        self.assertEqual(expired[0].remaining_contract_seasons,0)
        self.assertIsNone(expired[0].operational_salary)

    def test_uncertified_reconciliation_does_not_allow_expired_active_source_obligation(self):
        c=Client()
        c.rows["contract_transition_executions"]=[]
        c.rows["contract_seasons"]=[
            {**row, "obligation_status":"active"}
            if row["id"] in {"s1-25","s2-25","s3-25"} else row
            for row in c.rows["contract_seasons"]
        ]
        c.rows["contract_transition_reconciliations"]=[{
            "id":"r1",
            "league_id":"l1",
            "source_season":2025,
            "target_season":2026,
            "legacy_transition_id":"e1",
            "reconciliation_status":"applying",
            "actual_counts":{
                "agreements_active":2,
                "agreements_expired":1,
            },
        }]

        with self.assertRaises(ContractReadValidationError):
            ContractReadService(c).get_contracts("l1")

    def test_active_expired_salary_history_future_and_years(self):
        service=ContractReadService(Client());active=service.get_active_contracts("l1");expired=service.get_expired_contracts("l1")
        self.assertEqual((len(active),len(expired)),(2,1));self.assertEqual([x.remaining_contract_seasons for x in active],[1,2]);self.assertEqual(expired[0].remaining_contract_seasons,0)
        self.assertEqual(str(active[0].operational_salary),"11.00");self.assertEqual(active[1].future_contract_seasons[0].season,2027)
        self.assertEqual(service.get_contract_history("l1","a3")[0].temporal_role,"historical")
    def test_team_totals_decimal_and_cap_separate(self):
        row=ContractReadService(Client()).get_team_summary("l1")[0]
        self.assertEqual(str(row["active_operational_salary"]),"32.00");self.assertEqual(str(row["future_2027_salary"]),"22.00")
        self.assertEqual(str(row["expired_prior_season_salary"]),"5.00");self.assertFalse(row["cap_adjustments_included"])
    def test_legacy_adapter_ignores_legacy_salary_years_and_status(self):
        rows=ContractReadService(Client()).project_legacy_contract_shape("l1");by={x["sleeper_player_id"]:x for x in rows}
        self.assertEqual(by["p1"]["salary"],11.0);self.assertEqual(by["p1"]["contract_years_left"],1)
        self.assertEqual(by["p3"]["status"],"expired");self.assertEqual(by["p3"]["contract_years_left"],0);self.assertIsNone(by["p3"]["salary"])
    def test_read_modes_and_diagnostics(self):
        legacy=ContractReadService(Client(),mode="legacy").read_compatible("l1");self.assertEqual(legacy[0]["contract_years_left"],99)
        diagnostics=[];compare=ContractReadService(Client(),mode="compare",diagnostic_sink=diagnostics.append).read_compatible("l1","test")
        self.assertEqual(compare[0]["contract_years_left"],99);self.assertTrue(diagnostics[0]["salary_differences"]);self.assertIn("comparison_timestamp",diagnostics[0])
        self.assertIn("team_differences",diagnostics[0])
        normalized=ContractReadService(Client(),mode="normalized").read_compatible("l1");self.assertEqual(normalized[0]["contract_years_left"],1)
    def test_gap_duplicate_and_missing_identity_block(self):
        c=Client();c.rows["contract_seasons"]=[x for x in c.rows["contract_seasons"] if x["id"]!="s2-26"]
        with self.assertRaises((ContractReadValidationError,ContractOperationalSeasonError)):ContractReadService(c).get_contracts("l1")
        c=Client();c.rows["player_universe"]=[x for x in c.rows["player_universe"] if x["sleeper_id"]!="p1"]
        with self.assertRaises(ContractReadValidationError):ContractReadService(c).get_contracts("l1")
    def test_reads_perform_no_writes(self):
        c=Client();before=deepcopy(c.rows);ContractReadService(c).get_team_summary("l1");self.assertEqual(c.rows,before)
    def test_internal_reader_returns_normalized_active_contracts_only(self):
        rows=load_internal_contract_rows(Client(),"l1")
        self.assertEqual({row["sleeper_player_id"] for row in rows},{"p1","p2"})
        self.assertEqual({row["status"] for row in rows},{"active"})
        self.assertEqual({row["contract_years_left"] for row in rows},{1,2})
    def test_internal_compare_is_read_only(self):
        c=Client();before=deepcopy(c.rows);rows=load_internal_contract_rows(c,"l1",compare=True)
        self.assertEqual(len(rows),2);self.assertEqual(c.rows,before)


if __name__=="__main__":unittest.main()
