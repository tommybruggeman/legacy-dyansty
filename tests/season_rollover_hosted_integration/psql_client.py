from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from uuid import NAMESPACE_URL, uuid5

from supabase import create_client


COMPONENT_ENV = (
    "PHASE3B5H_TEST_DB_HOST", "PHASE3B5H_TEST_DB_PORT", "PHASE3B5H_TEST_DB_NAME",
    "PHASE3B5H_TEST_DB_USER", "PHASE3B5H_TEST_DB_PASSWORD",
)
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _literal(value: Any, cast: str | None = None) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) if cast == "jsonb" else str(value)
    result = "'" + text.replace("'", "''") + "'"
    return result + ("::" + cast if cast else "")


class _Auth:
    def __init__(self, actor: str | None): self.actor = actor
    def get_user(self):
        return SimpleNamespace(user=None if not self.actor else SimpleNamespace(id=self.actor))


@dataclass
class _Response:
    data: Any
    count: int | None = None


@dataclass(frozen=True)
class HostedAuthIdentity:
    user_id: str
    client: Any


class HostedAuthFixture:
    """Disposable Auth provisioning followed by real password sign-in."""
    def __init__(self, label: str, roles: tuple[str, ...] | None = None):
        required = ("PHASE3B5H_TEST_SUPABASE_URL", "PHASE3B5H_TEST_SUPABASE_ANON_KEY",
                    "PHASE3B5H_TEST_SUPABASE_SERVICE_ROLE_KEY", "PHASE3B5H_TEST_AUTH_PASSWORD")
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing: raise RuntimeError("missing disposable auth variables: " + ", ".join(missing))
        self.url, self.anon_key, self.service_key, self.password = (os.environ[name] for name in required)
        safe_label = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:40]
        selected_roles = roles or ("commissioner", "owner", "foreign-owner")
        self.emails = {role: f"phasef-{safe_label}-{role}@rollover-certification.invalid"
                       for role in selected_roles}
        self.admin = create_client(self.url, self.service_key)
        self.created: list[str] = []

    def establish(self) -> dict[str, HostedAuthIdentity]:
        if os.getenv("PHASE3B5H_TEST_LOCAL_PSQL_AUTH") == "1":
            service = PsqlSession(None, "service_role")
            try:
                identities = {}
                for role, email in self.emails.items():
                    user_id = str(uuid5(NAMESPACE_URL, email))
                    service.command(
                        "insert into auth.users(id,aud,role,email,raw_app_meta_data,raw_user_meta_data,created_at,updated_at) "
                        f"values('{user_id}','authenticated','authenticated','{email}',"
                        "'{\"provider\":\"email\",\"providers\":[\"email\"]}','{}',now(),now()) "
                        "on conflict(id) do nothing"
                    )
                    self.created.append(user_id)
                    identities[role] = HostedAuthIdentity(user_id, PsqlSession(user_id, "authenticated"))
                return identities
            finally:
                service.close()
        identities = {}
        try:
            for role, email in self.emails.items():
                client = create_client(self.url, self.anon_key)
                try:
                    signed_in = client.auth.sign_in_with_password({"email": email, "password": self.password})
                except Exception:
                    created = self.admin.auth.admin.create_user({"email": email, "password": self.password,
                        "email_confirm": True, "user_metadata": {"phasef_disposable_role": role}})
                    user = getattr(created, "user", None)
                    created_id = str(getattr(user, "id", "") or "")
                    if not created_id: raise RuntimeError(f"Auth did not return the {role} user id")
                    self.created.append(created_id)
                    signed_in = client.auth.sign_in_with_password({"email": email, "password": self.password})
                session = getattr(signed_in, "session", None)
                if not session or not str(getattr(session, "access_token", "") or ""):
                    raise RuntimeError(f"Auth sign-in returned no user JWT for {role}")
                verified = client.auth.get_user()
                user_id = str(getattr(getattr(verified, "user", None), "id", "") or "")
                if not user_id: raise RuntimeError(f"Auth user verification failed for {role}")
                if user_id not in self.created: self.created.append(user_id)
                identities[role] = HostedAuthIdentity(user_id, client)
            if len({identity.user_id for identity in identities.values()}) != len(self.emails):
                raise RuntimeError("hosted Auth identities are not distinct")
            return identities
        except Exception:
            self.cleanup()
            raise

    def cleanup(self) -> None:
        if os.getenv("PHASE3B5H_TEST_LOCAL_PSQL_AUTH") == "1":
            return
        failures = []
        for user_id in reversed(self.created):
            try: self.admin.auth.admin.delete_user(user_id)
            except Exception: failures.append(user_id)
        if failures: raise RuntimeError("one or more disposable Auth users could not be removed")


