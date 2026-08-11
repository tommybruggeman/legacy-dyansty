from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from types import SimpleNamespace
from typing import Any
from uuid import uuid4


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
        self._start()
        self._configure()

    def _start(self):
        self.process = subprocess.Popen(
            ["/opt/homebrew/opt/postgresql@16/bin/psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=self._env,
        )

    def _configure(self):
        claims = {"sub": self.actor or "", "role": self.role}
        self.command("select set_config('request.jwt.claims', " + _literal(json.dumps(claims)) + ", false)")
        self.command("select set_config('request.jwt.claim.sub', " + _literal(self.actor or "") + ", false)")
        self.command("select set_config('request.jwt.claim.role', " + _literal(self.role) + ", false)")

    def close(self):
        if self.process.poll() is None:
            assert self.process.stdin
            try:
                self.process.stdin.write("\\q\n"); self.process.stdin.flush()
                self.process.wait(timeout=10)
            except BrokenPipeError:
                pass

    def command(self, sql: str) -> list[str]:
        marker = "__CODEX_END_" + uuid4().hex + "__"
        assert self.process.stdin and self.process.stdout
        try:
            self.process.stdin.write(sql.rstrip().rstrip(";") + ";\n\\echo " + marker + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            remainder = self.process.stdout.read()
            if not remainder and self.reconnect_count < 8:
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
    def select(self, columns: str = "*"):
        if columns != "*" and not all(_IDENT.match(x.strip()) for x in columns.split(",")):
            raise ValueError("invalid columns")
        self.columns = columns; return self
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
    def execute(self):
        where = " where " + " and ".join(self.filters) if self.filters else ""
        sql = f"select coalesce(jsonb_agg(to_jsonb(q)),'[]'::jsonb) from (select {self.columns} from public.{self.name}{where}) q"
        return _Response(self.session.json_query(sql))


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
