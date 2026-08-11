"""Deterministic domain-only fixture for a first hosted 2025 -> 2026 rollover.

This module intentionally knows nothing about lifecycle hashes, plans, approvals,
executions, or publication evidence.  Its SQL is instrumented and restricted to
the explicit allowlist below.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import UUID, uuid5


FIXTURE_VERSION = "no-seeding-domain-v1"
NAMESPACE = UUID("e9a2f08f-e4f4-44d0-bf06-d6892917ff5c")

BOOTSTRAP_TABLE_ALLOWLIST = frozenset({
    "auth.users", "public.leagues", "public.league_memberships",
    "public.league_seasons", "public.league_teams", "public.teams",
    "public.season_team_mappings", "public.player_universe",
    "public.contract_agreements", "public.contract_seasons",
    "public.contract_events", "public.league_rules", "public.cap_adjustments",
    "public.dead_cap_ledger", "public.draft_inventory_classes",
    "public.draft_pick_assets", "public.league_rookie_class_authorities",
})

LIFECYCLE_TABLE_DENYLIST = frozenset({
    "public.league_rollover_policies", "public.historical_capture_executions",
    "public.rollover_executions", "public.rollover_owner_decisions",
    "public.rollover_owner_decision_revisions", "public.rollover_commissioner_reviews",
    "public.rollover_commissioner_review_events", "public.rollover_authority_preparations",
    "public.rollover_dry_run_simulations", "public.rollover_execution_plans",
    "public.rollover_execution_plan_approvals", "public.rollover_execution_locks",
    "public.rollover_execution_runs", "public.rollover_execution_run_state_events",
    "public.rollover_execution_input_snapshots", "public.rollover_execution_input_snapshot_components",
    "public.rollover_execution_operation_results", "public.rollover_post_execution_validation_reports",
    "public.rollover_executed_unpublished_finalizations",
    "public.rollover_target_season_authority_publications",
    "public.rollover_target_cap_authority_publications",
    "public.rollover_target_market_visibility_publications",
    "public.rollover_cutover_release_publications", "public.publication_context_generations",
    "public.season_cache_invalidation_manifests", "public.prepared_team_cap_sets",
    "public.prepared_free_agent_eligibility_sets", "public.prepared_expiring_contract_sets",
    "public.prepared_target_standings_sets", "public.prepared_target_matchup_sets",
    "public.prepared_target_playoff_structures", "public.prepared_rookie_eligibility_sets",
})

_WRITE = re.compile(r"\b(?:insert\s+into|update|delete\s+from)\s+((?:auth|public)\.[a-z0-9_]+)", re.I)


def _id(label: str) -> str:
    return str(uuid5(NAMESPACE, label))


def _quote(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class DomainFixtureIdentity:
    version: str
    namespace: str
    league_id: str
    commissioner_id: str
    owner_id: str
    owner_ids: tuple[str, ...]
    outsider_id: str
    source_season_id: str
    target_season_id: str
    sleeper_league_id: str
    team_ids: tuple[str, ...]
    owner_player_ids: tuple[str, ...]
    commissioner_player_ids: tuple[str, ...]


class SeasonRolloverDomainFactory:
    def __init__(self, label: str = "unified-hosted"):
        self.label = label
        self.namespace = f"season-rollover-{FIXTURE_VERSION}-{label}"
        self.league_id = _id(self.namespace + ":league")
        self.commissioner_id = _id(self.namespace + ":commissioner")

        # Preserve the original Team 1 owner identity for compatibility,
        # then create distinct deterministic owners for Teams 2–10.
        self.owner_id = _id(self.namespace + ":owner")
        self.owner_ids = (
            self.owner_id,
            *(
                _id(self.namespace + f":owner:{n}")
                for n in range(2, 11)
            ),
        )

        self.outsider_id = _id(self.namespace + ":outsider")
        self.source_season_id = _id(self.namespace + ":season:2025")
        self.target_season_id = _id(self.namespace + ":season:2026")
        self.team_ids = tuple(_id(self.namespace + f":team:{i}") for i in range(1, 11))
        self.owner_player_ids = tuple(f"{self.namespace}-owner-{i:03d}" for i in range(1, 109))
        self.commissioner_player_ids = tuple(f"{self.namespace}-review-{i:02d}" for i in range(1, 14))

    @property
    def identity(self) -> DomainFixtureIdentity:
        return DomainFixtureIdentity(
            FIXTURE_VERSION,
            self.namespace,
            self.league_id,
            self.commissioner_id,
            self.owner_id,
            self.owner_ids,
            self.outsider_id,
            self.source_season_id,
            self.target_season_id,
            self.namespace + "-sleeper-2025",
            self.team_ids,
            self.owner_player_ids,
            self.commissioner_player_ids,
        )

    @staticmethod
    def audit_bootstrap_sql(sql: str) -> tuple[str, ...]:
        writes = tuple(dict.fromkeys(x.lower() for x in _WRITE.findall(sql)))
        forbidden = sorted(set(writes) - BOOTSTRAP_TABLE_ALLOWLIST)
        denied = sorted(set(writes).intersection(LIFECYCLE_TABLE_DENYLIST))
        if forbidden or denied:
            raise AssertionError(f"domain bootstrap escaped allowlist: forbidden={forbidden}, denied={denied}")
        return writes

    def bootstrap_sql(self) -> str:
        i = self.identity
        statements = ["begin"]

        fixture_users = (
            i.commissioner_id,
            *i.owner_ids,
            i.outsider_id,
        )

        for actor in fixture_users:
            statements.append(
                "insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data,created_at,updated_at) "
                f"values({_quote(actor)},'authenticated','authenticated',{_quote(actor+'@no-seeding.invalid')},"
                "'{\"provider\":\"email\",\"providers\":[\"email\"]}','{}',now(),now())")
        statements.append("insert into public.leagues(id,name,created_by,sleeper_league_id) values(" +
            ",".join(map(_quote, (i.league_id, self.namespace, i.commissioner_id, i.sleeper_league_id))) + ")")
        statements.append(f"insert into public.league_seasons(id,league_id,season,sleeper_league_id,is_active,status,previous_league_season_id) values "
            f"({_quote(i.source_season_id)},{_quote(i.league_id)},2025,{_quote(i.sleeper_league_id)},true,'active',null),"
            f"({_quote(i.target_season_id)},{_quote(i.league_id)},2026,{_quote(self.namespace+'-sleeper-2026')},false,'scheduled',{_quote(i.source_season_id)})")
        for n, team_id in enumerate(i.team_ids, 1):
            uid = i.owner_ids[n - 1]
            statements.append(f"insert into public.league_teams(id,league_id,owner_name,team_name,user_id,sleeper_roster_id,sleeper_user_id) values"
                f"({_quote(team_id)},{_quote(i.league_id)},{_quote('Owner '+str(n))},{_quote('Team '+str(n))},{_quote(uid)},{n},{_quote('sleeper-owner-'+str(n))})")
            statements.append(f"insert into public.teams(id,league_id,team_name,sleeper_roster_id,sleeper_owner_id,owner_id) values"
                f"({_quote(team_id)},{_quote(i.league_id)},{_quote('Team '+str(n))},{n},{_quote('sleeper-owner-'+str(n))},null)")
            # Execution requires a complete canonical roster mapping
            # for both the closing and target league seasons.
            for league_season_id in (
                i.source_season_id,
                i.target_season_id,
            ):
                statements.append(
                    f"insert into public.season_team_mappings("
                    f"league_season_id,"
                    f"league_team_id,"
                    f"sleeper_roster_id,"
                    f"sleeper_owner_id,"
                    f"sleeper_user_id,"
                    f"team_name_snapshot,"
                    f"owner_name_snapshot,"
                    f"mapping_source,"
                    f"mapping_confidence"
                    f") values("
                    f"{_quote(league_season_id)},"
                    f"{_quote(team_id)},"
                    f"{n},"
                    f"{_quote('sleeper-owner-'+str(n))},"
                    f"{_quote('sleeper-owner-'+str(n))},"
                    f"{_quote('Team '+str(n))},"
                    f"{_quote('Owner '+str(n))},"
                    f"'league_teams.sleeper_roster_id',"
                    f"'exact'"
                    f")"
                )
        statements.append(
            f"insert into public.league_memberships(id,league_id,user_id,role,league_team_id) "
            f"values({_quote(_id(self.namespace+':membership:commissioner'))},"
            f"{_quote(i.league_id)},{_quote(i.commissioner_id)},'commissioner',null)"
        )

        for n, (owner_id, team_id) in enumerate(
            zip(i.owner_ids, i.team_ids),
            1,
        ):
            statements.append(
                f"insert into public.league_memberships("
                f"id,league_id,user_id,role,league_team_id"
                f") values("
                f"{_quote(_id(self.namespace+':membership:owner:'+str(n)))},"
                f"{_quote(i.league_id)},"
                f"{_quote(owner_id)},"
                f"'owner',"
                f"{_quote(team_id)}"
                f")"
            )

        statements.append(
            f"insert into public.league_rules("
            f"id,league_id,salary_cap,league_min_salary,"
            f"default_fa_years,drop_dead_cap_multiplier,"
            f"roster_limit,taxi_limit,ir_limit"
            f") values("
            f"{_quote(_id(self.namespace+':rules'))},"
            f"{_quote(i.league_id)},5000,1,1,0.5,30,5,5)"
        )
        all_players = list(i.owner_player_ids) + list(i.commissioner_player_ids)
        player_values, agreement_values, season_values = [], [], []
        for n, player_id in enumerate(all_players, 1):
            team_id = i.team_ids[(n - 1) % len(i.team_ids)]
            agreement_id = _id(self.namespace + ":agreement:" + player_id)
            is_active_review = player_id in i.commissioner_player_ids[-2:]
            status = "active" if is_active_review else "expired"
            end = 2026 if is_active_review else 2025
            rookie_year = 2026 if n % 17 == 0 else None
            player_values.append(
                f"({_quote(player_id)},{_quote(player_id)},"
                f"{_quote('Synthetic Player '+str(n))},'WR',true,"
                f"{_quote(rookie_year)},{_quote(rookie_year)},"
                f"{1 if rookie_year else 'null'},"
                f"{'true' if rookie_year else 'false'})"
            )
            agreement_values.append(f"({_quote(agreement_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},{_quote(player_id)},'veteran','imported_initial_contract',2025,2025,{end},{_quote(status)})")
            obligation = "active" if is_active_review else "satisfied"
            season_values.append(f"({_quote(_id(self.namespace+':contract-season:2025:'+player_id))},{_quote(agreement_id)},{_quote(i.source_season_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},2025,{5+n%7},{5+n%7},null,{_quote(obligation)},'synthetic_domain',false,null)")
            if is_active_review:
                season_values.append(f"({_quote(_id(self.namespace+':contract-season:2026:'+player_id))},{_quote(agreement_id)},{_quote(i.target_season_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},2026,10,10,null,'scheduled','synthetic_domain',false,null)")
            elif player_id in i.owner_player_ids:
                season_values.append(f"({_quote(_id(self.namespace+':contract-season:2026:'+player_id))},{_quote(agreement_id)},{_quote(i.target_season_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},2026,{5+n%7},{5+n%7},{5+n%7},'scheduled','synthetic_domain',true,'owner_option')")
        statements.append("insert into public.player_universe(sleeper_id,canonical_player_id,player_name,pos,active,rookie_class_year,draft_year,draft_round,is_rookie_contract) values " + ",".join(player_values))
        statements.append("insert into public.contract_agreements(id,league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,signed_season,start_season,end_season,status) values " + ",".join(agreement_values))
        # Every tuple already uses the final contract_seasons column order.
        normalized_season_values = season_values
        statements.append("insert into public.contract_seasons(id,contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,cap_hit,guaranteed_salary,obligation_status,source,is_option_year,option_type) values " + ",".join(normalized_season_values))
        for n, team_id in enumerate(i.team_ids, 1):
            statements.append(f"insert into public.cap_adjustments(id,league_id,owner_name,season,adjustment_type,amount,note) values({_quote(_id(self.namespace+':cap:'+str(n)))},{_quote(i.league_id)},{_quote(team_id)},2026,'manual_adjustment',0,'domain prerequisite')")
            statements.append(f"insert into public.dead_cap_ledger(id,league_id,league_season_id,team_id,original_salary,dead_cap_amount,remaining_years,active) values({_quote(_id(self.namespace+':dead:'+str(n)))},{_quote(i.league_id)},{_quote(i.target_season_id)},{_quote(team_id)},0,0,0,true)")
        asset_values = []
        for year in (2026, 2027, 2028):
            class_id = _id(self.namespace + f":draft-class:{year}")
            statements.append(f"insert into public.draft_inventory_classes(id,league_id,draft_year,horizon_status,publication_status,provenance,deterministic_class_hash) values({_quote(class_id)},{_quote(i.league_id)},{year},'visible_prepared','unpublished','{{\"source\":\"synthetic_domain\"}}',encode(digest({_quote(self.namespace+':draft:'+str(year))},'sha256'),'hex'))")
            for rnd in range(1, 4):
                for team_id in i.team_ids:
                    asset_values.append(f"(public.phase3b9a_stable_pick_id({_quote(i.league_id)},{year},{rnd},{_quote(team_id)}),{_quote(class_id)},{_quote(i.league_id)},{year},{rnd},{_quote(team_id)},{_quote(team_id)},'tradable','unpublished','{{\"source\":\"synthetic_domain\"}}',encode(digest({_quote(str(year)+':'+str(rnd)+':'+team_id)},'sha256'),'hex'))")
        statements.append("insert into public.draft_pick_assets(stable_pick_id,class_id,league_id,draft_year,round_number,original_league_team_id,current_owner_league_team_id,asset_status,publication_status,generation_provenance,deterministic_asset_hash) values " + ",".join(asset_values))
        statements.append(f"insert into public.league_rookie_class_authorities(id,league_id,target_season_id,rookie_class_year,authority_version,authority_status,published_at,source_snapshot_hash,deterministic_authority_fingerprint) values({_quote(_id(self.namespace+':rookie-authority:2025'))},{_quote(i.league_id)},{_quote(i.source_season_id)},2025,1,'published',now(),encode(digest({_quote(self.namespace+':rookie-source')},'sha256'),'hex'),encode(digest({_quote(self.namespace+':rookie-authority')},'sha256'),'hex'))")
        statements.append("commit")
        sql = ";\n".join(statements) + ";\n"
        self.audit_bootstrap_sql(sql)
        return sql

    def history_source(self) -> dict[str, Any]:
        i = self.identity
        assignments = {n: [] for n in range(1, 11)}
        for n, player_id in enumerate(i.owner_player_ids, 1):
            assignments[(n - 1) % 10 + 1].append(player_id)
        rosters = []
        for n in range(1, 11):
            players = assignments[n]
            rosters.append({"roster_id": n, "owner_id": f"sleeper-owner-{n}", "players": players,
                "starters": players[:8], "taxi": players[8:9], "reserve": players[9:10],
                "settings": {"wins": 11 - n, "losses": n - 1, "ties": 0,
                    "fpts": 2000 - n * 10, "fpts_against": 1500 + n * 10}})
        matchups = tuple({"roster_id": n, "matchup_id": (n + 1) // 2, "points": 100 + n}
                         for n in range(1, 11))
        return {"league": {"league_id": i.sleeper_league_id, "season": "2025", "settings": {"playoff_week_start": 15}},
            "users": tuple({"user_id": f"sleeper-owner-{n}", "metadata": {"team_name": f"Team {n}"}} for n in range(1, 11)),
            "rosters": tuple(rosters), "matchups_by_week": {1: matchups},
            "winners_bracket": ({"r": 1, "m": 1, "t1": 1, "t2": 2, "w": 1, "l": 2, "p": 1},),
            "losers_bracket": ()}

    def cleanup_sql(self) -> str:
        """Administrative disposable-test cleanup, never a product boundary."""
        i = self.identity
        league = _quote(i.league_id)
        source = _quote(i.source_season_id)
        target = _quote(i.target_season_id)
        players = ",".join(
            _quote(x)
            for x in (
                *i.owner_player_ids,
                *i.commissioner_player_ids,
            )
        )
        actors = ",".join(
            _quote(x)
            for x in (
                i.commissioner_id,
                *i.owner_ids,
                i.outsider_id,
            )
        )
        return f"""begin;
