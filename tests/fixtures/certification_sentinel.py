from __future__ import annotations

import os
import re

ENVIRONMENT_VARIABLES = (
    "PHASE3B5H_EXPECTED_ENVIRONMENT_NAME",
    "PHASE3B5H_EXPECTED_ENVIRONMENT_TYPE",
    "PHASE3B5H_EXPECTED_PARENT_PROJECT",
)
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def expected_sentinel(default_name: str) -> tuple[str, str, str]:
    defaults = (default_name, "disposable_test", "Legacy-Dynasty")
    values = tuple(os.getenv(name, default).strip()
                   for name, default in zip(ENVIRONMENT_VARIABLES, defaults, strict=True))
    if any(not value or not _SAFE.fullmatch(value) for value in values):
        raise RuntimeError("expected disposable sentinel values are missing or invalid")
    return values


def count_sql(expected: tuple[str, str, str]) -> str:
    name, kind, parent = expected
    return ("select count(*)::text||':'||count(*) filter(where singleton and "
            f"environment_name='{name}' and environment_type='{kind}' and "
            f"parent_project='{parent}')::text from public.environment_identity")
