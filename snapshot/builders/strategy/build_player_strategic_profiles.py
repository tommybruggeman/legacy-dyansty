from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auth import service_client
from snapshot.strategy.player_strategic_profile_engine import PlayerStrategicProfileEngine


TARGET_TABLE = "player_strategic_profiles"
TEAM_NAME_ALIASES = {
    "dburruel": "Dylan Burruel",
}
MISSING_ID_VALUES = {"", "none", "null", "nan"}


@dataclass(frozen=True)
class TeamResolution:
    league_team_id: str
    team_name: str


def build_player_strategic_profiles(
    league_id: str,
    league_team_id: str | None = None,
    dry_run: bool = False,
    sb: Any | None = None,
):
    sb = sb or service_client()
    engine = PlayerStrategicProfileEngine(samples=100)

    _require_league(sb, league_id)
    league_teams = _load_league_teams(sb, league_id)
    if league_team_id:
        _require_league_team(league_teams, league_team_id)

    rows = _load_player_recommendations(sb, league_id=league_id, league_team_id=league_team_id)

    print("Requested league_id:", league_id)
    print("Requested league_team_id:", league_team_id or "all")
    print(f"Loaded player rows: {len(rows)}")

    output = []
    skipped = 0

    for r in rows:
        resolution = _resolve_league_team(r, league_teams)
        if not resolution:
            skipped += 1
            print(f"Skipped unresolved team: player={r.get('player_name')} team={r.get('owner_team_name')}")
            continue

        if league_team_id and resolution.league_team_id != league_team_id:
            skipped += 1
            continue

        sleeper_id = _clean_sleeper_id(r.get("sleeper_id"))
        if not sleeper_id:
            sleeper_id = _resolve_sleeper_id(
                sb,
                player_name=r.get("player_name"),
                pos=r.get("pos"),
            )

        if not sleeper_id:
            skipped += 1
            print(
                "Skipped missing sleeper_id: "
                f"player={r.get('player_name')} team={r.get('owner_team_name')}"
            )
            continue

        player = {
            "player_name": r.get("player_name"),
            "sleeper_id": sleeper_id,
            "pos": r.get("pos"),
            "salary": r.get("salary"),
            "years": r.get("years"),
            "dynasty_asset_score": r.get("dynasty_asset_score"),
            "win_now_score": r.get("win_now_score"),
        }

        try:
            profile = engine.evaluate(player)

            output.append({
                "league_id": league_id,
                "league_team_id": resolution.league_team_id,
                "owner_team_name": resolution.team_name,
                "sleeper_id": sleeper_id,
                "player_name": profile.player_name,
                "pos": profile.pos,
                "strategic_label": profile.strategic_label,
                "action": profile.action,
                "confidence": profile.confidence,
                "median_projection": profile.median_projection,
                "opportunity_score": profile.opportunity_score,
                "volatility_label": profile.volatility_label,
                "contract_flag": profile.contract_flag,
                "asset_score": profile.asset_score,
                "win_now_score": profile.win_now_score,
                "explanation": profile.explanation,
            })

            print(f"Prepared {profile.player_name}: {profile.strategic_label}")

        except Exception as e:
            skipped += 1
            print(f"FAILED {player.get('player_name')}: {e}")

    inserted = 0
    updated = 0
    if output and not dry_run:
        inserted, updated = _write_scoped_player_rows(sb, TARGET_TABLE, output)

    print(f"Prepared strategic profiles: {len(output)}")
    print("Skipped rows:", skipped)
    print("Written rows:", 0 if dry_run else len(output))
    print("Inserted:", inserted)
    print("Updated:", updated)
    print("Dry run:", dry_run)
    print("Done.")

    return {
        "input_count": len(rows),
        "prepared_count": len(output),
        "skipped_count": skipped,
        "written_count": 0 if dry_run else len(output),
        "inserted_count": inserted,
        "updated_count": updated,
    }


def _load_player_recommendations(
    sb: Any,
    *,
    league_id: str,
    league_team_id: str | None,
) -> list[dict]:
    query = sb.table("player_recommendations").select("*")

    try:
        query = query.eq("league_id", league_id)
        if league_team_id:
            query = query.eq("league_team_id", league_team_id)
        return query.execute().data or []
    except Exception:
        rows = sb.table("player_recommendations").select("*").execute().data or []
        return rows


