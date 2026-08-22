from __future__ import annotations

from collections import defaultdict
from statistics import mean

from auth import service_client


def fantasy_points(row: dict) -> float:
    return (
        float(row.get("pass_yds") or 0) * 0.04
        + float(row.get("pass_td") or 0) * 4
        - float(row.get("pass_int") or 0) * 2
        + float(row.get("rush_yds") or 0) * 0.1
        + float(row.get("rush_td") or 0) * 6
        + float(row.get("rec_yds") or 0) * 0.1
        + float(row.get("receptions") or 0) * 0.5
        + float(row.get("rec_td") or 0) * 6
        - float(row.get("fumbles_lost") or 0) * 2
        + float(row.get("two_pt_conv") or 0) * 2
    )


def trend_label_from_delta(delta: float, primary_ppg: float) -> str:
    if delta >= 3:
        return "RISING"
    if delta <= -3:
        return "DECLINING"
    if primary_ppg > 0:
        return "STABLE"
    return "UNKNOWN"


def build_player_production_intelligence():
    sb = service_client()

    universe = (
        sb.table("player_universe")
        .select(
            "sleeper_id,player_name,pos,nfl_team,season_ppg,expected_ppg,"
            "historical_ppg,latest_season,latest_week,latest_week_ppr,latest_week_points"
        )
        .execute()
        .data
        or []
    )

    week_rows = (
        sb.table("player_week_stats")
        .select("*")
        .execute()
        .data
        or []
    )

    weekly_by_sid = defaultdict(list)
    for wr in week_rows:
        sid = str(wr.get("sleeper_id") or "")
        if not sid:
            continue
        wr["_fantasy_points"] = fantasy_points(wr)
        weekly_by_sid[sid].append(wr)

    rows = []

    for p in universe:
        sid = str(p.get("sleeper_id") or "")
        player_name = p.get("player_name")
        pos = p.get("pos")
        team = p.get("nfl_team")

        warnings = []
        player_weeks = weekly_by_sid.get(sid, [])

        if player_weeks:
            seasons = sorted({int(w.get("season") or 0) for w in player_weeks if w.get("season")})
            latest_season = seasons[-1] if seasons else None
            season_weeks = [w for w in player_weeks if int(w.get("season") or 0) == latest_season]

            season_points = [float(w.get("_fantasy_points") or 0) for w in season_weeks]
            season_games = len([x for x in season_points if x > 0])

            season_ppg = mean(season_points) if season_points else 0.0

            recent_weeks = sorted(
                season_weeks,
                key=lambda x: int(x.get("week") or 0),
                reverse=True,
            )[:3]
            recent_points = [float(w.get("_fantasy_points") or 0) for w in recent_weeks]
            recent_ppg_signal = mean(recent_points) if recent_points else 0.0

            historical_points = [float(w.get("_fantasy_points") or 0) for w in player_weeks]
            historical_ppg = mean(historical_points) if historical_points else season_ppg

            expected_ppg = season_ppg
            primary_ppg = season_ppg
            trend_delta = recent_ppg_signal - season_ppg
            production_confidence = 90 if season_games >= 5 else 70
            source = "player_week_stats"

        else:
            season_ppg = float(p.get("season_ppg") or 0)
            expected_ppg = float(p.get("expected_ppg") or 0)
            historical_ppg = float(p.get("historical_ppg") or 0)
            recent_ppg_signal = float(p.get("latest_week_ppr") or p.get("latest_week_points") or 0)

            primary_ppg = max(season_ppg, expected_ppg, historical_ppg, recent_ppg_signal)
            trend_delta = recent_ppg_signal - primary_ppg if primary_ppg else 0

            production_confidence = 45 if recent_ppg_signal else 15
            source = "player_universe_latest_week_fallback" if recent_ppg_signal else "no_production_source"

            warnings.append("No player_week_stats rows found")
            if recent_ppg_signal:
                warnings.append(
                    f"Using stale/latest fallback: season={p.get('latest_season')}, week={p.get('latest_week')}"
                )

        trend_label = trend_label_from_delta(trend_delta, primary_ppg)
        production_score = min(100, max(0, primary_ppg * 5))

        rows.append({
            "sleeper_id": sid,
            "player_name": player_name,
            "pos": pos,
            "nfl_team": team,
            "season_ppg": round(season_ppg, 3),
            "expected_ppg": round(expected_ppg, 3),
            "historical_ppg": round(historical_ppg, 3),
            "recent_ppg_signal": round(recent_ppg_signal, 3),
            "primary_ppg": round(primary_ppg, 3),
            "production_score": round(production_score, 2),
            "trend_delta": round(trend_delta, 3),
            "trend_label": trend_label,
            "production_confidence": production_confidence,
            "production_warnings": warnings,
            "source": source,
        })

    if rows:
        sb.table("player_production_intelligence").upsert(
            rows,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(rows)} player_production_intelligence rows")


if __name__ == "__main__":
    build_player_production_intelligence()