class PsqlSession:
    """One persistent libpq/psql connection; no HTTP client or URL is used."""
    def __init__(self, actor: str | None = None, role: str = "service_role"):
        missing = [name for name in COMPONENT_ENV if not os.getenv(name, "").strip()]
        if missing:
            raise RuntimeError("missing disposable component variables: " + ", ".join(missing))
        env = os.environ.copy()
        env.update(PGHOST=env[COMPONENT_ENV[0]], PGPORT=env[COMPONENT_ENV[1]],
                   PGDATABASE=env[COMPONENT_ENV[2]], PGUSER=env[COMPONENT_ENV[3]],
                   PGPASSWORD=env[COMPONENT_ENV[4]])
        env.pop("PHASE3B5H_TEST_DATABASE_URL", None)
        self._env = env
        self.actor, self.role, self.auth = actor, role, _Auth(actor)
        self.invocations: list[str] = []
        self.reconnect_count = 0
        self.closed_before_write_recovery_count = 0
        self._configuring = False
        self._start()
        self._configure()

    def _start(self):
        self.process = subprocess.Popen(
            ["/opt/homebrew/opt/postgresql@16/bin/psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=self._env,
        )
        self._started_at = time.monotonic()

    def _configure(self):
        self._configuring = True
        try:
            claims = {"sub": self.actor or "", "role": self.role}
            self.command("select set_config('request.jwt.claims', " + _literal(json.dumps(claims)) + ", false)")
            self.command("select set_config('request.jwt.claim.sub', " + _literal(self.actor or "") + ", false)")
            self.command("select set_config('request.jwt.claim.role', " + _literal(self.role) + ", false)")
            if os.getenv("PHASE3B5H_TEST_LOCAL_PSQL_AUTH") == "1":
                self.command("select set_config('search_path','public,extensions',false)")
        finally:
            self._configuring = False

    def _recycle_before_pool_expiry(self):
        """Reconnect only between commands; never replay an uncertain in-flight command."""
        if self._configuring or time.monotonic() - self._started_at < 20:
            return
        self.close()
        self.reconnect_count += 1
        self._start()
        self._configure()

    def close(self):
        if self.process.poll() is None:
            assert self.process.stdin
            try:
                self.process.stdin.write("\\q\n"); self.process.stdin.flush()
                self.process.wait(timeout=10)
            except BrokenPipeError:
                pass

    def command(self, sql: str) -> list[str]:
        self._recycle_before_pool_expiry()
        marker = "__CODEX_END_" + uuid4().hex + "__"
        assert self.process.stdin and self.process.stdout
        try:
            self.process.stdin.write(sql.rstrip().rstrip(";") + ";\n\\echo " + marker + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            remainder = self.process.stdout.read()
            if not remainder and self.closed_before_write_recovery_count < 64:
                self.closed_before_write_recovery_count += 1
                self.reconnect_count += 1
                self._start(); self._configure()
                return self.command(sql)
            raise RuntimeError(f"psql rejected command before completion (exit={self.process.poll()}, sql={sql[:300]!r}):\n" + remainder) from None
        lines = []
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("psql session ended: " + "\n".join(lines))
            line = line.rstrip("\n")
            if line == marker: break
            lines.append(line)
        errors = [line for line in lines if line.startswith("ERROR:") or line.startswith("psql:")]
        if errors:
            raise RuntimeError("\n".join(lines))
        return [line for line in lines if line and line not in {"SET", "BEGIN", "COMMIT"}]

    def execute_script(self, sql: str):
        self.command(sql)

    def json_query(self, sql: str) -> Any:
        lines = self.command(sql)
        if not lines: return None
        return json.loads(lines[-1])

    def table(self, name: str): return _Query(self, name)
    def rpc(self, name: str, params: dict[str, Any]): return _Rpc(self, name, params)


class _Query:
    def __init__(self, session: PsqlSession, table: str):
        if not _IDENT.match(table): raise ValueError("invalid table")
        self.session, self.name, self.columns = session, table, "*"
        self.filters: list[str] = []
        self.order_key: str | None = None
        self.start, self.end, self.head, self.want_count = 0, None, False, False
    def select(self, columns: str = "*", count: str | None = None, head: bool = False):
        if columns != "*" and not all(_IDENT.match(x.strip()) for x in columns.split(",")):
            raise ValueError("invalid columns")
        self.columns, self.want_count, self.head = columns, count == "exact", bool(head); return self
    def eq(self, field: str, value: Any):
        if not _IDENT.match(field): raise ValueError("invalid field")
        if value is None: self.filters.append(f"{field} is null")
        elif isinstance(value, bool): self.filters.append(f"{field} is {'true' if value else 'false'}")
        elif isinstance(value, int): self.filters.append(f"{field}={value}")
        else: self.filters.append(f"{field}={_literal(value)}")
        return self
    def in_(self, field: str, values: list[Any]):
        if not _IDENT.match(field): raise ValueError("invalid field")
        self.filters.append(f"{field} in (" + ",".join(_literal(x) for x in values) + ")")
        return self
    def order(self, field: str, desc: bool = False):
        if not _IDENT.match(field): raise ValueError("invalid order field")
        self.order_key = field + (" desc" if desc else ""); return self
    def range(self, start: int, end: int):
        self.start, self.end = int(start), int(end); return self
    def execute(self):
        where = " where " + " and ".join(self.filters) if self.filters else ""
        order = " order by " + self.order_key if self.order_key else ""
        count = int(self.session.json_query(f"select to_jsonb(count(*)) from public.{self.name}{where}"))
        if self.head: return _Response([], count if self.want_count else None)
        limit = "" if self.end is None else f" limit {max(0,self.end-self.start+1)} offset {self.start}"
        sql = f"select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) from (select {self.columns} from public.{self.name}{where}{order}{limit}) q"
        return _Response(self.session.json_query(sql), count if self.want_count else None)


class _Rpc:
    def __init__(self, session: PsqlSession, name: str, params: dict[str, Any]):
        if not _IDENT.match(name): raise ValueError("invalid rpc")
        self.session, self.name, self.params = session, name, params
    def execute(self):
        args = []
        for key, value in self.params.items():
            if not _IDENT.match(key): raise ValueError("invalid rpc argument")
            cast = "uuid" if key.endswith("execution_id") and not isinstance(value, dict) else "jsonb"
            args.append(f"{key} => {_literal(value, cast)}")
        self.session.invocations.append(self.name)
        data = self.session.json_query(f"select to_jsonb(public.{self.name}({','.join(args)}))")
        return _Response(data)
