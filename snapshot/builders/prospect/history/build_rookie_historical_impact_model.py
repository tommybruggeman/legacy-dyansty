from __future__ import annotations

from auth import service_client


TARGET_TABLE = "rookie_historical_impact_model"
FANTASY_POSITIONS = ["QB", "RB", "WR", "TE"]


def n(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except Exception:
        return default


def impact_bucket(score: float) -> str:
    if score >= 85:
        return "ELITE_IMPACT"
    if score >= 70:
        return "HIGH_STARTER_IMPACT"
    if score >= 55:
        return "STARTER_IMPACT"
    if score >= 40:
        return "FLEX_DEPTH_IMPACT"
    if score >= 25:
        return "LOW_IMPACT"
    return "MISS"


def production_score(pos: str, ppg: float, games: float) -> float:
    games_mult = min(games / 14, 1.0)

    if pos == "QB":
        raw = min(ppg / 22, 1.0) * 100
    elif pos == "RB":
        raw = min(ppg / 17, 1.0) * 100
    elif pos == "WR":
        raw = min(ppg / 17, 1.0) * 100
    elif pos == "TE":
        raw = min(ppg / 12, 1.0) * 100
    else:
        raw = min(ppg / 15, 1.0) * 100

    return raw * games_mult


def load_all(sb, table: str, select: str) -> list[dict]:
    out = []
    page = 1000
    start = 0

    while True:
        rows = (
            sb.table(table)
            .select(select)
            .range(start, start + page - 1)
            .execute()
            .data or []
        )
        out.extend(rows)

        if len(rows) < page:
            break

        start += page

    return out


def build_model() -> None:
    sb = service_client()

    seasons = load_all(
        sb,
        "player_season_stats",
        "season,sleeper_id,gsis_id,player_name,pos,team,games,fantasy_points_ppr,fantasy_ppg_ppr,position_rank,overall_rank",
    )

    universe = load_all(
        sb,
        "player_universe",
        "sleeper_id,player_name,pos,draft_year,rookie_class_year,dynasty_asset_score,future_projection_score,market_consensus_score",
    )

    by_sleeper = {
        str(r.get("sleeper_id")): r
        for r in universe
        if r.get("sleeper_id")
    }

    first_season_by_player = {}
    for row in seasons:
        sid = str(row.get("sleeper_id") or "")
        if not sid:
            continue
        season = int(n(row.get("season")))
        if season <= 0:
            continue
        first_season_by_player[sid] = min(
            first_season_by_player.get(sid, season),
            season,
        )

    out = []

    for s in seasons:
        pos = s.get("pos")
        if pos not in FANTASY_POSITIONS:
            continue

        sleeper_id = str(s.get("sleeper_id") or "")
        if not sleeper_id:
            continue

        u = by_sleeper.get(sleeper_id, {})

        season = int(n(s.get("season")))

        draft_year = u.get("draft_year") or u.get("rookie_class_year")

        # If draft year is missing, infer an approximate rookie season from
        # the player's first season found in player_season_stats later.
        if not draft_year:
            draft_year = first_season_by_player.get(sleeper_id)

        if not draft_year:
            continue

        draft_year = int(n(draft_year))
        years_exp = season - draft_year + 1

        # Rookie impact model is first three NFL seasons.
        if years_exp < 1 or years_exp > 3:
            continue

        games = n(s.get("games"))
        ppg = n(s.get("fantasy_ppg_ppr"))
        total = n(s.get("fantasy_points_ppr"))

        prod = production_score(pos, ppg, games)

        # V1 impact = real production-heavy.
        # Later we add market + future only as after-season context.
        impact = (
            prod * 0.70
            + min(total / 250 * 100, 100) * 0.15
            + max(0, 100 - n(s.get("position_rank"), 100)) * 0.15
        )

        out.append({
            "sleeper_id": sleeper_id,
            "player_name": s.get("player_name") or u.get("player_name"),
            "pos": pos,
            "draft_year": draft_year,
            "season": season,
            "years_exp": years_exp,
            "games": games,
            "fantasy_points_ppr": round(total, 2),
            "fantasy_ppg_ppr": round(ppg, 2),
            "position_rank": int(n(s.get("position_rank"), 999)),
            "overall_rank": int(n(s.get("overall_rank"), 999)),
            "production_score": round(prod, 2),
            "rookie_impact_score": round(impact, 2),
            "impact_bucket": impact_bucket(impact),
        })

    if out:
        sb.table(TARGET_TABLE).upsert(
            out,
            on_conflict="sleeper_id,season",
        ).execute()

    print(f"Upserted {len(out)} rookie historical impact player-season rows")


if __name__ == "__main__":
    build_model()
