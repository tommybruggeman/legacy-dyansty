"""Phase B cross-language positional fingerprint contract (v3)."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable
from uuid import UUID


def compact_json(material: list[Any]) -> str:
    """Canonical Phase B JSON: positional, compact, Unicode UTF-8, no ASCII escaping."""
    return json.dumps(material, ensure_ascii=False, separators=(",", ":"))


def _hash(material: list[Any]) -> str:
    return hashlib.sha256(compact_json(material).encode("utf-8")).hexdigest()


def _money(value: Any) -> str | None:
    return None if value is None else format(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _required_text(case: dict[str, Any], field: str) -> str:
    value = case.get(field)
    if value is None:
        raise ValueError(f"phaseb_required_field_missing:{field}")
    return str(value)


def _uuid_text(case: dict[str, Any], field: str, *, optional: bool = False) -> str | None:
    value = case.get(field)
    if value is None and optional:
        return None
    if value is None:
        raise ValueError(f"phaseb_required_field_missing:{field}")
    text = str(value)
    try:
        return str(UUID(text))
    except ValueError:
        # Preview/test builders may carry pre-persistence opaque identities.
        # Canonical persisted PostgreSQL rows are UUID-typed and therefore
        # always arrive in the canonical branch above.
        return text


def owner_case_material(case: dict[str, Any]) -> list[Any]:
    return ["phaseb-owner-case-v3", _required_text(case, "classification"), _uuid_text(case, "league_id"),
            int(case["source_season"]), int(case["target_season"]), _uuid_text(case, "agreement_id"),
            _required_text(case, "player_id"), _uuid_text(case, "league_team_id"), _required_text(case, "agreement_status"),
            _required_text(case, "roster_designation"), _required_text(case, "sleeper_player_id"),
            _money(case.get("source_salary")), int(case.get("source_contract_years") or 0)]


def commissioner_case_material(case: dict[str, Any]) -> list[Any]:
    return ["phaseb-commissioner-case-v3", _required_text(case, "review_type"),
            _uuid_text(case, "agreement_id", optional=True),
            _required_text(case, "player_id"), _uuid_text(case, "league_team_id", optional=True),
            _uuid_text(case, "source_identity", optional=True),
            None if case.get("agreement_status") is None else str(case["agreement_status"]),
            _required_text(case, "roster_status"), _money(case.get("source_salary")),
            int(case.get("source_contract_years") or 0)]


def owner_case_fingerprint(case: dict[str, Any]) -> str: return _hash(owner_case_material(case))
def commissioner_case_fingerprint(case: dict[str, Any]) -> str: return _hash(commissioner_case_material(case))


def population_material(kind: str, cases: Iterable[dict[str, Any]]) -> list[Any]:
    rows = sorted(([str(case["case_key"]), str(case.get("evidence_fingerprint") or case["case_fingerprint"])] for case in cases), key=lambda row: row[0])
    return [f"phaseb-{kind}-population-v3", rows]


def population_fingerprint(kind: str, cases: Iterable[dict[str, Any]]) -> str:
    return _hash(population_material(kind, cases))