set local session_replication_role=replica;
select public.reset_rollover_disposable_clock_private();
delete from public.rollover_operation_requests where league_id={league};
delete from public.rollover_executions where league_id={league};
delete from public.league_rollover_policies where league_id={league};
delete from public.historical_capture_executions where league_season_id={source};
delete from public.season_playoff_brackets where league_season_id={source};
delete from public.season_matchups where league_season_id={source};
delete from public.season_standings where league_season_id={source};
delete from public.season_roster_assignments where league_season_id={source};
delete from public.season_team_mappings
where league_season_id in ({source},{target});
delete from public.contract_events where league_id={league};
delete from public.contract_seasons where league_id={league};
delete from public.contract_agreements where league_id={league};
delete from public.league_rookie_class_authorities where league_id={league};
delete from public.draft_pick_assets where league_id={league};
delete from public.draft_inventory_classes where league_id={league};
delete from public.dead_cap_ledger where league_id={league};
delete from public.cap_adjustments where league_id={league};
delete from public.league_rules where league_id={league};
delete from public.league_memberships where league_id={league};
delete from public.teams where league_id={league};
delete from public.league_teams where league_id={league};
delete from public.league_seasons where league_id={league};
delete from public.player_universe where sleeper_id in ({players});
delete from public.leagues where id={league};
delete from auth.users where id in ({actors});
commit;"""
