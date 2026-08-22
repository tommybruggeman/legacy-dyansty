from __future__ import annotations

import argparse
from collections import defaultdict

from auth import service_client


TARGET_TABLE = "team_brain"


def avg(vals):
    vals = [float(v) for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def score(p):
    return float(p.get("overall_value_score") or 0)


def build_team_brain(
    league_id: str,
    league_team_id: str | None = None,
    dry_run: bool = False,
):
    sb = service_client()
    _require_league(sb, league_id)
    if league_team_id:
        _require_league_team(sb, league_id, league_team_id)

    print("Requested league_id:", league_id)
    print("Requested league_team_id:", league_team_id or "all")

    relative_query = sb.table("league_relative_player_values").select("*")
    relative_query = relative_query.eq("league_id", league_id)
    if league_team_id:
        relative_query = relative_query.eq("league_team_id", league_team_id)

    relative_rows = relative_query.execute().data or []

    strategic_query = sb.table("player_strategic_profiles").select(
        "owner_team_name,sleeper_id,strategic_label,contract_flag,action,league_id,league_team_id"
    )
    strategic_query = strategic_query.eq("league_id", league_id)
    if league_team_id:
        strategic_query = strategic_query.eq("league_team_id", league_team_id)

    strategic_rows = strategic_query.execute().data or []

    strategic_by_key = {
        (r.get("league_team_id"), r.get("sleeper_id")): r
        for r in strategic_rows
    }

    print("Input relative values:", len(relative_rows))
    print("Input strategic profiles:", len(strategic_rows))

    teams = defaultdict(list)

    for r in relative_rows:
        key = (r.get("league_team_id"), r.get("sleeper_id"))
        s = strategic_by_key.get(key, {})
        merged = {**r, **s}
        team_key = (
            r.get("league_team_id")
            or r.get("owner_team_name")
            or "Unknown"
        )
        teams[team_key].append(merged)

    output = []
    skipped_missing_scope = 0

    required = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    preferred = {"QB": 3, "RB": 4, "WR": 5, "TE": 2}

    for team_key, players in teams.items():
        first_player = players[0] if players else {}
        team_name = first_player.get("owner_team_name") or str(team_key)
        row_league_id = league_id
        row_league_team_id = first_player.get("league_team_id")

        if not row_league_id or not row_league_team_id:
            skipped_missing_scope += 1
            print(f"Skipped {team_name}: missing league_id or league_team_id")
            continue
        ranked = sorted(players, key=score, reverse=True)
        top10 = ranked[:10]

        top10_strength = avg([p.get("overall_value_score") for p in top10])
        avg_asset = avg([p.get("asset_score") for p in players])
        avg_win_now = avg([p.get("win_now_score") for p in players])

        core = [
            p["player_name"] for p in ranked
            if p.get("league_value_tier") in ["LEAGUE_ELITE", "HIGH_END_STARTER"]
            and float(p.get("overall_percentile") or 0) >= 75
        ][:8]

        anchor_players = [
            p["player_name"] for p in ranked
            if p.get("league_value_tier") in ["LEAGUE_ELITE", "HIGH_END_STARTER", "STARTER_LEVEL"]
        ][:10]

        contract_problems = [
            p["player_name"] for p in ranked
            if p.get("contract_flag") in ["BAD_CONTRACT", "OVERPAID"]
        ][:8]

        trade_candidates = [
            p["player_name"] for p in ranked
            if p.get("strategic_label") in ["CONTRACT_PROBLEM", "REPLACEABLE_ASSET", "LOW_IMPACT_DEPTH"]
        ][:10]

        by_pos = defaultdict(list)
        for p in players:
            by_pos[p.get("pos")].append(p)

        position_strengths = []
        position_needs = []

        for pos in ["QB", "RB", "WR", "TE"]:
            group = sorted(by_pos.get(pos, []), key=score, reverse=True)

            starters = group[:required[pos]]
            depth = group[required[pos]:]

            starter_pct = avg([p.get("position_overall_percentile") for p in starters])
            depth_pct = avg([p.get("position_overall_percentile") for p in depth])

            if len(group) < required[pos] or starter_pct < 45:
                position_needs.append(pos)
            elif len(group) < preferred[pos] or depth_pct < 35:
                position_needs.append(pos)

            if len(group) >= preferred[pos] and starter_pct >= 70:
                position_strengths.append(pos)

        if len(core) >= 3 and top10_strength >= 55:
            direction = "CONTEND_NOW"
        elif len(core) >= 2 and top10_strength >= 50:
            direction = "COMPETE_WITH_TARGETED_FIXES"
        elif avg_asset >= 55 and avg_win_now < 50:
            direction = "ASCENDING_BUILD"
        elif top10_strength < 40 or len(anchor_players) < 5:
            direction = "RETOOL"
        else:
            direction = "BALANCED"

        recommendations = []

        if position_needs:
            recommendations.append(f"Prioritize upgrades/depth at: {', '.join(position_needs)}.")
        if position_strengths:
            recommendations.append(f"Use positional strength as leverage: {', '.join(position_strengths)}.")
        if contract_problems:
            recommendations.append(f"Explore exits/restructures for: {', '.join(contract_problems[:5])}.")
        if trade_candidates:
            recommendations.append(f"Market-check: {', '.join(trade_candidates[:5])}.")
        if direction == "CONTEND_NOW":
            recommendations.append("Push for weekly starter upgrades and playoff ceiling.")
        elif direction == "RETOOL":
            recommendations.append("Prioritize picks, cap flexibility, and younger assets.")

        output.append({
            "league_id": row_league_id,
            "league_team_id": row_league_team_id,
            "team_name": team_name,
            "player_count": len(players),
            "team_direction": direction,
            "avg_asset_score": round(avg_asset, 2),
            "avg_win_now_score": round(avg_win_now, 2),
            "core_players": core,
            "position_strengths": position_strengths,
            "position_needs": position_needs,
            "contract_problems": contract_problems,
            "trade_candidates": trade_candidates,
            "recommendations": recommendations,
            "summary": (
                f"{team_name} profiles as {direction}. "
                f"Top-10 league-relative strength {round(top10_strength,1)}. "
                f"Strengths: {', '.join(position_strengths) if position_strengths else 'none clear'}. "
                f"Needs: {', '.join(position_needs) if position_needs else 'none clear'}. "
                f"Core: {', '.join(core[:5]) if core else 'none flagged'}."
            ),
        })

        print(f"Prepared {team_name}: {direction} | core {len(core)} | top10 {round(top10_strength,1)}")

    existing_rows = _fetch_existing_team_brain_rows(sb)
    updates, inserts = _plan_team_brain_writes(output, existing_rows)
    inserted = len(inserts)
    updated = len(updates)

    print("Existing team_brain rows fetched:", len(existing_rows))
    print("Updates planned:", updated)
    print("Inserts planned:", inserted)

    if output and not dry_run:
        for existing_row, row in updates:
            if existing_row.get("id"):
                sb.table(TARGET_TABLE).update(row).eq("id", existing_row["id"]).execute()
            else:
                (
                    sb.table(TARGET_TABLE)
                    .update(row)
                    .eq("team_name", row["team_name"])
                    .execute()
                )

        if inserts:
            sb.table(TARGET_TABLE).insert(inserts).execute()

    action = "Prepared" if dry_run else "Wrote"
    print(f"{action} team brain rows:", len(output))
    print("Prepared rows:", len(output))
    print("Inserted:", 0 if dry_run else inserted)
    print("Updated:", 0 if dry_run else updated)
    print("Skipped rows:", skipped_missing_scope)
    print("Written rows:", 0 if dry_run else len(output))
    print("Dry run:", dry_run)

    return {
        "input_count": len(relative_rows),
        "prepared_count": len(output),
        "skipped_count": skipped_missing_scope,
        "written_count": 0 if dry_run else len(output),
        "inserted_count": 0 if dry_run else inserted,
        "updated_count": 0 if dry_run else updated,
    }


def _require_league(sb, league_id: str) -> None:
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


def _require_league_team(sb, league_id: str, league_team_id: str) -> None:
    rows = (
        sb.table("league_teams")
        .select("id")
        .eq("id", league_team_id)
        .eq("league_id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError("league_team_id was not found in the requested league.")


def _existing_team_brain_keys(sb, league_id: str) -> set[str]:
    rows = (
        sb.table(TARGET_TABLE)
        .select("league_team_id")
        .eq("league_id", league_id)
        .execute()
        .data
        or []
    )
    return {row.get("league_team_id") for row in rows if row.get("league_team_id")}


def _fetch_existing_team_brain_rows(sb) -> list[dict]:
    return (
        sb.table(TARGET_TABLE)
        .select("id,league_id,league_team_id,team_name")
        .execute()
        .data
        or []
    )


def _plan_team_brain_writes(
    output: list[dict],
    existing_rows: list[dict],
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    modern_by_scope = {}
    legacy_by_name = {}

    for existing in existing_rows:
        league_id = existing.get("league_id")
        league_team_id = existing.get("league_team_id")
        team_name = existing.get("team_name")

        if league_id and league_team_id:
            key = (str(league_id), str(league_team_id))
            if key in modern_by_scope:
                raise RuntimeError(
                    "Duplicate scoped team_brain rows found for "
                    f"league_id={league_id} league_team_id={league_team_id}."
                )
            modern_by_scope[key] = existing

        if team_name:
            if team_name in legacy_by_name:
                raise RuntimeError(f"Duplicate team_brain team_name rows found: {team_name}.")
            legacy_by_name[team_name] = existing

    updates = []
    inserts = []

    for row in output:
        scoped_key = (str(row["league_id"]), str(row["league_team_id"]))
        modern = modern_by_scope.get(scoped_key)
        if modern:
            updates.append((modern, row))
            continue

        legacy = legacy_by_name.get(row["team_name"])
        if legacy:
            legacy_league_id = legacy.get("league_id")
            legacy_team_id = legacy.get("league_team_id")
            if legacy_league_id and str(legacy_league_id) != str(row["league_id"]):
                raise RuntimeError(
                    "Refusing to overwrite team_brain row already scoped to another league: "
                    f"team_name={row['team_name']}."
                )
            if legacy_team_id and str(legacy_team_id) != str(row["league_team_id"]):
                raise RuntimeError(
                    "Refusing to overwrite team_brain row already scoped to another league team: "
                    f"team_name={row['team_name']}."
                )

            updates.append((legacy, row))
            continue

        inserts.append(row)

    return updates, inserts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build scoped Coach Condor team brain rows.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--league-team-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    build_team_brain(
        league_id=args.league_id,
        league_team_id=args.league_team_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
