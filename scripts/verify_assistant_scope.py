from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from auth import service_client


@dataclass
class VerificationReport:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def add_failure(self, message: str) -> None:
        self.failures.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_detail(self, message: str) -> None:
        self.details.append(message)


def verify_assistant_scope(
    *,
    league_id: str,
    league_team_id: str | None = None,
    user_id: str | None = None,
    sb: Any | None = None,
) -> VerificationReport:
    sb = sb or service_client()
    report = VerificationReport()

    _verify_memory_columns(sb, report)
    if not report.ok:
        return report

    _verify_team_membership(sb, report, league_id=league_id, league_team_id=league_team_id)
    _verify_memory_rows(sb, report, league_id=league_id, league_team_id=league_team_id, user_id=user_id)
    _verify_league_brain(sb, report, league_id=league_id)
    _verify_team_brain(sb, report, league_id=league_id, league_team_id=league_team_id)

    return report


def print_report(report: VerificationReport) -> None:
    for detail in report.details:
        print(f"DETAIL: {detail}")
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for failure in report.failures:
        print(f"FAILURE: {failure}")

    print("RESULT:", "PASS" if report.ok else "FAIL")


def _verify_memory_columns(sb: Any, report: VerificationReport) -> None:
    try:
        (
            sb.table("gm_user_memory")
            .select("user_id,league_id,league_team_id,team_name")
            .limit(1)
            .execute()
        )
        report.add_detail("gm_user_memory scope columns are queryable.")
    except Exception:
        report.add_failure("gm_user_memory is missing one or more required scope columns.")


def _verify_team_membership(
    sb: Any,
    report: VerificationReport,
    *,
    league_id: str,
    league_team_id: str | None,
) -> None:
    if not league_team_id:
        report.add_warning("No league_team_id was supplied; team membership ownership check skipped.")
        return

    rows = _rows(
        sb.table("league_teams")
        .select("id,league_id,team_name,owner_name")
        .eq("id", league_team_id)
        .eq("league_id", league_id)
        .limit(1)
    )
    if not rows:
        report.add_failure("Requested league_team_id does not belong to the requested league.")
        return

    team_name = rows[0].get("team_name") or rows[0].get("owner_name") or "unknown"
    report.add_detail(f"Requested team belongs to league: league_team_id={league_team_id}, team_name={team_name}")


def _verify_memory_rows(
    sb: Any,
    report: VerificationReport,
    *,
    league_id: str,
    league_team_id: str | None,
    user_id: str | None,
) -> None:
    rows = _rows(sb.table("gm_user_memory").select("user_id,league_id,league_team_id,team_name"))
    modern = [row for row in rows if row.get("user_id") and row.get("league_id") and row.get("league_team_id")]
    legacy = [row for row in rows if not row.get("league_id") or not row.get("league_team_id")]

    duplicate_keys = [
        key for key, count in Counter(
            (row.get("user_id"), row.get("league_id"), row.get("league_team_id"))
            for row in modern
        ).items()
        if count > 1
    ]

    if duplicate_keys:
        report.add_failure(f"Duplicate modern gm_user_memory scopes found: {len(duplicate_keys)}")
    else:
        report.add_detail("No duplicate modern gm_user_memory scopes found.")

    scoped = [row for row in modern if row.get("league_id") == league_id]
    if league_team_id:
        scoped = [row for row in scoped if row.get("league_team_id") == league_team_id]
    if user_id:
        scoped = [row for row in scoped if row.get("user_id") == user_id]

    report.add_detail(f"Scoped gm_user_memory rows matching request: {len(scoped)}")
    if legacy:
        report.add_warning(f"Legacy unscoped gm_user_memory rows present: {len(legacy)}")


def _verify_league_brain(sb: Any, report: VerificationReport, *, league_id: str) -> None:
    rows = _rows(sb.table("league_brain").select("league_id,league_key,team_count").eq("league_id", league_id))
    if len(rows) > 1:
        report.add_failure("Multiple league_brain rows found for the requested league_id.")
    elif rows:
        report.add_detail("Scoped league_brain row found for requested league.")
    else:
        report.add_warning("No scoped league_brain row found for requested league.")

    all_rows = _rows(sb.table("league_brain").select("league_id,league_key"))
    legacy = [row for row in all_rows if not row.get("league_id")]
    if legacy:
        report.add_warning(f"Legacy unscoped league_brain rows present: {len(legacy)}")


def _verify_team_brain(
    sb: Any,
    report: VerificationReport,
    *,
    league_id: str,
    league_team_id: str | None,
) -> None:
    query = sb.table("team_brain").select("league_id,league_team_id,team_name").eq("league_id", league_id)
    if league_team_id:
        query = query.eq("league_team_id", league_team_id)

    rows = _rows(query)
    duplicate_keys = [
        key for key, count in Counter(
            (row.get("league_id"), row.get("league_team_id"))
            for row in rows
            if row.get("league_id") and row.get("league_team_id")
        ).items()
        if count > 1
    ]

    if duplicate_keys:
        report.add_failure(f"Duplicate scoped team_brain rows found: {len(duplicate_keys)}")
    else:
        report.add_detail(f"Scoped team_brain rows matching request: {len(rows)}")

    if league_team_id and len(rows) > 1:
        report.add_failure("Requested league/team scope returned more than one team_brain row.")

    if rows:
        team_names = {row.get("team_name") for row in rows if row.get("team_name")}
        collision_rows = [
            row for team_name in team_names
            for row in _rows(sb.table("team_brain").select("league_id,league_team_id,team_name").eq("team_name", team_name))
            if row.get("league_id") != league_id
        ]
        if collision_rows:
            report.add_detail(f"Same team name exists outside this league but scoped query stayed isolated: {len(collision_rows)}")

    all_rows = _rows(sb.table("team_brain").select("league_id,league_team_id,team_name"))
    legacy = [row for row in all_rows if not row.get("league_id") or not row.get("league_team_id")]
    if legacy:
        report.add_warning(f"Legacy unscoped team_brain rows present: {len(legacy)}")


def _rows(query: Any) -> list[dict]:
    return query.execute().data or []


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Coach Condor assistant scope isolation.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--league-team-id")
    parser.add_argument("--user-id")
    args = parser.parse_args(argv)

    report = verify_assistant_scope(
        league_id=args.league_id,
        league_team_id=args.league_team_id,
        user_id=args.user_id,
    )
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
