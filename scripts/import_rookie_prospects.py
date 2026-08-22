from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth import service_client
from services.rookie_prospects import build_completed_draft_import_plan, build_rookie_class_diagnostics
from services.sleeper_sync import fetch_sleeper_players


PLAYER_UNIVERSE_COLUMNS = frozenset({
    "sleeper_id", "canonical_player_id", "gsis_id", "player_name", "search_name", "pos", "nfl_team",
    "nfl_status", "active", "current_owner", "roster_status", "has_contract", "salary", "years",
    "contract_total_years", "is_rookie_contract", "market_pool", "estimated_market_value", "recommended_years",
    "dynasty_asset_score", "future_projection_score", "rookie_asset_score", "market_consensus_score",
    "nfl_intelligence_score", "nfl_intelligence_grade", "nfl_intelligence_flags", "contract_efficiency_score",
    "contract_efficiency_grade", "position_contract_rank", "position_contract_percentile", "expected_ppg",
    "historical_ppg", "latest_season", "latest_week", "latest_week_points", "latest_week_ppr", "season_ppg",
    "season_games", "player_universe_summary", "updated_at", "rookie_class_year", "draft_year", "draft_round",
    "draft_pick", "years_exp", "college",
})

REPORT_ONLY_COLUMNS = frozenset({
    "source", "source_updated_at", "match_method", "confidence", "warnings", "proposed_action",
})


def project_player_universe_upserts(records: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Strip known report metadata and reject every other unknown database key."""
    unexpected = sorted({key for row in records for key in row if key not in PLAYER_UNIVERSE_COLUMNS | REPORT_ONLY_COLUMNS})
    if unexpected:
        raise ValueError(
            "Unknown player_universe columns; no writes were performed: " + ", ".join(unexpected)
        )
    payload = [{key: value for key, value in row.items() if key in PLAYER_UNIVERSE_COLUMNS} for row in records]
    invalid_payload = sorted({key for row in payload for key in row if key not in PLAYER_UNIVERSE_COLUMNS})
    if invalid_payload:
        raise ValueError(
            "Unknown player_universe columns after projection; no writes were performed: " + ", ".join(invalid_payload)
        )
    return payload


def load_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text())
        if not isinstance(payload, list):
            raise ValueError("Prospect JSON must contain a list of records.")
        return [dict(row) for row in payload]
    if path.suffix.lower() == ".csv":
        with path.open(newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise ValueError("Prospect input must be CSV or JSON.")


def import_rookie_prospects(
    path: Path,
    *,
    apply: bool = False,
    aliases_path: Path | None = None,
    sleeper_data_path: Path | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    incoming = load_records(path)
    aliases = json.loads(aliases_path.read_text()) if aliases_path else {}
    sb = client or service_client()
    existing = sb.table("player_universe").select("*").execute().data or []
    sleeper_players = json.loads(sleeper_data_path.read_text()) if sleeper_data_path else fetch_sleeper_players()
    plan = build_completed_draft_import_plan(incoming, sleeper_players, existing, source_aliases=aliases)

    if apply and not plan.safe_to_apply:
        raise RuntimeError("Draft import validation failed; no writes were performed: " + "; ".join(plan.errors))
    persistence_upserts = project_player_universe_upserts([dict(row) for row in plan.upserts])

    if apply:
        if persistence_upserts:
            sb.table("player_universe").upsert(persistence_upserts, on_conflict="sleeper_id").execute()
        for synthetic_id in plan.synthetic_ids_to_remove:
            sb.table("player_universe").delete().eq("sleeper_id", synthetic_id).execute()

    effective = {
        str(row.get("sleeper_id")): dict(row)
        for row in existing
        if row.get("sleeper_id") and str(row.get("sleeper_id")) not in plan.synthetic_ids_to_remove
    }
    effective.update({str(row["sleeper_id"]): dict(row) for row in plan.upserts})
    return {
        "dry_run": not apply,
        "current_diagnostics": [report.__dict__ for report in build_rookie_class_diagnostics(existing)],
        "official_drafted_fantasy_players": plan.official_count,
        "matched_canonical": plan.matched_count,
        "synthetic_fallback": plan.synthetic_count,
        "ambiguous": plan.ambiguous_count,
        "missing": plan.missing_count,
        "inserted": plan.inserted_count,
        "updated": plan.updated_count,
        "merged": plan.merged_count,
        "unchanged": plan.unchanged_count,
        "upserts": len(plan.upserts),
        "persistence_columns": sorted({key for row in persistence_upserts for key in row}),
        "synthetic_ids_to_remove": list(plan.synthetic_ids_to_remove),
        "validation_errors": list(plan.errors),
        "safe_to_apply": plan.safe_to_apply,
        "matching_table": [report.__dict__ for report in plan.reports],
        "diagnostics": [report.__dict__ for report in build_rookie_class_diagnostics(list(effective.values()))],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or apply a deterministic rookie prospect import.")
    parser.add_argument("input", type=Path, help="CSV or JSON prospect records")
    parser.add_argument("--aliases", type=Path, help="Optional JSON map of source IDs to canonical Sleeper IDs")
    parser.add_argument("--sleeper-data", type=Path, help="Optional cached Sleeper NFL player JSON; otherwise fetch current data")
    parser.add_argument("--apply", action="store_true", help="Apply player_universe upserts and completed identity merges")
    args = parser.parse_args()
    print(json.dumps(import_rookie_prospects(args.input, apply=args.apply, aliases_path=args.aliases, sleeper_data_path=args.sleeper_data), indent=2, default=str))


if __name__ == "__main__":
    main()