def _load_league_teams(sb: Any, league_id: str) -> list[dict]:
    return (
        sb.table("league_teams")
        .select("id,league_id,team_name,owner_name")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )


def _resolve_league_team(row: dict, league_teams: list[dict]) -> TeamResolution | None:
    requested_id = row.get("league_team_id")
    if requested_id:
        matches = [team for team in league_teams if str(team.get("id")) == str(requested_id)]
        if len(matches) == 1:
            team = matches[0]
            return TeamResolution(
                league_team_id=team["id"],
                team_name=team.get("team_name") or team.get("owner_name") or row.get("owner_team_name"),
            )
        return None

    raw_owner_team_name = row.get("owner_team_name")
    owner_team_name = _canonical_team_alias(raw_owner_team_name)
    if not owner_team_name:
        return None

    matches = [
        team for team in league_teams
        if owner_team_name in {
            _normalize(team.get("team_name")),
            _normalize(team.get("owner_name")),
        }
    ]

    if len(matches) != 1:
        return None

    team = matches[0]
    canonical_name = team.get("team_name") or team.get("owner_name") or row.get("owner_team_name")
    if _normalize(raw_owner_team_name) != owner_team_name:
        print(f"Used team alias: {raw_owner_team_name} -> {canonical_name}")

    return TeamResolution(
        league_team_id=team["id"],
        team_name=canonical_name,
    )


def _write_scoped_player_rows(sb: Any, table_name: str, rows: list[dict]) -> tuple[int, int]:
    inserted = 0
    updated = 0

    for row in rows:
        row["sleeper_id"] = _clean_sleeper_id(row.get("sleeper_id"))
        if not row["sleeper_id"]:
            raise RuntimeError(f"Refusing to write player row with missing sleeper_id: {row.get('player_name')}")

        existing = _find_existing_scoped_row(sb, table_name, row)

        if not existing:
            existing = _find_existing_legacy_unique_row(sb, table_name, row)

        if existing:
            query = sb.table(table_name).update(row)
            if existing.get("id"):
                query = query.eq("id", existing["id"])
            else:
                query = (
                    query
                    .eq("sleeper_id", row["sleeper_id"])
                    .eq("owner_team_name", row["owner_team_name"])
                )
            query.execute()
            updated += 1
        else:
            sb.table(table_name).insert(row).execute()
            inserted += 1

    return inserted, updated


def _find_existing_scoped_row(sb: Any, table_name: str, row: dict) -> dict | None:
    existing = (
        sb.table(table_name)
        .select("id,league_id,league_team_id,sleeper_id,owner_team_name")
        .eq("league_id", row["league_id"])
        .eq("league_team_id", row["league_team_id"])
        .eq("sleeper_id", row["sleeper_id"])
        .limit(2)
        .execute()
        .data
        or []
    )

    if len(existing) > 1:
        raise RuntimeError(
            "Duplicate scoped player rows found for "
            f"league_id={row['league_id']} league_team_id={row['league_team_id']} sleeper_id={row['sleeper_id']}."
        )

    return existing[0] if existing else None


def _find_existing_legacy_unique_row(sb: Any, table_name: str, row: dict) -> dict | None:
    existing = (
        sb.table(table_name)
        .select("id,league_id,league_team_id,sleeper_id,owner_team_name")
        .eq("sleeper_id", row["sleeper_id"])
        .eq("owner_team_name", row["owner_team_name"])
        .limit(2)
        .execute()
        .data
        or []
    )

    if len(existing) > 1:
        raise RuntimeError(
            "Duplicate legacy-key player rows found for "
            f"sleeper_id={row['sleeper_id']} owner_team_name={row['owner_team_name']}."
        )

    if not existing:
        return None

    legacy = existing[0]
    legacy_league_id = legacy.get("league_id")
    legacy_team_id = legacy.get("league_team_id")
    if legacy_league_id and str(legacy_league_id) != str(row["league_id"]):
        raise RuntimeError(
            "Refusing to overwrite player row already scoped to another league: "
            f"sleeper_id={row['sleeper_id']} owner_team_name={row['owner_team_name']}."
        )
    if legacy_team_id and str(legacy_team_id) != str(row["league_team_id"]):
        raise RuntimeError(
            "Refusing to overwrite player row already scoped to another league team: "
            f"sleeper_id={row['sleeper_id']} owner_team_name={row['owner_team_name']}."
        )

    return legacy


