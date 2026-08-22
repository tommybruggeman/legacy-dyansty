#!/usr/bin/env python3
"""Phase E hosted disposable certification. Never use against production."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.season_rollover_ui import bounded_review_page
from services.strict_pagination import PaginationIntegrityError, complete_rows, exact_count
from tests.fixtures.certification_sentinel import expected_sentinel

DB_ENV = (
    "PHASE3B5H_TEST_DB_HOST", "PHASE3B5H_TEST_DB_PORT", "PHASE3B5H_TEST_DB_NAME",
    "PHASE3B5H_TEST_DB_USER", "PHASE3B5H_TEST_DB_PASSWORD",
)
REST_ENV = (
    "PHASE3B5H_TEST_SUPABASE_URL", "PHASE3B5H_TEST_SUPABASE_ANON_KEY",
    "PHASE3B5H_TEST_SUPABASE_SERVICE_ROLE_KEY",
)
AUTH_PASSWORD_ENV = "PHASE3B5H_TEST_AUTH_PASSWORD"
AUTH_IDENTITIES = {
    "commissioner": "phasee-commissioner@rollover-cardinality-certification.invalid",
    "owner": "phasee-owner@rollover-cardinality-certification.invalid",
    "foreign": "phasee-foreign-owner@rollover-cardinality-certification.invalid",
}
SIZES = (1, 10, 32, 100, 2000)
RELATIONS = (
    "league_teams", "league_memberships", "season_team_mappings", "season_roster_assignments",
    "contract_agreements", "contract_seasons", "rollover_owner_decisions", "rollover_commissioner_reviews",
)
TABLE = "phasee_hosted_pagination_fixture"
SENTINEL = expected_sentinel("rollover-cardinality-certification")


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        raise RuntimeError("Supabase Auth returned a malformed access token") from None
    if not str(payload.get("sub") or "") or payload.get("role") != "authenticated":
        raise RuntimeError("Supabase Auth token lacks an authenticated subject")
    return payload


def api_headers(apikey: str, key_type: str, *, user_token: str | None = None) -> dict[str, str]:
    if key_type not in {"publishable", "secret"}:
        raise ValueError("key_type must be publishable or secret")
    headers = {"apikey": apikey}
    if user_token is not None:
        if key_type != "publishable" or user_token.startswith(("sb_publishable_", "sb_secret_")):
            raise ValueError("Authorization bearer must be a user access token used with the publishable key")
        headers["Authorization"] = f"Bearer {user_token}"
    return headers


def sanitized_http_error(url: str, status: int, raw: bytes, key_type: str) -> str:
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        payload = {}
    code = str(payload.get("error_code") or payload.get("code") or payload.get("error") or "unknown")
    message = str(payload.get("msg") or payload.get("message") or payload.get("error_description") or "unavailable")
    if "sb_" in code or "bearer " in code.lower(): code = "[redacted]"
    if "sb_" in message or "bearer " in message.lower(): message = "[redacted]"
    return (f"HTTP request failed: endpoint={urlsplit(url).path} status={status} "
            f"code={code[:120]!r} message={message[:240]!r} key_type={key_type}")


def auth_request(url: str, apikey: str, key_type: str, *, user_token: str | None = None,
                 method="GET", payload=None,
                 accepted=(200,)) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(url, headers={**api_headers(apikey, key_type, user_token=user_token),
                                    "Content-Type": "application/json"}, data=body, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    if status not in accepted:
        raise RuntimeError(sanitized_http_error(url, status, raw, key_type))
    try:
        return status, json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise RuntimeError("Supabase Auth returned malformed JSON") from None


class DisposableAuth:
    def __init__(self, url: str, anon_key: str, service_key: str, password: str):
        self.url, self.anon_key, self.service_key, self.password = url.rstrip("/"), anon_key, service_key, password
        self.created: list[str] = []

    def establish(self) -> dict[str, dict[str, str]]:
        identities = {}
        for intended_role, email in AUTH_IDENTITIES.items():
            status, created = auth_request(f"{self.url}/auth/v1/admin/users", self.service_key, "secret", method="POST",
                payload={"email": email, "password": self.password, "email_confirm": True,
                         "user_metadata": {"phasee_disposable_role": intended_role}}, accepted=(200, 201, 422))
            if status in (200, 201):
                created_id = str(created.get("id") or created.get("user", {}).get("id") or "")
                if not created_id: raise RuntimeError(f"Supabase Auth did not return the {intended_role} user id")
                self.created.append(created_id)
            _, session = auth_request(f"{self.url}/auth/v1/token?grant_type=password",
                self.anon_key, "publishable", method="POST", payload={"email": email, "password": self.password})
            token = str(session.get("access_token") or "")
            claims = jwt_claims(token)
            _, verified = auth_request(f"{self.url}/auth/v1/user",
                self.anon_key, "publishable", user_token=token)
            subject = str(verified.get("id") or "")
            verified_email = str(verified.get("email") or "").lower()
            metadata_role = str((verified.get("user_metadata") or {}).get("phasee_disposable_role") or "")
            if subject != str(claims["sub"]) or verified_email != email or metadata_role != intended_role:
                raise RuntimeError(f"Supabase Auth identity verification failed for {intended_role}")
            identities[intended_role] = {"id": subject, "token": token}
        if len({item["id"] for item in identities.values()}) != 3:
            raise RuntimeError("Supabase Auth identities are not distinct")
        return identities

    def cleanup(self):
        failures = []
        for user_id in reversed(self.created):
            try: auth_request(f"{self.url}/auth/v1/admin/users/{user_id}", self.service_key, "secret",
                              method="DELETE", accepted=(200, 204))
            except RuntimeError: failures.append(user_id)
        if failures: raise RuntimeError("one or more runner-created disposable Auth users could not be removed")


class Database:
    def __init__(self):
        env = os.environ.copy()
        env.update(PGHOST=env[DB_ENV[0]], PGPORT=env[DB_ENV[1]], PGDATABASE=env[DB_ENV[2]],
                   PGUSER=env[DB_ENV[3]], PGPASSWORD=env[DB_ENV[4]])
        self.env = env

    def run(self, sql: str, *, json_result: bool = False) -> Any:
        result = subprocess.run(
            ["/opt/homebrew/opt/postgresql@16/bin/psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
            cwd=ROOT, env=self.env, text=True, capture_output=True, check=True,
        )
        raw = result.stdout.strip()
        return json.loads(raw if raw.startswith(("[", "{")) else raw.splitlines()[-1]) if json_result else raw


class RestResponse:
    def __init__(self, data: list[dict[str, Any]], count: int | None, body_bytes: int, content_range: str | None):
        self.data, self.count = data, count
        self.body_bytes, self.content_range = body_bytes, content_range


class RestClient:
    def __init__(self, url: str, apikey: str, key_type: str, user_token: str | None = None):
        self.url, self.apikey, self.key_type, self.user_token = url.rstrip("/"), apikey, key_type, user_token
        api_headers(apikey, key_type, user_token=user_token)
        self.requests: list[dict[str, Any]] = []

    def table(self, name: str):
        if name != TABLE:
            raise ValueError("runner REST client is fixture-table only")
        return RestQuery(self, name)


class RestQuery:
    def __init__(self, client: RestClient, table: str):
        self.client, self.table, self.columns = client, table, "*"
        self.filters: dict[str, Any] = {}
        self.order_key, self.descending = "id", False
        self.start, self.end, self.want_count, self.head = 0, 499, False, False

    def select(self, columns="*", count=None, head=False):
        self.columns, self.want_count, self.head = columns, count == "exact", bool(head)
        return self

    def eq(self, key, value): self.filters[key] = value; return self
    def order(self, key, desc=False): self.order_key, self.descending = key, bool(desc); return self
    def range(self, start, end): self.start, self.end = int(start), int(end); return self

    def execute(self):
        params = {"select": self.columns, "order": f"{self.order_key}.{'desc' if self.descending else 'asc'}"}
        params.update({key: f"eq.{value}" for key, value in self.filters.items()})
        headers = {**api_headers(self.client.apikey, self.client.key_type, user_token=self.client.user_token),
                   "Accept": "application/json", "Range": f"{self.start}-{self.end}"}
        if self.want_count: headers["Prefer"] = "count=exact"
        request = Request(f"{self.client.url}/rest/v1/{self.table}?{urlencode(params)}",
                          headers=headers, method="HEAD" if self.head else "GET")
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                content_range = response.headers.get("Content-Range")
        except HTTPError as exc:
            raise RuntimeError(sanitized_http_error(request.full_url, exc.code, exc.read(), self.client.key_type)) from None
        count = None
        if content_range and "/" in content_range and content_range.rsplit("/", 1)[1] != "*":
            count = int(content_range.rsplit("/", 1)[1])
        data = [] if self.head or not body else json.loads(body)
        self.client.requests.append({"method": "HEAD" if self.head else "GET", "range": headers["Range"],
            "content_range": content_range, "body_bytes": len(body), "rows": len(data),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
        return RestResponse(data, count, len(body), content_range)


class FaultClient:
    """Inject transport defects after real hosted responses to prove fail-closed behavior."""
    def __init__(self, base: RestClient, fault: str): self.base, self.fault, self.calls = base, fault, 0
    def table(self, name): return FaultQuery(self, self.base.table(name))


class FaultQuery:
    def __init__(self, owner: FaultClient, query: RestQuery): self.owner, self.query = owner, query
    def select(self, *a, **k): self.query.select(*a, **k); return self
    def eq(self, *a): self.query.eq(*a); return self
    def order(self, *a, **k): self.query.order(*a, **k); return self
    def range(self, *a): self.query.range(*a); return self
    def execute(self):
        response = self.query.execute(); call = self.owner.calls; self.owner.calls += 1
        if self.owner.fault == "missing_count": response.count = None
        elif self.owner.fault == "changing_count" and call: response.count += 1
        elif self.owner.fault == "early_page" and call: response.data = []
        elif self.owner.fault == "replayed_page" and call:
            replay = self.owner.base.table(TABLE).select("*").eq("run_id", self.query.filters["run_id"]).eq(
                "relation_name", self.query.filters["relation_name"]).eq("cardinality", self.query.filters["cardinality"]
            ).order("id").range(0, self.query.end - self.query.start).execute()
            response.data = replay.data
        elif self.owner.fault == "overrun" and response.data:
            response.count = max(0, response.count - 1)
        return response


def sql_literal(value: str) -> str: return "'" + value.replace("'", "''") + "'"
def stable_uuid(value: str) -> str: return str(UUID(bytes=hashlib.md5(value.encode()).digest()))


def sentinel(db: Database) -> tuple[str, str, str]:
    row = db.run("select json_build_array(environment_name,environment_type,parent_project) "
                 "from public.environment_identity where singleton", json_result=True)
    return tuple(row)


def state(db: Database) -> dict[str, int]:
    return db.run("select json_build_object('executions',(select count(*) from public.rollover_execution_runs),"
                  "'publications',(select count(*) from public.rollover_target_season_authority_publications)+"
                  "(select count(*) from public.rollover_target_cap_authority_publications)+"
                  "(select count(*) from public.rollover_target_market_visibility_publications))", json_result=True)


def setup(db: Database, run_id: str, commissioner: str, owner: str, foreign: str):
    db.run(f"""
      drop table if exists public.{TABLE};
      create table public.{TABLE}(
        id uuid primary key,run_id uuid not null,relation_name text not null,cardinality integer not null,
        league_id uuid not null,league_team_id uuid not null,ordinal integer not null,player_id text not null,
        review_type text not null,review_state text not null,commissioner_id uuid not null,owner_id uuid not null,
        unique(run_id,relation_name,cardinality,league_id,ordinal));
      alter table public.{TABLE} enable row level security;
      revoke all on public.{TABLE} from public,anon,authenticated;
      grant select on public.{TABLE} to authenticated,service_role;
      create policy phasee_service on public.{TABLE} for all to service_role using(true) with check(true);
      create policy phasee_authenticated on public.{TABLE} for select to authenticated using(
        auth.uid()=commissioner_id or auth.uid()=owner_id);
      insert into public.{TABLE}
      select md5({sql_literal(run_id)}||':'||r||':'||n||':'||i||':home')::uuid,{sql_literal(run_id)}::uuid,r,n,
        md5({sql_literal(run_id)}||':league:home')::uuid,
        md5({sql_literal(run_id)}||':team:'||(i%greatest(1,least(n,32))))::uuid,i,'player-'||i,
        case when i%2=0 then 'release' else 'option' end,case when i%7=0 then 'blocked' else 'approved' end,
        {sql_literal(commissioner)}::uuid,case when i%greatest(1,least(n,32))=0 then {sql_literal(owner)}::uuid else '00000000-0000-0000-0000-000000000000'::uuid end
      from unnest(array[{','.join(sql_literal(x) for x in RELATIONS)}]) r
      cross join unnest(array[{','.join(map(str,SIZES))}]) n cross join lateral generate_series(1,n) i;
      insert into public.{TABLE}
      select md5({sql_literal(run_id)}||':'||r||':'||n||':'||i||':foreign')::uuid,{sql_literal(run_id)}::uuid,r,n,
        md5({sql_literal(run_id)}||':league:foreign')::uuid,md5({sql_literal(run_id)}||':foreign-team:'||i)::uuid,
        i,'foreign-'||i,'foreign','blocked',{sql_literal(foreign)}::uuid,{sql_literal(foreign)}::uuid
      from unnest(array[{','.join(sql_literal(x) for x in RELATIONS)}]) r
      cross join unnest(array[{','.join(map(str,SIZES))}]) n cross join lateral generate_series(1,n) i;
      notify pgrst,'reload schema';""")


def wait_for_rest(client: RestClient, run_id: str):
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            response = client.table(TABLE).select("id", count="exact").eq("run_id", run_id).range(0, 0).execute()
            if response.count is not None: return
        except RuntimeError: pass
        time.sleep(1)
    raise RuntimeError("PostgREST schema cache did not expose certification fixture")


def assert_faults(service: RestClient, filters: dict[str, Any]):
    messages = {}
    for fault in ("missing_count", "changing_count", "early_page", "replayed_page", "overrun"):
        try:
            complete_rows(FaultClient(service, fault), TABLE, filters=filters, page_size=500)
        except PaginationIntegrityError as exc: messages[fault] = str(exc)
        else: raise RuntimeError(f"strict paginator accepted injected {fault}")
    return messages


def main():
    missing = [name for name in (*DB_ENV, *REST_ENV, AUTH_PASSWORD_ENV) if not os.getenv(name, "").strip()]
    production = sorted(name for name in os.environ if name.startswith("LEGACY_PROD_DB_") and os.getenv(name))
    if missing: raise RuntimeError("missing disposable variables: " + ", ".join(missing))
    if production: raise RuntimeError("production variables are forbidden: " + ", ".join(production))
    db = Database(); run_id = str(uuid4()); before = None; created = False; auth = None
    url, anon, service_key = (os.environ[name] for name in REST_ENV)
    service = RestClient(url, service_key, "secret")
    evidence: dict[str, Any] = {"sentinel": "PASS", "external_calls": False}
    try:
        if sentinel(db) != SENTINEL: raise RuntimeError("disposable sentinel mismatch")
        before = state(db)
        auth = DisposableAuth(url, anon, service_key, os.environ[AUTH_PASSWORD_ENV])
        identities = auth.establish()
        commissioner, owner, foreign = (identities[name]["id"] for name in ("commissioner", "owner", "foreign"))
        commissioner_client = RestClient(url, anon, "publishable", identities["commissioner"]["token"])
        owner_client = RestClient(url, anon, "publishable", identities["owner"]["token"])
        foreign_client = RestClient(url, anon, "publishable", identities["foreign"]["token"])
        setup(db, run_id, commissioner, owner, foreign); created = True; wait_for_rest(service, run_id)
        matrix = []; home_league = stable_uuid(run_id+':league:home')
        for relation in RELATIONS:
            for size in SIZES:
                start = len(service.requests); filters = {"run_id": run_id, "relation_name": relation,
                    "cardinality": size, "league_id": home_league}
                rows, metrics = complete_rows(service, TABLE, filters=filters, page_size=500, with_metrics=True)
                requests = service.requests[start:]
                if len(rows) != size or len({row["id"] for row in rows}) != size: raise RuntimeError("matrix completeness mismatch")
                if any(not item["content_range"] or item["rows"] > 500 for item in requests): raise RuntimeError("Content-Range/page bound missing")
                if size == 2000 and metrics.backend_requests != 4: raise RuntimeError("2000 rows did not use four pages")
                matrix.append({"relation":relation,"rows":size,"requests":metrics.backend_requests,
                    "max_page_rows":max(item["rows"] for item in requests),"content_ranges":[x["content_range"] for x in requests]})
        head_start = len(service.requests); count = exact_count(service, TABLE, filters={"run_id":run_id,"relation_name":"league_teams","cardinality":2000,"league_id":home_league})
        head = service.requests[head_start:][-1]
        if count != 2000 or head["method"] != "HEAD" or head["body_bytes"] != 0 or head["rows"] != 0:
            raise RuntimeError("count-only HEAD contract failed")
        filters = {"run_id":run_id,"relation_name":"league_teams","cardinality":2000,"league_id":home_league}
        faults = assert_faults(service, filters)
        manipulations = []
        for label, client in (("commissioner",commissioner_client),("owner",owner_client),("foreign",foreign_client)):
            for desc in (False, True):
                for field, value in ((None,None),("review_type","option"),("review_state","blocked"),("player_id","player-1999")):
                    query = client.table(TABLE).select("*").eq("run_id",run_id).eq("relation_name","rollover_commissioner_reviews").eq("cardinality",2000)
                    if field: query=query.eq(field,value)
                    response = query.order("id",desc=desc).range(1500,1999).execute()
                    if label in {"commissioner","owner"} and any(row["league_id"] != home_league for row in response.data): raise RuntimeError("foreign league row escaped RLS")
                    if label == "owner" and any(row["owner_id"] != owner for row in response.data): raise RuntimeError("owner team scope escaped RLS")
                    if label == "foreign" and any(row["league_id"] == home_league for row in response.data): raise RuntimeError("foreign identity reached home league")
                    manipulations.append({"identity":label,"descending":desc,"filter":field or "none","rows":len(response.data),"foreign_rows":0})
        reviews, review_metrics = complete_rows(service, TABLE, filters={"run_id":run_id,
            "relation_name":"rollover_commissioner_reviews","cardinality":2000,"league_id":home_league},
            page_size=500, with_metrics=True)
        ui = bounded_review_page(reviews, status="exceptions", page=1, page_size=25)
        ui2 = bounded_review_page(list(reversed(reviews)), status="exceptions", page=1, page_size=25)
        deterministic = ui == ui2
        diagnostic = {"total":ui["total"],"filtered":ui["filtered"],"displayed":ui["displayed"],
            "page":ui["page"],"page_size":ui["page_size"],"first_id":ui["rows"][0]["id"] if ui["rows"] else None,
            "last_id":ui["rows"][-1]["id"] if ui["rows"] else None,"deterministic":deterministic,
            "backend_requests":review_metrics.backend_requests}
        if not deterministic or ui["total"]!=2000 or ui["displayed"]>25 or review_metrics.backend_requests!=4:
            raise RuntimeError("UI slice contract failed: " + json.dumps(diagnostic, sort_keys=True))
        plans = db.run(f"explain (analyze,buffers,format json) select * from public.{TABLE} where run_id={sql_literal(run_id)}::uuid and relation_name='league_teams' and cardinality=2000 order by id limit 500", json_result=True)
        evidence.update(matrix=matrix,count_only={"relation":"league_teams","exact_count":count,"downloaded_rows":0,"body_bytes":0},
                        fault_injection=faults,authorization=manipulations,ui_slice=diagnostic,query_plans=plans)
    finally:
        if created:
            db.run(f"drop table if exists public.{TABLE};notify pgrst,'reload schema';")
        if sentinel(db) != SENTINEL: raise RuntimeError("disposable sentinel changed")
        after = state(db)
        if before is not None and after != before: raise RuntimeError(f"execution/publication state changed: {before}->{after}")
        left = db.run(f"select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname={sql_literal(TABLE)}")
        if left != "0": raise RuntimeError("certification fixture rows/object left behind")
        if auth is not None: auth.cleanup()
        evidence.update(rows_left_behind=0,execution=False,publication=False,sentinel_after="PASS")
    print(json.dumps(evidence, sort_keys=True, indent=2))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(f"PHASE E FAILED: {exc}", file=sys.stderr)
        raise
