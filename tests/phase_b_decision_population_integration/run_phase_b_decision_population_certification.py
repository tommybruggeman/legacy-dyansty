#!/usr/bin/env python3
"""Rollback-only Phase B integration certification. Disposable database only."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from season_engine.decision_population_fingerprints import (
    compact_json, commissioner_case_fingerprint, commissioner_case_material,
    owner_case_fingerprint, owner_case_material,
    population_fingerprint as phaseb_population_fingerprint,
)
from tests.fixtures.season_rollover_domain_factory import SeasonRolloverDomainFactory, _id
from tests.season_rollover_hosted_integration.psql_client import PsqlSession
from tests.fixtures.certification_sentinel import count_sql, expected_sentinel

SENTINEL = count_sql(expected_sentinel("rollover-cardinality-certification"))
IMMUTABLE_HISTORY_TABLES = (
    "season_roster_assignments", "season_team_mappings", "season_standings",
    "season_matchups", "season_playoff_brackets", "historical_capture_executions",
)


def audit_harness_immutable_writes() -> None:
    source = Path(__file__).read_text()
    for table in IMMUTABLE_HISTORY_TABLES:
        if re.search(rf"\b(?:delete\s+from|update)\s+(?:public\.)?{table}\b", source, re.I):
            raise RuntimeError(f"harness mutates immutable history table: {table}")


def check_sentinel(session: PsqlSession) -> None:
    if session.command(SENTINEL) != ["1:1"]:
        raise RuntimeError("disposable sentinel mismatch")


def fixture_sql(factory: SeasonRolloverDomainFactory) -> str:
    sql = factory.bootstrap_sql().strip()
    sql = sql.removeprefix("begin;").removesuffix("commit;")
    return sql


def setup(session: PsqlSession, label: str, owner_count: int, commissioner_count: int):
    factory = SeasonRolloverDomainFactory(label)
    i = factory.identity
    session.command("begin")
    session.command(fixture_sql(factory))
    owner_extra = max(0, owner_count - len(i.owner_player_ids))
    commissioner_extra = max(0, commissioner_count - len(i.commissioner_player_ids))
    if owner_extra:
        session.command(f"""with added as(select g,'{factory.namespace}-owner-extra-'||lpad(g::text,3,'0') player_id
          from generate_series(1,{owner_extra})g), players as(
          insert into public.player_universe(sleeper_id,canonical_player_id,player_name,pos,active,is_rookie_contract)
          select player_id,player_id,'Phase B Owner Extra '||g,'WR',true,false from added returning sleeper_id)
          insert into public.contract_agreements(id,league_id,league_team_id,player_id,sleeper_player_id,
          contract_type,origin,signed_season,start_season,end_season,status)
          select md5('{factory.namespace}-owner-extra-agreement-'||g)::uuid,'{i.league_id}','{i.team_ids[0]}',
          player_id,player_id,'veteran','imported_initial_contract',2025,2025,2025,'expired' from added""")
        session.command(f"""insert into public.contract_seasons(id,contract_id,league_season_id,league_id,
          league_team_id,player_id,season,salary,cap_hit,obligation_status,source,is_option_year)
          select md5('{factory.namespace}-owner-extra-season-'||g)::uuid,
          md5('{factory.namespace}-owner-extra-agreement-'||g)::uuid,'{i.source_season_id}','{i.league_id}',
          '{i.team_ids[0]}','{factory.namespace}-owner-extra-'||lpad(g::text,3,'0'),2025,5,5,'satisfied',
          'phaseb-certification',false from generate_series(1,{owner_extra})g""")
    if commissioner_extra:
        session.command(f"""with added as(select g,'{factory.namespace}-review-extra-'||lpad(g::text,3,'0') player_id
          from generate_series(1,{commissioner_extra})g), players as(
          insert into public.player_universe(sleeper_id,canonical_player_id,player_name,pos,active,is_rookie_contract)
          select player_id,player_id,'Phase B Review Extra '||g,'WR',true,false from added returning sleeper_id)
          insert into public.contract_agreements(id,league_id,league_team_id,player_id,sleeper_player_id,
          contract_type,origin,signed_season,start_season,end_season,status)
          select md5('{factory.namespace}-review-extra-agreement-'||g)::uuid,'{i.league_id}','{i.team_ids[0]}',
          player_id,player_id,'veteran','imported_initial_contract',2025,2025,2025,'expired' from added""")
        session.command(f"""insert into public.contract_seasons(id,contract_id,league_season_id,league_id,
          league_team_id,player_id,season,salary,cap_hit,obligation_status,source,is_option_year)
          select md5('{factory.namespace}-review-extra-season-'||g)::uuid,
          md5('{factory.namespace}-review-extra-agreement-'||g)::uuid,'{i.source_season_id}','{i.league_id}',
          '{i.team_ids[0]}','{factory.namespace}-review-extra-'||lpad(g::text,3,'0'),2025,5,5,'satisfied',
          'phaseb-certification',false from generate_series(1,{commissioner_extra})g""")
    # Non-cases are made ineligible before history is inserted. No captured row
    # is ever updated or deleted.
    session.command(f"""with ranked as(select id,row_number() over(order by player_id) n
      from public.contract_agreements where league_id='{i.league_id}' and player_id like '%-owner-%')
      update public.contract_agreements a set status='released' from ranked r
      where a.id=r.id and r.n>{owner_count}""")
    session.command(f"""with ranked as(select id,row_number() over(order by player_id) n
      from public.contract_agreements where league_id='{i.league_id}' and player_id like '%-review-%')
      update public.contract_agreements a set status='released' from ranked r
      where a.id=r.id and r.n>{commissioner_count}""")
    # Insert exactly the intended owner roster evidence once.
    session.command(f"""insert into public.season_roster_assignments(
      league_season_id,league_team_id,canonical_player_id,sleeper_player_id,
      player_name_snapshot,roster_designation,source)
      select '{i.source_season_id}',a.league_team_id,a.player_id,a.player_id,p.player_name,
             'active','phaseb-certification'
      from public.contract_agreements a join public.player_universe p on p.sleeper_id=a.player_id
      where a.league_id='{i.league_id}' and a.status='expired' and a.player_id like '%-owner-%'""")
    policy = session.command(f"""insert into public.league_rollover_policies(
      league_id,source_season,target_season,version,status,created_by,approved_by,approved_at,fingerprint)
      values('{i.league_id}',2025,2026,1,'approved','{i.commissioner_id}',
             '{i.commissioner_id}',now(),repeat('a',64)) returning id""")[-1]
    execution = session.command(f"""insert into public.rollover_executions(
      league_id,source_season,target_season,policy_id,policy_fingerprint,status,
      approval_status,preflight_fingerprint,before_state_fingerprint,notice_timestamp,owner_deadline)
      values('{i.league_id}',2025,2026,'{policy}',repeat('a',64),'decision_window_closed',
      'not_required',repeat('b',64),repeat('c',64),now()-interval '8 days',now()-interval '1 day') returning id""")[-1]
    return factory, execution


def expected(session: PsqlSession, execution: str, kind: str):
    fn = "phaseb_owner_expected_cases_private" if kind == "owner" else "phaseb_commissioner_expected_cases_private"
    return session.json_query(f"""select coalesce(jsonb_agg(jsonb_build_object(
      'case_key',case_key,'evidence_fingerprint',case_fingerprint)||case_payload order by case_key),'[]'::jsonb)
      from public.{fn}('{execution}')""") or []


def population_fingerprint(cases, kind):
    return phaseb_population_fingerprint(kind,cases)


def database_fingerprint(session, execution, kind, cases):
    payload = json.dumps(cases, sort_keys=True, separators=(",", ":")).replace("'", "''")
    return session.command(
        f"select public.phaseb_assert_population_private('{execution}','{kind}','{payload}'::jsonb)"
    )[-1]


def assert_case_parity(session, execution, kind, cases):
    for case in cases:
        payload = {k: case.get(k) for k in (
            "schema", "classification", "league_id", "source_season", "target_season",
            "agreement_id", "player_id", "league_team_id", "agreement_status",
            "roster_designation", "sleeper_player_id", "source_salary", "source_contract_years",
            "review_type", "source_identity", "roster_status") if k in case}
        # PostgreSQL independently normalizes the same semantic payload through
        # its private v3 positional encoder; the legacy object hasher is not a
        # valid cross-language parity oracle.
        encoded_payload=json.dumps(payload,sort_keys=True,separators=(",", ":")).replace("'", "''")
        prefix="phaseb_owner" if kind=="owner" else "phaseb_commissioner"
        pg_material=session.command(
            f"select public.{prefix}_case_material_v3_private('{encoded_payload}'::jsonb)"
        )[-1]
        pg=session.command(
            f"select public.{prefix}_case_fingerprint_v3_private('{encoded_payload}'::jsonb)"
        )[-1]
        py_material=owner_case_material(payload) if kind=="owner" else commissioner_case_material(payload)
        py_serialized=compact_json(py_material)
        py=owner_case_fingerprint(payload) if kind=="owner" else commissioner_case_fingerprint(payload)
        if pg != py or pg != case["evidence_fingerprint"]:
            raise RuntimeError(json.dumps({"error":f"{kind} cross-language case fingerprint mismatch",
                "case_key":case.get("case_key"),"python_material":py_material,
                "python_serialized":py_serialized,"postgres_serialized":pg_material,
                "python_fingerprint":py,"postgres_fingerprint":pg,
                "expected_fingerprint":case.get("evidence_fingerprint")},ensure_ascii=False,sort_keys=True))
    db = database_fingerprint(session, execution, kind, cases)
    py = population_fingerprint(cases, kind)
    if db != py:
        raise RuntimeError(f"{kind} population fingerprint mismatch")
    return db


def seed_frozen_rows(session, execution, owner_cases, commissioner_cases, owner_fp, commissioner_fp):
    for c in owner_cases:
        session.command(f"""insert into public.rollover_owner_decisions(
          rollover_execution_id,league_id,source_season,target_season,league_team_id,
          player_id,agreement_id,initial_roster_status,initial_roster_slot,decision_status,
          execution_status,deadline,evidence,metadata)
          select '{execution}',x.league_id,x.source_season,x.target_season,'{c['league_team_id']}',
          '{c['player_id']}','{c['agreement_id']}','rostered','{c.get('roster_slot') or 'active'}',
          'planned_release','ready',x.owner_deadline,'{{}}',jsonb_build_object(
          'evidence_fingerprint','{c['evidence_fingerprint']}') from public.rollover_executions x where x.id='{execution}'""")
    for c in commissioner_cases:
        session.command(f"""insert into public.rollover_commissioner_reviews(
          rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,
          league_team_id,review_type,review_status,review_state,execution_status,evidence,
          evidence_fingerprint,review_fingerprint,revision_number,phaseb_case_key,metadata)
          select '{execution}',x.league_id,x.source_season,x.target_season,'{c['player_id']}',
          '{c['agreement_id']}','{c['league_team_id']}','{c['review_type']}','review_required',
          'pending','pending','{{}}','{c['evidence_fingerprint']}',repeat('d',64),0,
          '{c['case_key']}',jsonb_build_object('phaseb_case_key','{c['case_key']}',
          'phaseb_case_fingerprint','{c['evidence_fingerprint']}')
          from public.rollover_executions x where x.id='{execution}'""")
    session.command(f"""update public.rollover_executions set decision_population_fingerprint='{owner_fp}',
      metadata=metadata||jsonb_build_object('owner_expected_set_fingerprint','{owner_fp}',
      'commissioner_expected_set_fingerprint','{commissioner_fp}') where id='{execution}'""")


def expect_rejection(session, execution, kind, cases, mutation, marker):
    changed = mutation([dict(c) for c in cases])
    payload = json.dumps(changed, sort_keys=True, separators=(",", ":")).replace("'", "''")
    session.command(f"""do $$begin
      begin perform public.phaseb_assert_population_private('{execution}','{kind}','{payload}'::jsonb);
       raise exception 'negative_case_accepted:{marker}';
      exception when others then if sqlerrm='negative_case_accepted:{marker}' then raise;end if;end;
      if exists(select 1 from public.rollover_owner_decisions where rollover_execution_id='{execution}')
       or exists(select 1 from public.rollover_commissioner_reviews where rollover_execution_id='{execution}')
       or (select status from public.rollover_executions where id='{execution}')<>'decision_window_closed'
      then raise exception 'negative_partial_state:{marker}';end if;
    end$$""")


def catalog(session):
    session.command("""do $$begin
      if exists(select 1 from pg_constraint c where c.conrelid='public.rollover_commissioner_reviews'::regclass
       and pg_get_constraintdef(c.oid) like 'UNIQUE (rollover_execution_id, player_id, review_type)%') then raise exception 'old uniqueness';end if;
      if not exists(select 1 from pg_class i join pg_index x on x.indexrelid=i.oid where
       x.indrelid='public.rollover_commissioner_reviews'::regclass and i.relname='rollover_reviews_execution_phaseb_case_uidx' and x.indisunique) then raise exception 'new uniqueness';end if;
      if exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'
       and p.proname like 'phaseb%private' and (not p.prosecdef or p.proconfig is distinct from array['search_path=pg_catalog, public']::text[]
       or has_function_privilege('authenticated',p.oid,'execute') or has_function_privilege('anon',p.oid,'execute') or has_function_privilege('public',p.oid,'execute'))) then raise exception 'private posture';end if;
    end$$""")


def state_counts(session):
    tables = (
        "leagues", "league_seasons", "league_teams", "player_universe",
        "contract_agreements", "contract_seasons", "season_roster_assignments",
        "league_rollover_policies", "rollover_executions", "rollover_owner_decisions",
        "rollover_commissioner_reviews", "rollover_execution_operation_results",
        "free_agent_publications",
    )
    return tuple(int(session.command(f"select count(*) from public.{table}")[-1]) for table in tables)


def main():
    if any(k.startswith("LEGACY_PROD_DB_") for k in os.environ):
        raise SystemExit("production variables must be absent")
    audit_harness_immutable_writes()
    session = PsqlSession()
    positive, negative = {}, {}
    try:
        check_sentinel(session); catalog(session)
        baseline = state_counts(session)
        for kind, sizes in (("owner", (0, 1, 108, 120)), ("commissioner", (0, 1, 13, 25))):
            for size in sizes:
                print(f"[Phase B] {kind} positive {size}",flush=True)
                owner_n = size if kind == "owner" else 0
                commissioner_n = size if kind == "commissioner" else 0
                factory, execution = setup(session, f"phaseb-{kind}-{size}", owner_n, commissioner_n)
                owner_cases, commissioner_cases = expected(session, execution, "owner"), expected(session, execution, "commissioner")
                selected = owner_cases if kind == "owner" else commissioner_cases
                if len(selected) != size: raise RuntimeError(f"{kind} expected {size}, got {len(selected)}")
                owner_fp = assert_case_parity(session, execution, "owner", owner_cases)
                commissioner_fp = assert_case_parity(session, execution, "commissioner", commissioner_cases)
                seed_frozen_rows(session, execution, owner_cases, commissioner_cases, owner_fp, commissioner_fp)
                session.command(f"update public.rollover_executions set status='authority_initializing' where id='{execution}'")
                positive[f"{kind}:{size}"] = "PASS"
                session.command("rollback"); check_sentinel(session)
                if state_counts(session) != baseline: raise RuntimeError(f"positive rollback residue:{kind}:{size}")
        factory, execution = setup(session, "phaseb-negative", 10, 10)
        foreign_factory=SeasonRolloverDomainFactory("phaseb-negative-foreign")
        session.command(fixture_sql(foreign_factory))
        foreign_identity=foreign_factory.identity
        foreign_player=foreign_identity.owner_player_ids[0]
        foreign_agreement=_id(foreign_factory.namespace+":agreement:"+foreign_player)
        foreign_team=foreign_identity.team_ids[0]
        for kind in ("owner", "commissioner"):
            cases = expected(session, execution, kind)
            def missing(x): return x[:-1]
            def added(x):
                row=dict(x[0]);row["case_key"]="foreign:"+row["case_key"];return x+[row]
            def duplicate(x): return x+[dict(x[0])]
            def stale(x): x[0]["evidence_fingerprint"]="0"*64;return x
            def cross(x):
                x[0]["league_id"]=foreign_identity.league_id;x[0]["case_key"]="cross:"+x[0]["case_key"];return x
            mutations=[("missing",missing),("added",added),("duplicate",duplicate),("stale",stale),("cross_league",cross)]
            if kind=="owner":
                def foreign_agreement_local_team(x):
                    x[0]["agreement_id"]=foreign_agreement;x[0]["evidence_fingerprint"]=owner_case_fingerprint(x[0]);return x
                def local_agreement_foreign_team(x):
                    x[0]["league_team_id"]=foreign_team;x[0]["evidence_fingerprint"]=owner_case_fingerprint(x[0]);return x
                def foreign_agreement_and_team(x):
                    x[0]["agreement_id"]=foreign_agreement
                    x[0]["player_id"]=foreign_player;x[0]["league_team_id"]=foreign_team;x[0]["sleeper_player_id"]=foreign_player
                    x[0]["evidence_fingerprint"]=owner_case_fingerprint(x[0]);return x
                mutations.extend((("foreign_agreement_local_team",foreign_agreement_local_team),
                    ("local_agreement_foreign_team",local_agreement_foreign_team),
                    ("equal_count_foreign_substitution",foreign_agreement_and_team)))
            else:
                def commissioner_foreign_agreement_team(x):
                    x[0]["agreement_id"]=foreign_agreement
                    x[0]["player_id"]=foreign_player;x[0]["league_team_id"]=foreign_team
                    x[0]["evidence_fingerprint"]=commissioner_case_fingerprint(x[0]);return x
                def commissioner_foreign_source_identity(x):
                    x[0]["source_identity"]=_id(foreign_factory.namespace+":foreign-source-identity")
                    x[0]["evidence_fingerprint"]=commissioner_case_fingerprint(x[0]);return x
                mutations.extend((("foreign_agreement_team",commissioner_foreign_agreement_team),
                    ("foreign_source_identity",commissioner_foreign_source_identity)))
            for name, mutation in mutations:
                print(f"[Phase B] {kind} {name}",flush=True)
                expect_rejection(session, execution, kind, cases, mutation, f"{kind}:{name}");negative[f"{kind}:{name}"]="PASS"
        # The new key permits distinct identities sharing player/review_type,
        # while a duplicate key is rejected by the real database index.
        commissioner = expected(session, execution, "commissioner")[0]
        print("[Phase B] commissioner distinct_identity",flush=True)
        for suffix in ("identity-1", "identity-2"):
            session.command(f"""insert into public.rollover_commissioner_reviews(
              rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,
              league_team_id,review_type,review_status,review_state,execution_status,evidence,
              evidence_fingerprint,review_fingerprint,revision_number,phaseb_case_key,metadata)
              select '{execution}',x.league_id,x.source_season,x.target_season,'{commissioner['player_id']}',
              '{commissioner['agreement_id']}','{commissioner['league_team_id']}','identity_conflict',
              'review_required','pending','pending','{{}}',repeat('e',64),repeat('f',64),0,
              '{commissioner['case_key']}:{suffix}','{{}}' from public.rollover_executions x where x.id='{execution}'""")
        duplicate_key=commissioner['case_key']+':identity-1'
        print("[Phase B] commissioner duplicate_phaseb_case_key",flush=True)
        session.command(f"""do $$begin begin
          insert into public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,
          target_season,player_id,agreement_id,league_team_id,review_type,review_status,review_state,
          execution_status,evidence,evidence_fingerprint,review_fingerprint,revision_number,phaseb_case_key,metadata)
          select '{execution}',x.league_id,x.source_season,x.target_season,'{commissioner['player_id']}',
          '{commissioner['agreement_id']}','{commissioner['league_team_id']}','identity_conflict',
          'review_required','pending','pending','{{}}',repeat('e',64),repeat('f',64),0,
          '{duplicate_key}','{{}}' from public.rollover_executions x where x.id='{execution}';
          raise exception 'duplicate_phaseb_case_key_accepted';
          exception when unique_violation then null;end;end$$""")
        negative["distinct_identity"]="PASS"
        negative["duplicate_phaseb_case_key"]="PASS"
        session.command("rollback");check_sentinel(session)
        if state_counts(session) != baseline: raise RuntimeError("negative rollback residue")
        print(json.dumps({"sentinel":"PASS","catalog":"PASS","positive":positive,"negative":negative,
          "cross_language":"PASS","rows_left":0,"execution":False,"publication":False,"external_calls":False},sort_keys=True))
    finally:
        try: session.command("rollback")
        except Exception: pass
        session.close()


if __name__ == "__main__": main()
