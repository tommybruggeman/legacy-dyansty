import unittest

from services.my_team_context import MyTeamContextError, resolve_my_team


class Query:
    def __init__(self,c,t): self.c,self.t,self.f=c,t,{}
    def select(self,*a): return self
    def eq(self,k,v): self.f[k]=v; return self
    def execute(self):
        class R: pass
        r=R(); r.data=[x for x in self.c.data[self.t] if all(x.get(k)==v for k,v in self.f.items())]; return r
class Client:
    def __init__(self): self.data={"league_memberships":[{"id":"m","league_id":"l1","user_id":"u1","role":"member","league_team_id":"t1"}],"league_teams":[{"id":"t1","league_id":"l1","team_name":"Correct Team","owner_name":"Owner","sleeper_roster_id":4,"sleeper_user_id":"su"},{"id":"t1","league_id":"l2","team_name":"Wrong Team","owner_name":"Owner"}]}
    def table(self,t): return Query(self,t)

class MyTeamContextTest(unittest.TestCase):
    def test_resolves_canonical_team_inside_selected_league(self):
        result=resolve_my_team(Client(),user_id="u1",league_id="l1")
        self.assertEqual(result["team_name"],"Correct Team")
        self.assertEqual(result["team_id"],"t1")
    def test_does_not_authorize_via_display_name_or_legacy_team_id(self):
        client=Client(); client.data["league_memberships"][0]["league_team_id"]=None
        with self.assertRaises(MyTeamContextError): resolve_my_team(client,user_id="u1",league_id="l1")

if __name__ == "__main__": unittest.main()
