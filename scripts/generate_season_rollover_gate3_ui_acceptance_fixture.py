#!/usr/bin/env python3
"""Generate the disposable-only Gate 3 UI acceptance fixture.

The certified domain factory remains byte-for-byte frozen.  This wrapper adapts
its legacy owner role to the live canonical ``member`` role and equips exactly
two synthetic users for normal Supabase email/password authentication.
Passwords are generated into a mode-0600 file under /tmp and are referenced by
psql variables; plaintext credentials never enter the generated SQL or repo.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
from uuid import uuid5

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures.season_rollover_domain_factory import (
    NAMESPACE,
    SeasonRolloverDomainFactory,
)


DEFAULT_FIXTURE_PATH = Path("/tmp/legacy_gate3_ui_acceptance_fixture.sql")
DEFAULT_CREDENTIALS_PATH = Path("/tmp/legacy_gate3_ui_acceptance_credentials.env")
DEFAULT_LABEL = "gate3-ui-acceptance"
COMMISSIONER_EMAIL = "gate3-commissioner@rollover-ui-acceptance.invalid"
OWNER_EMAIL = "gate3-owner-team-1@rollover-ui-acceptance.invalid"
PASSWORD_IMPORT_PREAMBLE = """\\getenv gate3_commissioner_password GATE3_COMMISSIONER_PASSWORD
\\if :{?gate3_commissioner_password}
\\else
\\echo 'GATE3_COMMISSIONER_PASSWORD is required; fixture not started'
\\quit 3
\\endif
\\getenv gate3_owner_password GATE3_OWNER_PASSWORD
\\if :{?gate3_owner_password}
\\else
\\echo 'GATE3_OWNER_PASSWORD is required; fixture not started'
\\quit 3
\\endif
"""

_WRITE = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from)\s+((?:auth|public)\.[a-z0-9_]+)",
    re.IGNORECASE,
)


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _login_user_sql(user_id: str, email: str, password_variable: str) -> str:
    return (
        "insert into auth.users("
        "id,instance_id,aud,role,email,encrypted_password,email_confirmed_at,"
        "confirmation_token,recovery_token,email_change,email_change_token_new,"
        "raw_app_meta_data,raw_user_meta_data,created_at,updated_at"
        ") values("
        f"{_quote(user_id)},'00000000-0000-0000-0000-000000000000',"
        f"'authenticated','authenticated',{_quote(email)},"
        f"extensions.crypt(:'{password_variable}',extensions.gen_salt('bf')),"
        "clock_timestamp(),'','','','',"
        "'{\"provider\":\"email\",\"providers\":[\"email\"]}',"
        "'{}',clock_timestamp(),clock_timestamp())"
    )


def _identity_sql(namespace: str, user_id: str, email: str, label: str) -> str:
    identity_id = str(uuid5(NAMESPACE, f"{namespace}:auth-identity:{label}"))
    identity_data = (
        "jsonb_build_object('sub',"
        f"{_quote(user_id)},'email',{_quote(email)},'email_verified',true)"
    )
    return (
        "insert into auth.identities("
        "id,user_id,provider_id,identity_data,provider,last_sign_in_at,created_at,updated_at"
        ") values("
        f"{_quote(identity_id)},{_quote(user_id)},{_quote(user_id)},"
        f"{identity_data},'email',null,clock_timestamp(),clock_timestamp())"
    )


def build_fixture_sql(label: str = DEFAULT_LABEL) -> tuple[str, SeasonRolloverDomainFactory]:
    factory = SeasonRolloverDomainFactory(label)
    base_sql = factory.bootstrap_sql()
    factory.audit_bootstrap_sql(base_sql)
    identity = factory.identity

    commissioner_placeholder = re.compile(
        rf"insert into auth\.users\([^;]+values\('{re.escape(identity.commissioner_id)}'[^;]+\)"
    )
    owner_placeholder = re.compile(
        rf"insert into auth\.users\([^;]+values\('{re.escape(identity.owner_id)}'[^;]+\)"
    )
    commissioner_replacement = ";\n".join((
        _login_user_sql(identity.commissioner_id, COMMISSIONER_EMAIL, "gate3_commissioner_password"),
        _identity_sql(factory.namespace, identity.commissioner_id, COMMISSIONER_EMAIL, "commissioner"),
    ))
    owner_replacement = ";\n".join((
        _login_user_sql(identity.owner_id, OWNER_EMAIL, "gate3_owner_password"),
        _identity_sql(factory.namespace, identity.owner_id, OWNER_EMAIL, "owner-team-1"),
    ))
    sql, commissioner_count = commissioner_placeholder.subn(commissioner_replacement, base_sql)
    sql, owner_count = owner_placeholder.subn(owner_replacement, sql)
    if (commissioner_count, owner_count) != (1, 1):
        raise AssertionError("expected exactly one commissioner and Team 1 owner auth placeholder")

    writes = set(x.lower() for x in _WRITE.findall(sql))
    allowed = set(factory.audit_bootstrap_sql(base_sql)) | {"auth.identities"}
    if writes != allowed:
        raise AssertionError(f"Gate 3 fixture write surface mismatch: actual={sorted(writes)} expected={sorted(allowed)}")
    if "'owner'" in "\n".join(
        line for line in sql.splitlines()
        if line.lower().startswith("insert into public.league_memberships")
    ):
        raise AssertionError("non-canonical owner membership role survived generation")
    # psql does not automatically turn shell environment variables into psql
    # variables. Import both explicitly and fail before BEGIN when either local
    # disposable credential is unavailable.
    sql = PASSWORD_IMPORT_PREAMBLE + sql
    return sql, factory


def write_credentials(path: Path) -> dict[str, str]:
    values = {
        "GATE3_COMMISSIONER_EMAIL": COMMISSIONER_EMAIL,
        "GATE3_COMMISSIONER_PASSWORD": secrets.token_urlsafe(24),
        "GATE3_OWNER_EMAIL": OWNER_EMAIL,
        "GATE3_OWNER_PASSWORD": secrets.token_urlsafe(24),
    }
    # ``export`` makes a normal ``source credentials.env`` sufficient for the
    # generated fixture's psql \getenv preamble.
    content = "\n".join(f"export {key}={shlex.quote(value)}" for key, value in values.items()) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--credentials-output", type=Path, default=DEFAULT_CREDENTIALS_PATH)
    args = parser.parse_args()
    sql, factory = build_fixture_sql(args.label)
    credentials = write_credentials(args.credentials_output)
    args.output.write_text(sql)
    print(f"fixture={args.output}")
    print(f"credentials={args.credentials_output} mode={oct(args.credentials_output.stat().st_mode & 0o777)}")
    print(f"league_id={factory.identity.league_id}")
    print(f"commissioner_id={factory.identity.commissioner_id} email={credentials['GATE3_COMMISSIONER_EMAIL']}")
    print(f"owner_id={factory.identity.owner_id} team_id={factory.identity.team_ids[0]} email={credentials['GATE3_OWNER_EMAIL']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
