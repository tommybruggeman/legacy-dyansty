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


FIXTURE_VERSION = "canonical-contract-authority-v2"
NAMESPACE = UUID("e9a2f08f-e4f4-44d0-bf06-d6892917ff5c")
CANONICAL_MEMBERSHIP_ROLES = frozenset({"commissioner", "member"})
LEGACY_MEMBERSHIP_ROLES = frozenset({"owner", "co_owner", "co-owner", "host"})

BOOTSTRAP_TABLE_ALLOWLIST = frozenset({
    "auth.users", "public.leagues", "public.league_memberships",
    "public.league_seasons", "public.league_teams", "public.teams",
    "public.season_team_mappings", "public.player_universe",
    "public.contract_agreements", "public.contract_seasons",
    "public.contract_events", "public.league_rules", "public.cap_adjustments",
    "public.dead_cap_ledger", "public.draft_inventory_classes",
    "public.draft_pick_assets", "public.league_rookie_class_authorities",
    "public.rookie_draft_board_assignments", "public.rookie_taxi_assignments",
    "public.contract_rollover_classifications",
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
    roster_player_ids: tuple[str, ...]
    commissioner_player_ids: tuple[str, ...]
    lifecycle_player_ids: dict[str, tuple[str, ...]]
    foreign_league_id: str
    foreign_team_id: str


class SeasonRolloverDomainFactory:
    def __init__(self, label: str = "unified-hosted", *, commissioner_id: str | None = None,
                 owner_id: str | None = None, outsider_id: str | None = None,
                 owner_ids: tuple[str, ...] | None = None,
                 profile: str = "canonical_minimal",
                 externally_provisioned_actor_ids: tuple[str, ...] = ()):
        if profile not in {"canonical_minimal", "abs_shaped", "abs_zero_owner"}:
            raise ValueError("profile must be canonical_minimal, abs_shaped, or abs_zero_owner")
        self.profile = profile
        self.label = label
        self.namespace = f"season-rollover-{FIXTURE_VERSION}-{label}"
        self.league_id = _id(self.namespace + ":league")
        self.commissioner_id = commissioner_id or _id(self.namespace + ":commissioner")

        # Preserve the original Team 1 owner identity for compatibility,
        # then create distinct deterministic owners for Teams 2–10.
        supplied_owner_ids = tuple(owner_ids or ())
        if supplied_owner_ids and len(supplied_owner_ids) != 10:
            raise ValueError("owner_ids must contain exactly ten canonical owners")
        self.owner_id = (supplied_owner_ids[0] if supplied_owner_ids else owner_id) or _id(self.namespace + ":owner")
        self.owner_ids = supplied_owner_ids or (
            self.owner_id,
            *(_id(self.namespace + f":owner:{n}") for n in range(2, 11)),
        )

        self.outsider_id = outsider_id or _id(self.namespace + ":outsider")
        self.externally_provisioned_actor_ids = frozenset(externally_provisioned_actor_ids)
        self.source_season_id = _id(self.namespace + ":season:2025")
        self.target_season_id = _id(self.namespace + ":season:2026")
        self.team_ids = tuple(_id(self.namespace + f":team:{i}") for i in range(1, 11))
        self.commissioner_player_ids = tuple(f"{self.namespace}-review-{i:02d}" for i in range(1, 14))
        counts = ({
            "ordinary_continuing": 74,
            "ordinary_expiration": 116,
            "rookie_initial_continuing": 12,
            "rookie_initial_taxi_paused": 9,
            "rookie_option_eligible": 0,
        } if profile == "abs_zero_owner" else {
            "ordinary_continuing": 74,
            "ordinary_expiration": 113,
            "rookie_initial_continuing": 12,
            "rookie_initial_taxi_paused": 9,
            "rookie_option_eligible": 3,
        } if profile == "abs_shaped" else {
            "ordinary_continuing": 5,
            "ordinary_expiration": 14,
            "rookie_initial_continuing": 2,
            "rookie_initial_taxi_paused": 1,
            "rookie_option_eligible": 3,
        })
        lifecycle: dict[str, tuple[str, ...]] = {}
        for classification, count in counts.items():
            lifecycle[classification] = tuple(
                f"{self.namespace}-{classification.replace('_', '-')}-{n:03d}"
                for n in range(1, count + 1)
            )
        # Commissioner cases are part of the ordinary populations, not extra
        # agreements. Eleven are expired/unrostered and two are active/off-roster.
        lifecycle["ordinary_expiration"] = (
            *lifecycle["ordinary_expiration"][:-11],
            *self.commissioner_player_ids[:-2],
        )
        lifecycle["ordinary_continuing"] = (
            *lifecycle["ordinary_continuing"][:-2],
            *self.commissioner_player_ids[-2:],
        )
        self.lifecycle_player_ids = lifecycle
        self.owner_player_ids = lifecycle["rookie_option_eligible"]
        self.roster_player_ids = tuple(
            player_id for rows in lifecycle.values() for player_id in rows
            if player_id not in self.commissioner_player_ids
        )
        self.foreign_league_id = _id(self.namespace + ":foreign-league")
        self.foreign_team_id = _id(self.namespace + ":foreign-team")

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
            self.roster_player_ids,
            self.commissioner_player_ids,
            self.lifecycle_player_ids,
            self.foreign_league_id,
            self.foreign_team_id,
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
            if actor in self.externally_provisioned_actor_ids:
                continue
            statements.append(
                "insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data,created_at,updated_at) "
                f"values({_quote(actor)},'authenticated','authenticated',{_quote(actor+'@no-seeding.invalid')},"
                "'{\"provider\":\"email\",\"providers\":[\"email\"]}','{}',now(),now())")
        statements.append("insert into public.leagues(id,name,created_by,sleeper_league_id) values(" +
            ",".join(map(_quote, (i.league_id, self.namespace, i.commissioner_id, i.sleeper_league_id))) + ")")
        statements.append("insert into public.leagues(id,name,created_by,sleeper_league_id) values(" +
            ",".join(map(_quote, (i.foreign_league_id, self.namespace+"-foreign", i.outsider_id,
                                  self.namespace+"-foreign-sleeper"))) + ")")
        statements.append(f"insert into public.league_seasons(id,league_id,season,sleeper_league_id,is_active,status,previous_league_season_id) values "
            f"({_quote(i.source_season_id)},{_quote(i.league_id)},2025,{_quote(i.sleeper_league_id)},true,'active',null),"
            f"({_quote(i.target_season_id)},{_quote(i.league_id)},2026,{_quote(self.namespace+'-sleeper-2026')},false,'scheduled',{_quote(i.source_season_id)})"
            + (f",({_quote(_id(self.namespace+':season:2027'))},{_quote(i.league_id)},2027,{_quote(self.namespace+'-sleeper-2027')},false,'scheduled',{_quote(i.target_season_id)})" if self.profile == "abs_zero_owner" else ""))
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
        statements.append(f"insert into public.league_teams(id,league_id,owner_name,team_name,user_id,sleeper_roster_id,sleeper_user_id) values"
            f"({_quote(i.foreign_team_id)},{_quote(i.foreign_league_id)},'Foreign Owner','Foreign Team',"
            f"{_quote(i.outsider_id)},1,{_quote(self.namespace+'-foreign-owner')})")
        statements.append(f"insert into public.teams(id,league_id,team_name,sleeper_roster_id,sleeper_owner_id,owner_id) values"
            f"({_quote(i.foreign_team_id)},{_quote(i.foreign_league_id)},'Foreign Team',1,"
            f"{_quote(self.namespace+'-foreign-owner')},null)")
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
                f"'member',"
                f"{_quote(team_id)}"
                f")"
            )
        statements.append(f"insert into public.league_memberships(id,league_id,user_id,role,league_team_id) values("
            f"{_quote(_id(self.namespace+':membership:foreign-owner'))},{_quote(i.foreign_league_id)},"
            f"{_quote(i.outsider_id)},'member',{_quote(i.foreign_team_id)})")

        statements.append(
            f"insert into public.league_rules("
            f"id,league_id,salary_cap,league_min_salary,"
            f"default_fa_years,drop_dead_cap_multiplier,"
            f"roster_limit,taxi_limit,ir_limit"
            f") values("
            f"{_quote(_id(self.namespace+':rules'))},"
            f"{_quote(i.league_id)},5000,1,1,0.5,30,5,5)"
        )
        all_players = [p for rows in i.lifecycle_player_ids.values() for p in rows]
        classification_by_player = {p: c for c, rows in i.lifecycle_player_ids.items() for p in rows}
        player_values, agreement_values, season_values = [], [], []
        board_values, taxi_values, classification_values = [], [], []
        rookie_number = 0
        for n, player_id in enumerate(all_players, 1):
            team_id = i.team_ids[(n - 1) % len(i.team_ids)]
            agreement_id = _id(self.namespace + ":agreement:" + player_id)
            classification = classification_by_player[player_id]
            rookie = classification.startswith("rookie_")
            status = "expired" if classification in {"ordinary_expiration", "rookie_option_eligible"} else "active"
            end = 2025 if status == "expired" else (2027 if classification == "rookie_initial_taxi_paused" else 2026)
            rookie_year = 2025 if rookie else None
            if rookie: rookie_number += 1
            draft_round = ((rookie_number - 1) % 3) + 1 if rookie else None
            salary = {1: 15, 2: 3, 3: 1}.get(draft_round, 5 + n % 7)
            player_values.append(
                f"({_quote(player_id)},{_quote(player_id)},"
                f"{_quote('Synthetic Player '+str(n))},'WR',true,"
                f"{_quote(rookie_year)},{_quote(rookie_year)},"
                f"{draft_round if rookie_year else 'null'},"
                f"{'true' if rookie_year else 'false'})"
            )
            agreement_values.append(f"({_quote(agreement_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},{_quote(player_id)},{_quote('rookie' if rookie else 'veteran')},'imported_initial_contract',2025,2025,{end},{_quote(status)})")
            source_obligation = "active" if status == "active" else "satisfied"
            season_values.append(f"({_quote(_id(self.namespace+':contract-season:2025:'+player_id))},{_quote(agreement_id)},{_quote(i.source_season_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},2025,{salary},{salary},null,{_quote(source_obligation)},'synthetic_domain',false,null)")
            if classification not in {"ordinary_expiration"}:
                option = classification == "rookie_option_eligible"
                target_salary = {1: 25, 2: 15, 3: 7}[draft_round] if option else salary
                guaranteed = 1 if option else None
                season_values.append(f"({_quote(_id(self.namespace+':contract-season:2026:'+player_id))},{_quote(agreement_id)},{_quote(i.target_season_id)},{_quote(i.league_id)},{_quote(team_id)},{_quote(player_id)},2026,{target_salary},{target_salary},{_quote(guaranteed)},'scheduled','synthetic_domain',{'true' if option else 'false'},{_quote('rookie_one_time_resign_option' if option else None)})")
            board_id = None
            taxi_id = None
            if rookie:
                board_id = _id(self.namespace + ":rookie-board:" + player_id)
                round_pick = ((rookie_number - 1) // 3) + 1
                overall = (draft_round - 1) * 10 + round_pick
                option_salary = {1: 25, 2: 15, 3: 7}[draft_round]
                board_values.append(f"({_quote(board_id)},{_quote(i.league_id)},{_quote(player_id)},{_quote(team_id)},2025,{draft_round},{round_pick},{overall},true,{salary},{2 if draft_round in (1,2) else 1},{option_salary},1,false,'rookie_draft_board_assignment','{{\"source\":\"canonical_fixture\"}}',encode(digest({_quote(self.namespace+':board:'+player_id)},'sha256'),'hex'))")
                if classification == "rookie_initial_taxi_paused":
                    taxi_id = _id(self.namespace + ":rookie-taxi:" + player_id)
                    taxi_values.append(f"({_quote(taxi_id)},{_quote(i.league_id)},{_quote(player_id)},{_quote(team_id)},{_quote(i.source_season_id)},{_quote(board_id)},null,{salary},round({salary}*.5,2),false,true,2026,'{{\"source\":\"canonical_fixture\"}}',encode(digest({_quote(self.namespace+':taxi:'+player_id)},'sha256'),'hex'))")
            classification_values.append(f"({_quote(i.league_id)},2025,2026,{_quote(agreement_id)},{_quote(player_id)},{_quote(classification)},{_quote(board_id)},{_quote(taxi_id)},false,'{{\"source\":\"canonical_fixture\"}}',encode(digest({_quote(self.namespace+':classification:'+player_id)},'sha256'),'hex'))")
        statements.append("insert into public.player_universe(sleeper_id,canonical_player_id,player_name,pos,active,rookie_class_year,draft_year,draft_round,is_rookie_contract) values " + ",".join(player_values))
        statements.append("insert into public.contract_agreements(id,league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,signed_season,start_season,end_season,status) values " + ",".join(agreement_values))
        # Every tuple already uses the final contract_seasons column order.
        normalized_season_values = season_values
        statements.append("insert into public.contract_seasons(id,contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,cap_hit,guaranteed_salary,obligation_status,source,is_option_year,option_type) values " + ",".join(normalized_season_values))
        if board_values:
            statements.append("insert into public.rookie_draft_board_assignments(id,league_id,player_id,original_league_team_id,draft_year,draft_round,round_pick,overall_pick,rookie_contract_provenance,original_salary,original_contract_term,one_time_option_salary,one_time_option_term,option_consumed,source_type,source_event,deterministic_fingerprint) values " + ",".join(board_values))
        if taxi_values:
            statements.append("insert into public.rookie_taxi_assignments(id,league_id,player_id,league_team_id,league_season_id,rookie_draft_assignment_id,source_roster_assignment_id,normal_annual_charge,taxi_charge,contract_year_consumed,locked,unlock_target_season,evidence,deterministic_fingerprint) values " + ",".join(taxi_values))
        statements.append("insert into public.contract_rollover_classifications(league_id,source_season,target_season,contract_agreement_id,player_id,classification,rookie_draft_assignment_id,taxi_assignment_id,option_consumed,classification_evidence,deterministic_fingerprint) values " + ",".join(classification_values))
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

    @staticmethod
    def assert_hosted_schema_compatibility(session: Any) -> dict[str, Any]:
        """Read-only preflight for constraints the domain bootstrap relies on."""
        report = session.json_query("""select jsonb_build_object(
          'membership_role_check',(
            select pg_get_constraintdef(c.oid) from pg_constraint c
            join pg_class t on t.oid=c.conrelid join pg_namespace n on n.oid=t.relnamespace
            where n.nspname='public' and t.relname='league_memberships'
              and c.conname='league_memberships_role_check'),
          'membership_columns',(select jsonb_object_agg(column_name,jsonb_build_object(
            'nullable',is_nullable,'type',data_type)) from information_schema.columns
            where table_schema='public' and table_name='league_memberships'),
          'league_season_columns',(select jsonb_object_agg(column_name,jsonb_build_object(
            'nullable',is_nullable,'type',data_type)) from information_schema.columns
            where table_schema='public' and table_name='league_seasons'),
          'table_constraints',(select jsonb_object_agg(t.relname||'.'||c.conname,pg_get_constraintdef(c.oid))
            from pg_constraint c join pg_class t on t.oid=c.conrelid
            join pg_namespace n on n.oid=t.relnamespace where n.nspname='public' and t.relname in(
              'league_memberships','league_seasons','league_teams','teams','season_team_mappings',
              'player_universe','contract_agreements','contract_seasons','league_rules',
              'cap_adjustments','dead_cap_ledger','draft_inventory_classes','draft_pick_assets',
              'league_rookie_class_authorities')))
        """)
        if not isinstance(report, dict):
            raise RuntimeError("hosted schema preflight returned no report")
        role_check = str(report.get("membership_role_check") or "").lower()
        if not role_check:
            raise RuntimeError("league_memberships_role_check is missing")
        quoted_roles = frozenset(re.findall(r"'([^']+)'", role_check))
        if not CANONICAL_MEMBERSHIP_ROLES.issubset(quoted_roles):
            raise RuntimeError("league_memberships_role_check missing canonical roles: "
                f"required={sorted(CANONICAL_MEMBERSHIP_ROLES)}, actual={sorted(quoted_roles)}")
        membership_columns = report.get("membership_columns") or {}
        required_membership_columns = {"id", "league_id", "user_id", "role", "league_team_id"}
        missing = sorted(required_membership_columns - set(membership_columns))
        if missing:
            raise RuntimeError(f"league_memberships columns missing: {missing}")
        required_season_columns = {"id", "league_id", "season", "sleeper_league_id", "is_active",
                                   "status", "previous_league_season_id"}
        missing = sorted(required_season_columns - set(report.get("league_season_columns") or {}))
        if missing:
            raise RuntimeError(f"league_seasons columns missing: {missing}")
        return report

    def history_source(self) -> dict[str, Any]:
        i = self.identity
        assignments = {n: [] for n in range(1, 11)}
        all_players = [p for rows in i.lifecycle_player_ids.values() for p in rows]
        agreement_team = {player_id: (n - 1) % 10 + 1
                          for n, player_id in enumerate(all_players, 1)}
        for player_id in i.roster_player_ids:
            assignments[agreement_team[player_id]].append(player_id)
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
                *i.roster_player_ids,
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
            if x not in self.externally_provisioned_actor_ids
        )
        foreign_league = _quote(i.foreign_league_id)
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
delete from public.contract_rollover_classifications where league_id={league};
delete from public.rookie_taxi_assignments where league_id={league};
delete from public.rookie_draft_board_assignments where league_id={league};
delete from public.contract_seasons where league_id={league};
delete from public.contract_agreements where league_id={league};
delete from public.league_rookie_class_authorities where league_id={league};
delete from public.draft_pick_assets where league_id={league};
delete from public.draft_inventory_classes where league_id={league};
delete from public.dead_cap_ledger where league_id={league};
delete from public.cap_adjustments where league_id={league};
delete from public.league_rules where league_id={league};
delete from public.league_memberships where league_id={league};
delete from public.league_memberships where league_id={foreign_league};
delete from public.teams where league_id={league};
delete from public.teams where league_id={foreign_league};
delete from public.league_teams where league_id={league};
delete from public.league_teams where league_id={foreign_league};
delete from public.league_seasons where league_id={league};
delete from public.player_universe where sleeper_id in ({players});
delete from public.leagues where id={league};
delete from public.leagues where id={foreign_league};
{f'delete from auth.users where id in ({actors});' if actors else ''}
commit;"""
