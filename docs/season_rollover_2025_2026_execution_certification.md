# 2025 → 2026 Season Rollover Execution Certification

## Certified result

This document freezes the hosted execution certification identified as `season-rollover-2025-2026-certified-v1`. The disposable hosted run used fixture `unified-hosted-offroster-v8` and completed 31 of 31 operations with 31 immutable completed-operation evidence rows. Its final state was `executed_unpublished`; `publication_performed=false`, `live_external_call_performed=false`, and there was no remaining blocker. Operation 15 recorded two intentional roster exclusions.

The certification was reported in the repository work context on 2026-08-11. At freeze time the repository was on branch `main` at HEAD `56a2c387f12c3bd3a152258bd7ff490ac252e76d` (commit time 2026-06-02T22:06:24-06:00, “Remove owner matching tool”). The certified rollover implementation was not represented by that commit: its files were modified or untracked in the working tree. The accompanying content-addressed manifest is therefore the authoritative pre-commit identity of the certified baseline. The recommended annotated tag should be created only after committing these exact contents.

## Architectural invariant

Ordinary continuing contracts require matching immutable source ownership and create unpublished target roster assignments. An approved `preserve_active_liability` case is contract/cap liability rather than roster authority: immutable evidence must identify `active_off_roster_liability`, the approved resolution, matching agreement/player/team, completed validation, and valid fingerprints. It remains an active contract and cap obligation but creates neither a source nor target roster assignment. Missing, unapproved, or mismatched evidence fails closed.

The certified corrections also make synthetic rookie fields internally consistent, hash empty dead-cap evidence as canonical `[]`, and include approved preserved off-roster liabilities in prepared cap calculations without fabricating roster authority.

## Certified boundary

The machine-readable manifest at `docs/certification/season_rollover_2025_2026_certified_v1.json` records an actual SHA-256 for every file in these categories:

- execution migrations: schema, snapshots, review, preparation, simulation, approval, execution dispatch, operations 1–31, trusted-boundary fixes, and the three final corrective migrations;
- engine/control code: canonical history, rollover state/control, authority preparation, dry run, plan generation, and roster reconciliation paths;
- catalogs: the operation catalog and post-execution validation registry used to define and validate the deterministic plan;
- certification evidence: the hosted runner, deterministic fixture, database client, Operation 15 integration suite, and critical local regression tests;
- freeze artifacts: this document and the read-only drift checker.

Publication migrations dated 20260917 through 20260921, publication services, UI code, and operations 32–36 are deliberately outside the certified implementation boundary. Execution certification **does not certify publication**. It certifies only the transition through operation 31 to `executed_unpublished`; the target remains unpublished and no external publication call is permitted.

## Canonical execution path

The path is the authenticated rollover control service plus immutable database RPCs for policy approval, execution creation, window/review resolution, authority preparation, simulation and canonical plan materialization, plan approval, and `execute_rollover_plan_authenticated`. The latter dispatches the catalogued operations 1–31 and finalizes `executed_unpublished`. The hosted runner is `tests/season_rollover_hosted_integration/run_unified_hosted_rollover.py`; its deterministic domain source is `tests/fixtures/season_rollover_domain_factory.py`. The complete exact file inventory is in the JSON manifest rather than duplicated here.

## Drift and recertification

Run `python3 scripts/verify_season_rollover_certification.py` from the repository root. It reads files only, performs no database or network calls, prints PASS or FAIL, identifies missing/changed files, and exits nonzero on drift.

Any change to a manifest-listed file invalidates this content baseline and requires review and re-certification before its hashes are updated. Adding a new execution migration, handler, RPC, operation implementation, or trusted-boundary dependency also requires expanding the manifest and re-certifying even though an additive file cannot be detected by a fixed per-file list.
