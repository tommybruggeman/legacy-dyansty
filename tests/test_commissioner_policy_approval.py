import unittest
from copy import deepcopy

from season_engine.commissioner_policy_approval import CommissionerPolicyApprovalService,PolicyApprovalError
from season_engine.commissioner_policy_draft import CommissionerPolicyDraftService,RELEASE_TO_HOLD,SEVEN_DAY_NOTICE_RULE


class Query:
    def __init__(self,c,t):self.c=c;self.t=t;self.filters=[];self.value=None
    def select(self,*a):return self
    def eq(self,k,v):self.filters.append((k,v));return self
    def insert(self,v):self.value=v;return self
    def execute(self):
        if self.value is not None:
            row={"id":"policy-1",**deepcopy(self.value)};self.c.rows[self.t].append(row);return type("R",(),{"data":[deepcopy(row)]})()
        rows=[x for x in self.c.rows[self.t] if all(x.get(k)==v for k,v in self.filters)];return type("R",(),{"data":deepcopy(rows)})()
class Client:
    def __init__(self):self.rows={"league_rollover_policies":[],"league_memberships":[{"user_id":"commish-1","role":"commissioner","league_id":"l1"}]}
    def table(self,t):return Query(self,t)

class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.client=Client();self.draft=CommissionerPolicyDraftService().prepare("l1",deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD);self.service=CommissionerPolicyApprovalService(self.client)
    def test_inserts_exactly_one_approved_inactive_row(self):
        result=self.service.approve(self.draft,self.draft.fingerprint,approved_at="2026-07-29T20:00:00+00:00")
        self.assertTrue(result.inserted);self.assertEqual(len(self.client.rows["league_rollover_policies"]),1);self.assertEqual(result.row["status"],"approved");self.assertIsNone(result.row["effective_at"]);self.assertIsNone(result.row["extension_deadline"]);self.assertEqual(result.row["metadata"]["policy_payload"],self.draft.payload)
    def test_retry_returns_existing_without_duplicate(self):
        self.service.approve(self.draft,self.draft.fingerprint);result=self.service.approve(self.draft,self.draft.fingerprint)
        self.assertTrue(result.idempotent);self.assertEqual(len(self.client.rows["league_rollover_policies"]),1)
    def test_wrong_fingerprint_blocks_before_write(self):
        with self.assertRaises(PolicyApprovalError):self.service.approve(self.draft,"wrong")
        self.assertEqual(self.client.rows["league_rollover_policies"],[])
    def test_conflict_blocks_without_overwrite(self):
        self.client.rows["league_rollover_policies"].append({"league_id":"l1","source_season":2025,"target_season":2026,"version":1,"status":"approved","fingerprint":"other"})
        before=deepcopy(self.client.rows)
        with self.assertRaises(PolicyApprovalError):self.service.approve(self.draft,self.draft.fingerprint)
        self.assertEqual(self.client.rows,before)
    def test_requires_unique_commissioner(self):
        self.client.rows["league_memberships"]=[]
        with self.assertRaises(PolicyApprovalError):self.service.approve(self.draft,self.draft.fingerprint)

if __name__=="__main__":unittest.main()