def _require_league(sb: Any, league_id: str) -> None:
    rows = (
        sb.table("leagues")
        .select("id")
        .eq("id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError(f"League not found: {league_id}")


def _require_league_team(league_teams: list[dict], league_team_id: str) -> None:
    if not any(str(team.get("id")) == str(league_team_id) for team in league_teams):
        raise RuntimeError("league_team_id was not found in the requested league.")


def _resolve_sleeper_id(sb: Any, *, player_name: Any, pos: Any) -> str | None:
    normalized_name = _normalize(player_name)
    normalized_pos = _normalize(pos)
    if not normalized_name:
        return None

    candidates = []
    for table_name, select_cols, id_fields, name_fields, pos_fields in [
        (
            "player_identity_map",
            "sleeper_id,canonical_player_id,player_name,pos",
            ["sleeper_id", "canonical_player_id"],
            ["player_name"],
            ["pos"],
        ),
        (
            "player_universe",
            "sleeper_id,player_name,pos",
            ["sleeper_id"],
            ["player_name"],
            ["pos"],
        ),
        (
            "player_engine_scores",
            "sleeper_id,player_name,pos",
            ["sleeper_id"],
            ["player_name"],
            ["pos"],
        ),
        (
            "player_season_stats",
            "sleeper_id,player_name,pos",
            ["sleeper_id"],
            ["player_name"],
            ["pos"],
        ),
        (
            "players",
            "sleeper_id,full_name,position",
            ["sleeper_id"],
            ["full_name"],
            ["position"],
        ),
        (
            "sleeper_players",
            "sleeper_player_id,full_name,position",
            ["sleeper_player_id"],
            ["full_name"],
            ["position"],
        ),
    ]:
        for row in _safe_select_rows(sb, table_name, select_cols):
            if not any(_normalize(row.get(field)) == normalized_name for field in name_fields):
                continue
            if normalized_pos and not any(_normalize(row.get(field)) == normalized_pos for field in pos_fields):
                continue

            for field in id_fields:
                sleeper_id = _clean_sleeper_id(row.get(field))
                if sleeper_id:
                    candidates.append(sleeper_id)

    candidates.extend(_resolve_sleeper_ids_from_local_snapshot(player_name=player_name, pos=pos))

    unique_ids = []
    for sleeper_id in candidates:
        if sleeper_id not in unique_ids:
            unique_ids.append(sleeper_id)

    if len(unique_ids) == 1:
        print(f"Resolved missing sleeper_id: player={player_name} sleeper_id={unique_ids[0]}")
        return unique_ids[0]

    if len(unique_ids) > 1:
        print(f"Skipped ambiguous sleeper_id resolution: player={player_name} candidates={', '.join(unique_ids)}")

    return None


def _resolve_sleeper_ids_from_local_snapshot(*, player_name: Any, pos: Any) -> list[str]:
    normalized_name = _normalize(player_name)
    normalized_pos = _normalize(pos)
    if not normalized_name:
        return []

    path = Path(__file__).resolve().parents[2] / "snapshots" / "latest.json"
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
    except Exception:
        return []

    matches = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            names = [
                value.get("player_name"),
                value.get("full_name"),
                value.get("name"),
            ]
            positions = [
                value.get("pos"),
                value.get("position"),
            ]
            if (
                any(_normalize(name) == normalized_name for name in names)
                and (
                    not normalized_pos
                    or any(_normalize(position) == normalized_pos for position in positions)
                )
            ):
                for field in ["sleeper_id", "canonical_player_id", "sleeper_player_id"]:
                    sleeper_id = _clean_sleeper_id(value.get(field))
                    if sleeper_id:
                        matches.append(sleeper_id)

            for child in value.values():
                visit(child)

        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return matches


def _safe_select_rows(sb: Any, table_name: str, select_cols: str) -> list[dict]:
    try:
        return sb.table(table_name).select(select_cols).execute().data or []
    except Exception:
        return []


def _clean_sleeper_id(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if text.lower() in MISSING_ID_VALUES:
        return None

    return text


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _canonical_team_alias(value: Any) -> str:
    normalized = _normalize(value)
    return _normalize(TEAM_NAME_ALIASES.get(normalized, normalized))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build scoped player strategic profiles.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--league-team-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    build_player_strategic_profiles(
        league_id=args.league_id,
        league_team_id=args.league_team_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
