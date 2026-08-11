# Season Rollover Gate 2B Rehearsal Certification

## Scope

This certification covers the production-derived rehearsal of the Gate 2B season rollover remediation set.

The rehearsal was performed on a temporary Supabase preview branch created from the production branch and deleted after certification.

## Pre-remediation state

The production-derived rehearsal branch reproduced the expected rollover execution parity failures plus one additional authenticated direct-write security failure.

Observed security issue:

- `authenticated` retained `INSERT`, `UPDATE`, `DELETE`, and `TRUNCATE` privileges on rollover lifecycle tables.

## Remediation set

The following migrations were applied in order:

1. `supabase/migrations/20260922_season_rollover_trusted_boundaries.sql`
2. `supabase/migrations/20261001_phase3b8a_preserved_off_roster_liability.sql`
3. `supabase/migrations/20261002_phase3b10b_empty_dead_cap_evidence.sql`
4. `supabase/migrations/20261003_phase3b10b_preserved_liability_caps.sql`
5. `supabase/migrations/20261004_rollover_authenticated_direct_write_hardening.sql`

## Final migration hashes

- `20260922_season_rollover_trusted_boundaries.sql`
  - `bc506fbf79ed01d3db2f3aecab796ec829d9dca2da38cd51ccfb28e55e7d398b`
- `20261001_phase3b8a_preserved_off_roster_liability.sql`
  - `512afbed528d739092cb10e58076dc593320c6c722a227b1bffd8684ddb0fe22`
- `20261002_phase3b10b_empty_dead_cap_evidence.sql`
  - `d6b9632aa241c6885c1fef068d2e4d18a76e675c0f08c549faed7e0f9e09c180`
- `20261003_phase3b10b_preserved_liability_caps.sql`
  - `d3bde83229f3b0964a4071c4f7c344166d615ff1754e358edfeba0fb6f5ebfa9`
- `20261004_rollover_authenticated_direct_write_hardening.sql`
  - `fcb9fe48e8770d1e9997b00ac8d1bdc0de01c9451d185210ba609b7cabcc19c7`

## Rehearsal result

The five-migration set was applied successfully.

The full five-migration set was then replayed successfully against the already-remediated rehearsal state.

Final execution parity result after replay:

- PASS: 203
- FAIL: 0
- WARN: 1
- NOT CHECKED: 1

Key invariants:

- execution handlers 1-31: `expected=31 actual=31`
- publication operations 32-36 remained excluded from execution certification
- authenticated direct writes: `none`
- final result: `PASS: certified execution parity`

The remaining warning was migration-history metadata being unavailable. Schema and function outcomes were authoritative.

Production-state verification was intentionally not run because no production league ID was supplied.

## Secondary disposable validation

The existing `phase3b5h-testing` disposable branch was also used to validate the new authenticated direct-write hardening migration.

Before `20261004`, the branch contained 44 authenticated direct-write grants across 11 rollover tables.

After applying `20261004`, the branch reached the same final execution parity result:

- PASS: 203
- FAIL: 0
- authenticated direct writes: `none`

The disposable branch was deleted after validation.

## Boundary

This certification does not execute or authorize a production season rollover.

Publication remains uncertified.
