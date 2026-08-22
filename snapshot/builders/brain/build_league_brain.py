from __future__ import annotations

import argparse

from auth import service_client


TARGET_TABLE = "league_brain"


def build_league_brain(league_id: str, dry_run: bool = False):
    sb = service_client()
    _require_league(sb, league_id)

    print("Requested league_id:", league_id)
    print("Requested league_team_id:", "all")

    teams_query = sb.table("team_brain").select("*")
    teams_query = teams_query.eq("league_id", league_id)

    teams = teams_query.execute().data or []

    print("Input team brain rows:", len(teams))

    contenders = [t["team_name"] for t in teams if t.get("team_direction") == "CONTEND_NOW"]
    retool = [t["team_name"] for t in teams if t.get("team_direction") == "RETOOL"]
    competing = [t["team_name"] for t in teams if t.get("team_direction") == "COMPETE_WITH_TARGETED_FIXES"]
    balanced = [t["team_name"] for t in teams if t.get("team_direction") == "BALANCED"]

    trade_fits = []

    for a in teams:
        for b in teams:
            if a["team_name"] == b["team_name"]:
                continue

            a_needs = a.get("position_needs") or []
            b_needs = b.get("position_needs") or []
            a_strengths = a.get("position_strengths") or []
            b_strengths = b.get("position_strengths") or []

            a_can_get = [p for p in a_needs if p in b_strengths]
            b_can_get = [p for p in b_needs if p in a_strengths]

            if a_can_get or b_can_get:
                trade_fits.append({
                    "team_a": a["team_name"],
                    "team_b": b["team_name"],
                    "team_a_needs_from_b": a_can_get,
                    "team_b_needs_from_a": b_can_get,
                    "fit_note": (
                        f"{a['team_name']} could target {', '.join(a_can_get) if a_can_get else 'no direct position'} "
                        f"from {b['team_name']}; "
                        f"{b['team_name']} could target {', '.join(b_can_get) if b_can_get else 'no direct position'} "
                        f"from {a['team_name']}."
                    ),
                })

    team_summaries = [
        {
            "team_name": t["team_name"],
            "direction": t.get("team_direction"),
            "needs": t.get("position_needs") or [],
            "strengths": t.get("position_strengths") or [],
            "core": t.get("core_players") or [],
            "trade_candidates": t.get("trade_candidates") or [],
        }
        for t in teams
    ]

    market_insights = []

    for t in teams:
        if t.get("position_strengths"):
            market_insights.append(
                f"{t['team_name']} has leverage at {', '.join(t.get('position_strengths') or [])}."
            )

        if t.get("position_needs"):
            market_insights.append(
                f"{t['team_name']} should look for help at {', '.join(t.get('position_needs') or [])}."
            )

        if t.get("contract_problems"):
            market_insights.append(
                f"{t['team_name']} has contract pressure around {', '.join((t.get('contract_problems') or [])[:3])}."
            )

    summary = (
        f"League Brain built with {len(teams)} teams. "
        f"Contenders: {', '.join(contenders) if contenders else 'none'}. "
        f"Competing with fixes: {', '.join(competing) if competing else 'none'}. "
        f"Retool teams: {', '.join(retool) if retool else 'none'}. "
        f"Trade fit pairs found: {len(trade_fits)}."
    )

    row = {
        "league_id": league_id,
        "league_key": str(league_id),
        "team_count": len(teams),
        "contenders": contenders,
        "competing_teams": competing,
        "balanced_teams": balanced,
        "retool_teams": retool,
        "team_summaries": team_summaries,
        "trade_fits": trade_fits,
        "market_insights": market_insights[:50],
        "summary": summary,
    }

    existing_rows = (
        sb.table(TARGET_TABLE)
        .select("id,league_id")
        .eq("league_id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    if not dry_run:
        sb.table(TARGET_TABLE).upsert(row, on_conflict="league_id").execute()

    print("Prepared league brain row:", 1)
    print("Inserted:", 0 if existing_rows else 1)
    print("Updated:", 1 if existing_rows else 0)
    print("Skipped rows:", 0)
    print("Written rows:", 0 if dry_run else 1)
    print("Dry run:", dry_run)
    print(summary)

    return {
        "input_count": len(teams),
        "prepared_count": 1,
        "skipped_count": 0,
        "written_count": 0 if dry_run else 1,
        "inserted_count": 0 if dry_run or existing_rows else 1,
        "updated_count": 0 if dry_run else (1 if existing_rows else 0),
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one scoped Coach Condor league brain row.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    build_league_brain(league_id=args.league_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
