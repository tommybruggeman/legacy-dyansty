from __future__ import annotations


class ContractOperationalSeasonError(RuntimeError):
    pass


def resolve_contract_operational_season(client, league_id: str) -> int:
    seasons = (
        client.table("league_seasons")
        .select("*")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )
    active = [
        x for x in seasons
        if x.get("is_active") and x.get("status") == "active"
    ]
    if len(active) != 1:
        raise ContractOperationalSeasonError(
            "Exactly one active league season is required."
        )

    by_id = {str(x["id"]): x for x in seasons}

    executions = (
        client.table("contract_transition_executions")
        .select("*")
        .eq("league_id", league_id)
        .eq("status", "validated")
        .execute()
        .data
        or []
    )

    if not executions:
        return int(active[0]["season"])

    pairs = {
        int(x["source_season"]): int(x["target_season"])
        for x in executions
    }
    if len(pairs) != len(executions):
        raise ContractOperationalSeasonError(
            "Conflicting validated contract transitions exist."
        )

    current = int(active[0]["season"])
    used = 0

    while current in pairs:
        target = pairs[current]

        if target != current + 1:
            raise ContractOperationalSeasonError(
                "Validated transitions are not sequential."
            )

        rows = [
            x for x in executions
            if int(x["source_season"]) == current
            and int(x["target_season"]) == target
        ]
        if len(rows) != 1:
            raise ContractOperationalSeasonError(
                "Ambiguous validated transition chain."
            )

        execution = rows[0]
        source = by_id.get(str(execution.get("source_league_season_id")))
        target_row = by_id.get(str(execution.get("target_league_season_id")))

        if (
            not source
            or not target_row
            or str(source.get("league_id")) != league_id
            or str(target_row.get("league_id")) != league_id
            or int(target_row["season"]) != target
        ):
            raise ContractOperationalSeasonError(
                "Validated execution has missing or cross-league season authority."
            )

        transition_is_operational = _validate_execution_state(
            client,
            league_id,
            execution,
        )
        used += 1

        # A certified reconciliation represents the repaired pre-rollover
        # authority state. The legacy validated transition remains as
        # immutable historical evidence, but it must no longer advance
        # contract operational authority to its target season.
        if not transition_is_operational:
            if used != len(executions):
                raise ContractOperationalSeasonError(
                    "Validated transitions continue beyond a certified "
                    "reconciled transition."
                )
            return current

        current = target

    if used != len(executions):
        raise ContractOperationalSeasonError(
            "Validated transitions do not form one chain from the active league season."
        )

    return current


def _validate_execution_state(client, league_id, execution):
    agreements = (
        client.table("contract_agreements")
        .select("id,status")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )
    seasons = (
        client.table("contract_seasons")
        .select("season,obligation_status")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )

    source = int(execution["source_season"])
    target = int(execution["target_season"])

    actual = {
        "agreements": len(agreements),
        "active_agreements": sum(
            x.get("status") == "active" for x in agreements
        ),
        "expired_agreements": sum(
            x.get("status") == "expired" for x in agreements
        ),
        f"satisfied_{source}": sum(
            int(x["season"]) == source
            and x.get("obligation_status") == "satisfied"
            for x in seasons
        ),
        f"active_{target}": sum(
            int(x["season"]) == target
            and x.get("obligation_status") == "active"
            for x in seasons
        ),
        f"scheduled_{target + 1}": sum(
            int(x["season"]) == target + 1
            and x.get("obligation_status") == "scheduled"
            for x in seasons
        ),
    }

    reconciliations = (
        client.table("contract_transition_reconciliations")
        .select("*")
        .eq("league_id", league_id)
        .eq("legacy_transition_id", execution["id"])
        .execute()
        .data
        or []
    )

    certified = [
        row for row in reconciliations
        if row.get("reconciliation_status") == "certified"
    ]

    if len(certified) > 1:
        raise ContractOperationalSeasonError(
            "Multiple certified reconciliations exist for validated transition."
        )

    if certified:
        reconciliation = certified[0]

        if (
            int(reconciliation.get("source_season") or 0) != source
            or int(reconciliation.get("target_season") or 0) != target
            or str(reconciliation.get("legacy_transition_id"))
            != str(execution.get("id"))
        ):
            raise ContractOperationalSeasonError(
                "Certified reconciliation transition provenance mismatch."
            )

        counts = reconciliation.get("actual_counts") or {}

        reconciliation_expected = {
            "active_agreements": counts.get("agreements_active"),
            "expired_agreements": counts.get("agreements_expired"),
            f"active_{target}": counts.get("target_active"),
            f"scheduled_{target + 1}": counts.get(
                f"season_{target + 1}_scheduled"
            ),
        }

        for key, expected in reconciliation_expected.items():
            if expected is None:
                continue
            if int(expected) != actual[key]:
                raise ContractOperationalSeasonError(
                    f"Certified reconciliation persisted state mismatch: {key}."
                )

        return False

    result = (execution.get("result") or {}).get("persisted") or {}

    for key, value in actual.items():
        if key in result and int(result[key]) != value:
            raise ContractOperationalSeasonError(
                f"Validated execution persisted state mismatch: {key}."
            )

    return True
