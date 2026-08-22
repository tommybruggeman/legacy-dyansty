#!/usr/bin/env python3
"""Generate and verify, but never activate, the post-Phase-F manifest."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "docs/certification/season_rollover_2025_2026_certified_v1.json"
BASE = ROOT / "docs/certification/season_rollover_post_phase_e_proposed_v1.json"
OUTPUT = ROOT / "docs/certification/season_rollover_post_phase_f_proposed_v1.json"
EXPECTED_BASE_COUNT = 190
EXPECTED_FINAL_COUNT = 211
ADDITIONS = frozenset({
    "Admin Commissioner/_80_Transactions.py", "Admin Commissioner/_84_Draft_Picks.py",
    "auth.py", "components/sidebar_nav.py", "home.py", "pages/00_league_Setup.py",
    "pages/02_My_Team.py", "pages/02_Weekly_Matchups.py", "pages/03_Teams.py",
    "pages/04_Commit_Contracts.py", "pages/90_Settings.py", "pages/_82_Trades.py",
    "services/app_context.py", "services/sync_sleeper_teams.py",
    "services/sync_sleeper_transactions_to_ledger.py",
    "scripts/generate_season_rollover_gate3_ui_acceptance_fixture.py",
    "scripts/prepare_phase_f_clean_branch.py",
    "supabase/sql/phase_f_final_certification_sentinel.sql",
    "supabase/migrations/20261018_phaseA_final_fingerprint_contract_reassertion.sql",
    "supabase/tests/20261018_phaseA_final_fingerprint_contract_reassertion_test.sql",
    "tests/fixtures/certification_sentinel.py",
})
FORBIDDEN_EXACT = frozenset({
    ".gitignore", "requirements.txt", "_cleanup_hold/tests/debug_roster.py",
    "pages/02_My_Team_REVERT_READY.py",
})
FORBIDDEN_PARTS = frozenset({"archive", ".streamlit", ".env.gate3-production-hold"})
FORBIDDEN_SUFFIXES = frozenset({".zip", ".pdf"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_payload() -> dict:
    historical = json.loads(HISTORICAL.read_text(encoding="utf-8"))
    base = json.loads(BASE.read_text(encoding="utf-8"))
    base_paths = [entry["path"] for entry in base["files"]]
    if len(base_paths) != EXPECTED_BASE_COUNT or len(set(base_paths)) != EXPECTED_BASE_COUNT:
        raise RuntimeError("post-Phase-E base is not exactly 190 unique paths")
    paths = set(base_paths) | set(ADDITIONS)
    if len(paths) != EXPECTED_FINAL_COUNT:
        raise RuntimeError(f"final surface is not exactly {EXPECTED_FINAL_COUNT} paths: {len(paths)}")
    missing = sorted(path for path in paths if not (ROOT / path).is_file())
    if missing:
        raise RuntimeError("manifest inputs missing: " + ", ".join(missing))
    forbidden = sorted(path for path in paths if path in FORBIDDEN_EXACT
        or any(part in FORBIDDEN_PARTS for part in Path(path).parts)
        or Path(path).suffix.lower() in FORBIDDEN_SUFFIXES
        or Path(path).name.startswith(".env") or "secret" in Path(path).name.lower())
    if forbidden:
        raise RuntimeError("forbidden manifest inputs: " + ", ".join(forbidden))
    if len(historical.get("files", ())) != 119:
        raise RuntimeError("historical manifest is not the immutable 119-file baseline")
    return {"files": [{"path": path, "sha256": sha256(ROOT / path)} for path in sorted(paths)],
        "historical_manifest": str(HISTORICAL.relative_to(ROOT)),
        "predecessor": str(BASE.relative_to(ROOT)), "schema_version": 3,
        "sha256_method": "SHA-256 over exact file bytes", "status": "proposed_unactivated"}


def verify(payload: dict) -> None:
    entries = payload.get("files") or []
    paths = [entry.get("path") for entry in entries]
    if len(paths) != EXPECTED_FINAL_COUNT or len(set(paths)) != EXPECTED_FINAL_COUNT:
        raise RuntimeError("proposed manifest paths are missing or duplicated")
    for entry in entries:
        path = ROOT / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise RuntimeError(f"proposed manifest verification failed: {entry['path']}")
    if not ADDITIONS.issubset(paths):
        raise RuntimeError("required Phase F/Gate 3 support dependency omitted")


def main() -> None:
    payload = build_payload()
    verify(payload)
    rendered = encoded(payload)
    OUTPUT.write_bytes(rendered)
    verify(json.loads(OUTPUT.read_text(encoding="utf-8")))
    if encoded(build_payload()) != rendered:
        raise RuntimeError("manifest generation is not deterministic")
    print(json.dumps({"deterministic": True, "historical_sha256": sha256(HISTORICAL),
        "manifest_sha256": sha256(OUTPUT), "output": str(OUTPUT.relative_to(ROOT)),
        "proposed_file_count": len(payload["files"]), "verified": True}, sort_keys=True))


if __name__ == "__main__":
    main()
