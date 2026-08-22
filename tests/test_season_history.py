from __future__ import annotations

import unittest

from season_engine.history.models import SourceBundle
from season_engine.history.planner import build_capture_plan
from season_engine.history.repositories import HistoricalSeasonRepository
from season_engine.models import LeagueSeason


SEASON = LeagueSeason("season-2025", "league-1", 2025, "sleeper-2025", True)


def source(team_count=10):
    rosters, users = [], []
    for rid in range(1, team_count + 1):
        players = [f"p{rid}", f"b{rid}", f"t{rid}", f"i{rid}"]
        rosters.append({"roster_id": rid, "owner_id": f"u{rid}", "players": players,
            "starters": [f"p{rid}"], "taxi": [f"t{rid}"], "reserve": [f"i{rid}"],
            "settings": {"wins": 11-rid, "losses": rid, "ties": 0, "fpts": 1000+rid,
                         "fpts_decimal": rid, "fpts_against": 900+rid, "fpts_against_decimal": 0},
            "metadata": {"streak": "1W"}})
        users.append({"user_id": f"u{rid}", "metadata": {"team_name": f"Sleeper {rid}"}})
    entries=[]
    for rid in range(1, team_count + 1):
        if rid + (rid % 2) <= team_count:
            entries.append({"roster_id":rid,"matchup_id":((rid-1)//2)+1,"points":100+rid})
    winner=({"m":1,"r":1,"t1":1,"t2":2 if team_count > 1 else None,"w":1,"l":2 if team_count > 1 else None,"p":1},)
    return SourceBundle({"season":"2025","settings":{"playoff_week_start":15}}, tuple(users), tuple(rosters), {1:tuple(entries)}, winner, ())


def teams(team_count=10):
    return [{"id":f"team-{i}","league_id":"league-1","owner_name":f"Owner {i}",
             "team_name":f"Team {i}","sleeper_roster_id":i,"sleeper_user_id":f"u{i}"} for i in range(1,team_count + 1)]


class HistoryPlannerTest(unittest.TestCase):
    def test_exact_canonical_sets_scale(self):
        for team_count in (1, 10, 32, 100, 2000):
            with self.subTest(team_count=team_count):
                plan = build_capture_plan(season=SEASON, source=source(team_count), league_teams=teams(team_count))
                self.assertTrue(plan.safe_to_apply, plan.blocking_errors)
                self.assertEqual(plan.canonical_team_count, team_count)
                self.assertEqual(len(plan.team_mappings), team_count)
                self.assertEqual(len(plan.standings), team_count)
                self.assertTrue(all(len(value) == 64 for value in (
                    plan.canonical_team_set_fingerprint, plan.source_roster_set_fingerprint,
                    plan.mapping_set_fingerprint, plan.standings_set_fingerprint)))

    def test_capture_is_season_scoped_and_designations_are_preserved(self):
        plan=build_capture_plan(season=SEASON,source=source(),league_teams=teams())
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(len(plan.team_mappings),10)
        self.assertEqual(len(plan.matchups),5)
        self.assertEqual(len(plan.standings),10)
        self.assertEqual({r["roster_designation"] for r in plan.roster_assignments},{"active","bench","taxi","ir"})
        self.assertTrue(all(r["league_season_id"]=="season-2025" for r in plan.roster_assignments))

    def test_matchup_normalization_is_deterministic(self):
        first=build_capture_plan(season=SEASON,source=source(),league_teams=teams())
        second=build_capture_plan(season=SEASON,source=source(),league_teams=list(reversed(teams())))
        self.assertEqual(first.matchups,second.matchups)
        self.assertEqual(first.source_fingerprint,second.source_fingerprint)

    def test_missing_mapping_blocks_apply(self):
        plan=build_capture_plan(season=SEASON,source=source(),league_teams=teams()[:-1])
        self.assertFalse(plan.safe_to_apply)
        self.assertIn("team_mapping",{x["code"] for x in plan.blocking_errors})

    def test_duplicate_roster_mapping_is_rejected(self):
        duplicate=teams()+[{**teams()[0],"id":"other-team"}]
        plan=build_capture_plan(season=SEASON,source=source(),league_teams=duplicate)
        self.assertFalse(plan.safe_to_apply)

    def test_duplicate_canonical_team_id_is_rejected(self):
        duplicate = teams() + [{**teams()[0]}]
        plan = build_capture_plan(season=SEASON, source=source(), league_teams=duplicate)
        self.assertIn("duplicate_canonical_team", {x["code"] for x in plan.blocking_errors})

    def test_duplicate_source_roster_is_rejected(self):
        bundle = source(); bundle = SourceBundle(bundle.league, bundle.users, bundle.rosters + (bundle.rosters[0],),
            bundle.matchups_by_week, bundle.winners_bracket, bundle.losers_bracket)
        plan = build_capture_plan(season=SEASON, source=bundle, league_teams=teams())
        self.assertIn("duplicate_source_sleeper_roster", {x["code"] for x in plan.blocking_errors})

    def test_ambiguous_canonical_roster_mapping_is_rejected(self):
        contaminated = teams(); contaminated[1] = {**contaminated[1], "sleeper_roster_id": 1}
        plan = build_capture_plan(season=SEASON, source=source(), league_teams=contaminated)
        self.assertIn("duplicate_canonical_sleeper_roster", {x["code"] for x in plan.blocking_errors})

    def test_missing_team_and_standings_are_rejected(self):
        bundle = source(); bundle = SourceBundle(bundle.league, bundle.users, bundle.rosters[:-1],
            {}, bundle.winners_bracket, bundle.losers_bracket)
        plan = build_capture_plan(season=SEASON, source=bundle, league_teams=teams())
        codes = {x["code"] for x in plan.blocking_errors}
        self.assertIn("source_roster_set_missing", codes)
        self.assertIn("standings_set_mismatch", codes)

    def test_equal_count_substitution_is_rejected(self):
        bundle = source(); replacement = {**bundle.rosters[-1], "roster_id": 999, "owner_id": "foreign"}
        bundle = SourceBundle(bundle.league, bundle.users, bundle.rosters[:-1] + (replacement,),
            bundle.matchups_by_week, bundle.winners_bracket, bundle.losers_bracket)
        plan = build_capture_plan(season=SEASON, source=bundle, league_teams=teams())
        codes = {x["code"] for x in plan.blocking_errors}
        self.assertIn("source_roster_set_missing", codes)
        self.assertIn("source_roster_set_foreign", codes)

    def test_cross_league_team_is_rejected(self):
        contaminated=teams(); contaminated[0]={**contaminated[0],"league_id":"league-2"}
        plan=build_capture_plan(season=SEASON,source=source(),league_teams=contaminated)
        self.assertIn("cross_league_team",{x["code"] for x in plan.blocking_errors})

    def test_champion_is_derivable(self):
        plan=build_capture_plan(season=SEASON,source=source(),league_teams=teams())
        champions=[x for x in plan.brackets if x["placement"]==1 and x["bracket_type"]=="winner"]
        self.assertEqual(champions[0]["winner_league_team_id"],"team-1")

    def test_inactive_source_is_blocked(self):
        inactive=LeagueSeason("season-2026","league-1",2026,"sleeper-2026",False)
        bundle=source(); bundle=SourceBundle({**bundle.league,"season":"2026"},bundle.users,bundle.rosters,bundle.matchups_by_week,bundle.winners_bracket,bundle.losers_bracket)
        self.assertFalse(build_capture_plan(season=inactive,source=bundle,league_teams=teams()).safe_to_apply)


class Query:
    def __init__(self, client, table): self.client,self.table_name,self.filters,self.bounds,self.head=client,table,{},None,False
    def select(self,*a,**kw): self.head=bool(kw.get("head")); return self
    def eq(self,k,v): self.filters[k]=v; return self
    def order(self,*a): return self
    def range(self,start,end): self.bounds=(start,end); return self
    def execute(self):
        class R: pass
        all_rows=[x for x in self.client.data.get(self.table_name,[]) if all(x.get(k)==v for k,v in self.filters.items())]
        r=R(); r.count=len(all_rows)
        r.data=[] if self.head else all_rows[slice(*((self.bounds[0],self.bounds[1]+1) if self.bounds else (None,None)))]
        return r

class Client:
    def __init__(self): self.data={"league_seasons":[{"id":"s25","league_id":"l1","season":2025}],"season_standings":[{"id":"x","league_season_id":"s25"}]}
    def table(self,n): return Query(self,n)

class HistoricalRepositoryTest(unittest.TestCase):
    def test_explicit_season_is_required(self):
        with self.assertRaises(ValueError): HistoricalSeasonRepository(Client()).get_season_standings("l1",None)
    def test_reads_requested_season_without_active_fallback(self):
        self.assertEqual(HistoricalSeasonRepository(Client()).get_season_standings("l1",2025)[0]["id"],"x")
    def test_paginated_read_never_treats_first_page_as_complete(self):
        client=Client(); client.data["season_standings"]=[{"id":str(i),"league_season_id":"s25"} for i in range(1201)]
        self.assertEqual(len(HistoricalSeasonRepository(client).get_season_standings("l1",2025)),1201)
    def test_truncated_paginated_read_fails_closed(self):
        class TruncatedQuery(Query):
            def execute(self):
                response=super().execute()
                if self.table_name=="season_standings":
                    response.count=1201
                    if self.bounds and self.bounds[0]>=500: response.data=[]
                return response
        class TruncatedClient(Client):
            def table(self,n): return TruncatedQuery(self,n)
        client=TruncatedClient(); client.data["season_standings"]=[{"id":str(i),"league_season_id":"s25"} for i in range(500)]
        with self.assertRaises(RuntimeError): HistoricalSeasonRepository(client).get_season_standings("l1",2025)

if __name__ == "__main__": unittest.main()
