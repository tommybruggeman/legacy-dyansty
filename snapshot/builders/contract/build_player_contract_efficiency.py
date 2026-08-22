from __future__ import annotations

from datetime import datetime, timezone
from statistics import median

from auth import service_client


TARGET_TABLE = "player_contract_efficiency"


REPLACEMENT_BASE = {
    "QB": 14.0,
    "RB": 8.0,
    "WR": 8.5,
    "TE": 6.5,
}


POSITION_SCARCITY = {
    "QB": 1.35,  # superflex premium
    "RB": 1.15,
    "WR": 1.00,
    "TE": 1.10,
}


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _norm_name(v):
    return str(v or "").strip().lower().replace(".", "").replace("'", "")


def _salary_tier(salary):
    salary = _num(salary)
    if salary >= 40:
        return "ELITE_COST"
    if salary >= 25:
        return "PREMIUM_COST"
    if salary >= 12:
        return "STARTER_COST"
    if salary >= 5:
        return "VALUE_COST"
    return "CHEAP_COST"


def _grade(score):
    if score >= 80:
        return "LEAGUE_WINNING_CONTRACT"
    if score >= 65:
        return "STRONG_VALUE"
    if score >= 50:
        return "GOOD_VALUE"
    if score >= 35:
        return "FAIR_VALUE"
    if score >= 20:
        return "WEAK_VALUE"
    return "BAD_VALUE"


def _ordinal(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _summary(r):
    player = r["player_name"]
    pos = r["pos"]
    salary = r["salary"]
    years = r["years"]
    grade = r["contract_efficiency_grade"]
    rank = r["position_contract_rank"]
    pct = r["position_contract_percentile"]
    vpd = r["value_per_dollar"]
    por = r["points_over_replacement"]
    ppg = r["expected_ppg"]

    if pct >= 90:
        return (
            f"{player} is expensive at ${salary:g}/{years:g} yrs, but relative to other {pos} contracts "
            f"this is a top-tier deal: {_ordinal(rank)} at the position. "
            f"The price is justified by projected production ({ppg:.1f} PPG), positional scarcity, and value over replacement."
        )

    if grade in {"LEAGUE_WINNING_CONTRACT", "STRONG_VALUE", "GOOD_VALUE"}:
        return (
            f"{player} is a positive contract efficiency play at ${salary:g}/{years:g} yrs. "
            f"Among {pos}s, this profiles around {_ordinal(rank)} by contract efficiency "
            f"with value-per-dollar {vpd:.2f} and {por:.1f} projected points over replacement."
        )

    if grade == "FAIR_VALUE":
        return (
            f"{player} is a workable but not special contract at ${salary:g}/{years:g} yrs. "
            f"It lands around the {pct:.0f}th percentile among {pos} contracts."
        )

    return (
        f"{player} looks like an inefficient contract at ${salary:g}/{years:g} yrs. "
        f"The expected production does not clear the cost cleanly compared with other {pos} contracts, "
        f"landing around the {pct:.0f}th percentile at the position."
    )


def _rank_percentiles(rows, score_key, rank_key, pct_key, group_key=None):
    if group_key:
        groups = {}
        for r in rows:
            groups.setdefault(r[group_key], []).append(r)
    else:
        groups = {"ALL": rows}

    for group_rows in groups.values():
        group_rows.sort(key=lambda x: x[score_key], reverse=True)
        n = len(group_rows)

        for idx, r in enumerate(group_rows, 1):
            r[rank_key] = idx
            if n <= 1:
                r[pct_key] = 100.0
            else:
                # rank 1 = 100th percentile
                r[pct_key] = round(100 * (1 - ((idx - 1) / (n - 1))), 2)



def _median_safe(values, default=0.0):
    vals = [float(v) for v in values if v is not None and float(v) > 0]
    if not vals:
        return default
    return float(median(vals))


def _recent_weighted_ppg(seasons):
    if not seasons:
        return 0.0

    seasons = sorted(seasons, key=lambda r: int(r.get("season") or 0), reverse=True)[:3]
    weights = [0.55, 0.30, 0.15]

    total = 0.0
    total_w = 0.0

    for r, w in zip(seasons, weights):
        ppg = _num(r.get("fantasy_ppg_ppr") or r.get("fantasy_ppg"))
        games = _num(r.get("games"))

        if ppg <= 0:
            continue

        # Small durability adjustment. A 17-game season gets full credit.
        durability = min(1.0, games / 17.0) if games > 0 else 0.7
        adj_ppg = ppg * (0.85 + durability * 0.15)

        total += adj_ppg * w
        total_w += w

    return total / total_w if total_w else 0.0


def _production_consistency_score(seasons):
    vals = [
        _num(r.get("fantasy_ppg_ppr") or r.get("fantasy_ppg"))
        for r in seasons
        if _num(r.get("fantasy_ppg_ppr") or r.get("fantasy_ppg")) > 0
    ]

    if len(vals) < 2:
        return 50.0

    avg = sum(vals) / len(vals)
    if avg <= 0:
        return 50.0

    variance = sum((v - avg) ** 2 for v in vals) / len(vals)
    std = variance ** 0.5
    cv = std / avg

    # lower coefficient of variation = more consistent
    return max(0.0, min(100.0, 100 - cv * 120))


def _durability_score(seasons):
    if not seasons:
        return 50.0

    recent = sorted(seasons, key=lambda r: int(r.get("season") or 0), reverse=True)[:3]
    games = [_num(r.get("games")) for r in recent if _num(r.get("games")) > 0]

    if not games:
        return 50.0

    avg_games = sum(games) / len(games)
    return max(0.0, min(100.0, (avg_games / 17.0) * 100))


def _peak_ppg(seasons):
    vals = [
        _num(r.get("fantasy_ppg_ppr") or r.get("fantasy_ppg"))
        for r in seasons
        if _num(r.get("fantasy_ppg_ppr") or r.get("fantasy_ppg")) > 0
    ]
    return max(vals) if vals else 0.0


def _best_recent_position_rank(seasons):
    ranks = [
        int(_num(r.get("position_rank"), 999))
        for r in seasons
        if _num(r.get("position_rank"), 999) > 0
    ]
    return min(ranks) if ranks else 999

def build_player_contract_efficiency():
    sb = service_client()

    assets = sb.table("roster_asset_values").select("*").execute().data or []
    dev = sb.table("player_development_features").select("*").execute().data or []
    dynasty = sb.table("player_dynasty_asset_engine").select("*").execute().data or []
    nfl = sb.table("player_nfl_intelligence").select("*").execute().data or []
    season_stats = sb.table("player_season_stats").select("*").gte("season", 2021).execute().data or []
    evidence = sb.table("player_evidence_weights").select("*").execute().data or []
    evidence = sb.table("player_evidence_weights").select("*").execute().data or []
    evidence = sb.table("player_evidence_weights").select("*").execute().data or []

    dev_by_id = {str(r.get("sleeper_id")): r for r in dev}
    dynasty_by_id = {str(r.get("sleeper_id")): r for r in dynasty}
    nfl_by_key = {
        (str(r.get("sleeper_id")), str(r.get("owner_team_name"))): r
        for r in nfl
    }

    evidence_by_id = {str(r.get("sleeper_id")): r for r in evidence}
    evidence_by_name_pos = {}

    profile_priority = {
        "ESTABLISHED_NFL": 4,
        "AGING_VETERAN": 3,
        "EARLY_CAREER": 2,
        "ROOKIE_PROSPECT": 1,
    }

    for r in evidence:
        key = (_norm_name(r.get("player_name")), str(r.get("pos") or ""))
        existing = evidence_by_name_pos.get(key)
        if not existing or profile_priority.get(r.get("evidence_profile"), 0) > profile_priority.get(existing.get("evidence_profile"), 0):
            evidence_by_name_pos[key] = r

    evidence_by_id = {str(r.get("sleeper_id")): r for r in evidence}
    evidence_by_name_pos = {}

    profile_priority = {
        "ESTABLISHED_NFL": 4,
        "AGING_VETERAN": 3,
        "EARLY_CAREER": 2,
        "ROOKIE_PROSPECT": 1,
    }

    for r in evidence:
        key = (_norm_name(r.get("player_name")), str(r.get("pos") or ""))
        existing = evidence_by_name_pos.get(key)
        if not existing or profile_priority.get(r.get("evidence_profile"), 0) > profile_priority.get(existing.get("evidence_profile"), 0):
            evidence_by_name_pos[key] = r

    evidence_by_id = {str(r.get("sleeper_id")): r for r in evidence}
    evidence_by_name_pos = {}

    profile_priority = {
        "ESTABLISHED_NFL": 4,
        "AGING_VETERAN": 3,
        "EARLY_CAREER": 2,
        "ROOKIE_PROSPECT": 1,
    }

    for r in evidence:
        key = (_norm_name(r.get("player_name")), str(r.get("pos") or ""))
        existing = evidence_by_name_pos.get(key)
        if not existing or profile_priority.get(r.get("evidence_profile"), 0) > profile_priority.get(existing.get("evidence_profile"), 0):
            evidence_by_name_pos[key] = r

    seasons_by_id = {}
    seasons_by_name_pos = {}

    for r in season_stats:
        seasons_by_id.setdefault(str(r.get("sleeper_id")), []).append(r)

        name_key = (_norm_name(r.get("player_name")), str(r.get("pos") or ""))
        seasons_by_name_pos.setdefault(name_key, []).append(r)

    print(f"Loaded season stat rows: {len(season_stats)}")

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for a in assets:
        sid = str(a.get("sleeper_id"))
        owner = str(a.get("owner_team_name"))

        d = dev_by_id.get(sid, {})
        dy = dynasty_by_id.get(sid, {})
        ni = nfl_by_key.get((sid, owner), {})

        player = (a.get("player_name") or "").strip()
        pos = a.get("pos") or d.get("pos") or dy.get("pos") or "UNK"

        # Prefer the best name+position evidence profile because old nflverse IDs
        # and Sleeper IDs can create duplicate rows for the same player.
        ev_by_name = evidence_by_name_pos.get((_norm_name(player), str(pos or "")), {})
        ev_by_id = evidence_by_id.get(sid, {})

        def _profile_rank(ev):
            return profile_priority.get(ev.get("evidence_profile"), 0)

        if _profile_rank(ev_by_name) >= _profile_rank(ev_by_id):
            ev = ev_by_name
        else:
            ev = ev_by_id

        evidence_profile = ev.get("evidence_profile")
        rookie_asset_score = _num(ev.get("rookie_asset_score"))

        # Prefer the best name+position evidence profile because old nflverse IDs
        # and Sleeper IDs can create duplicate rows for the same player.
        ev_by_name = evidence_by_name_pos.get((_norm_name(player), str(pos or "")), {})
        ev_by_id = evidence_by_id.get(sid, {})

        def _profile_rank(ev):
            return profile_priority.get(ev.get("evidence_profile"), 0)

        if _profile_rank(ev_by_name) >= _profile_rank(ev_by_id):
            ev = ev_by_name
        else:
            ev = ev_by_id

        evidence_profile = ev.get("evidence_profile")
        rookie_asset_score = _num(ev.get("rookie_asset_score"))

        # Prefer the best name+position evidence profile because old nflverse IDs
        # and Sleeper IDs can create duplicate rows for the same player.
        ev_by_name = evidence_by_name_pos.get((_norm_name(player), str(pos or "")), {})
        ev_by_id = evidence_by_id.get(sid, {})

        def _profile_rank(ev):
            return profile_priority.get(ev.get("evidence_profile"), 0)

        if _profile_rank(ev_by_name) >= _profile_rank(ev_by_id):
            ev = ev_by_name
        else:
            ev = ev_by_id

        evidence_profile = ev.get("evidence_profile")
        rookie_asset_score = _num(ev.get("rookie_asset_score"))

        seasons = seasons_by_id.get(sid, [])
        if not seasons:
            seasons = seasons_by_name_pos.get((_norm_name(player), str(pos or "")), [])

        recent_weighted_ppg = _recent_weighted_ppg(seasons)
        consistency_score = _production_consistency_score(seasons)
        durability_score = _durability_score(seasons)
        peak_ppg = _peak_ppg(seasons)
        best_position_rank = _best_recent_position_rank(seasons)
        salary = max(1.0, _num(a.get("salary"), 1.0))
        years = max(1.0, _num(a.get("years"), 1.0))

        expected_ppg = _num(d.get("expected_ppg_next"))
        current_ppg = _num(d.get("current_ppg"))
        historical_ppg = _num(d.get("historical_career_ppg"))

        # fallback from asset scores when development features are missing
        # If development projection is missing/bad, infer PPG from win-now asset score.
        # Rough scale: 100 -> 25 PPG, 80 -> 20 PPG, 60 -> 15 PPG.
        if expected_ppg <= 0 or expected_ppg < 6:
            expected_ppg = _num(a.get("win_now_asset_score")) * 0.25

        # Rich production baseline:
        # - recent_weighted_ppg anchors proven output
        # - expected_ppg captures forecast/future
        # - current/historical fill gaps
        # - peak gives elite players credit for ceiling without fully anchoring to best season
        ppg_signals = []

        if recent_weighted_ppg > 0:
            ppg_signals.append((recent_weighted_ppg, 0.45))
        if expected_ppg > 0:
            ppg_signals.append((expected_ppg, 0.25))
        if current_ppg > 0:
            ppg_signals.append((current_ppg, 0.15))
        if historical_ppg > 0:
            ppg_signals.append((historical_ppg, 0.10))
        if peak_ppg > 0:
            ppg_signals.append((peak_ppg, 0.05))

        if ppg_signals:
            total_w = sum(w for _, w in ppg_signals)
            projected_ppg = sum(v * w for v, w in ppg_signals) / total_w
        else:
            projected_ppg = _num(a.get("win_now_asset_score")) * 0.25

        # Rookie/prospect override:
        # If there is little/no NFL production but strong rookie asset signal,
        # estimate PPG from prospect value + future projection + NFL context.
        if evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 55 and recent_weighted_ppg <= 0:
            rookie_ppg_floor = REPLACEMENT_BASE.get(pos, 7.5)
            rookie_ppg_ceiling = {
                "RB": 16.0,
                "WR": 14.0,
                "TE": 11.0,
                "QB": 18.0,
            }.get(pos, 12.0)

            rookie_strength = min(1.0, max(0.0, rookie_asset_score / 100.0))
            future_strength = min(1.0, max(0.0, _num(dy.get("future_projection_score"), 50) / 100.0))
            context_strength = min(1.0, max(0.0, _num(ni.get("nfl_intelligence_score"), 50) / 100.0))

            blended_strength = (
                rookie_strength * 0.45
                + future_strength * 0.30
                + context_strength * 0.25
            )

            projected_ppg = rookie_ppg_floor + (rookie_ppg_ceiling - rookie_ppg_floor) * blended_strength

        # Rookie/prospect override:
        # If there is little/no NFL production but strong rookie asset signal,
        # estimate PPG from prospect value + future projection + NFL context.
        if evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 55 and recent_weighted_ppg <= 0:
            rookie_ppg_floor = REPLACEMENT_BASE.get(pos, 7.5)
            rookie_ppg_ceiling = {
                "RB": 16.0,
                "WR": 14.0,
                "TE": 11.0,
                "QB": 18.0,
            }.get(pos, 12.0)

            rookie_strength = min(1.0, max(0.0, rookie_asset_score / 100.0))
            future_strength = min(1.0, max(0.0, _num(dy.get("future_projection_score"), 50) / 100.0))
            context_strength = min(1.0, max(0.0, _num(ni.get("nfl_intelligence_score"), 50) / 100.0))

            blended_strength = (
                rookie_strength * 0.45
                + future_strength * 0.30
                + context_strength * 0.25
            )

            projected_ppg = rookie_ppg_floor + (rookie_ppg_ceiling - rookie_ppg_floor) * blended_strength

        # Rookie/prospect override:
        # If there is little/no NFL production but strong rookie asset signal,
        # estimate PPG from prospect value + future projection + NFL context.
        if evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 55 and recent_weighted_ppg <= 0:
            rookie_ppg_floor = REPLACEMENT_BASE.get(pos, 7.5)
            rookie_ppg_ceiling = {
                "RB": 16.0,
                "WR": 14.0,
                "TE": 11.0,
                "QB": 18.0,
            }.get(pos, 12.0)

            rookie_strength = min(1.0, max(0.0, rookie_asset_score / 100.0))
            future_strength = min(1.0, max(0.0, _num(dy.get("future_projection_score"), 50) / 100.0))
            context_strength = min(1.0, max(0.0, _num(ni.get("nfl_intelligence_score"), 50) / 100.0))

            blended_strength = (
                rookie_strength * 0.45
                + future_strength * 0.30
                + context_strength * 0.25
            )

            projected_ppg = rookie_ppg_floor + (rookie_ppg_ceiling - rookie_ppg_floor) * blended_strength

        replacement_ppg = REPLACEMENT_BASE.get(pos, 7.5)
        scarcity = POSITION_SCARCITY.get(pos, 1.0)

        nfl_score = _num(ni.get("nfl_intelligence_score"), 50)
        nfl_multiplier = 0.70 + (nfl_score / 100) * 0.60  # 0.70 to 1.30

        future_projection = _num(dy.get("future_projection_score"), _num(a.get("dynasty_window_score"), 50))
        future_multiplier = 0.80 + (future_projection / 100) * 0.40  # 0.80 to 1.20

        points_over_replacement = max(0.0, projected_ppg - replacement_ppg)

        value_per_dollar = (
            points_over_replacement
            * scarcity
            * nfl_multiplier
            * future_multiplier
        ) / salary

        projected_surplus_value = (
            points_over_replacement
            * scarcity
            * nfl_multiplier
            * future_multiplier
            * years
        ) - salary

        win_now = _num(a.get("win_now_asset_score"))
        dynasty_score = _num(a.get("dynasty_asset_score"))
        market = _num(a.get("market_liquidity_score"))

        # Convert value-per-dollar and asset context to a 0-100 efficiency score.
        raw_efficiency = (
            value_per_dollar * 28
            + max(0, projected_surplus_value) * 1.25
            + win_now * 0.18
            + dynasty_score * 0.18
            + market * 0.10
            + nfl_score * 0.12
        )

        # Penalty for expensive contracts that do not clear replacement by enough.
        if salary >= 25 and points_over_replacement < 6:
            raw_efficiency -= 20

        # Superflex elite QB override: high-salary QBs can still be efficient because replacement gap matters.
        if pos == "QB" and salary >= 35 and (projected_ppg >= 20 or win_now >= 85 or market >= 85):
            raw_efficiency += 24

        # Rookie upside contracts deserve credit when prestige/future/context are strong.
        if evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 70:
            raw_efficiency += 18
        elif evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 60:
            raw_efficiency += 10

        # Rookie upside contracts deserve credit when prestige/future/context are strong.
        if evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 70:
            raw_efficiency += 18
        elif evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 60:
            raw_efficiency += 10

        # Rookie upside contracts deserve credit when prestige/future/context are strong.
        if evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 70:
            raw_efficiency += 18
        elif evidence_profile == "ROOKIE_PROSPECT" and rookie_asset_score >= 60:
            raw_efficiency += 10

        # Reward stable, durable producers. This prevents proven elite players from being crushed by one weak projection.
        raw_efficiency += consistency_score * 0.05
        raw_efficiency += durability_score * 0.04

        # Positional finish boost: elite finishes matter in contract valuation.
        if best_position_rank <= 3:
            raw_efficiency += 12
        elif best_position_rank <= 8:
            raw_efficiency += 7
        elif best_position_rank <= 15:
            raw_efficiency += 3

        score = max(0.0, min(100.0, raw_efficiency))

        row = {
            "sleeper_id": sid,
            "owner_team_name": owner,
            "player_name": player,
            "pos": pos,
            "salary": round(salary, 2),
            "years": round(years, 2),
            "expected_ppg": round(projected_ppg, 2),
            "current_ppg": round(current_ppg, 2),
            "historical_ppg": round(historical_ppg, 2),
            "win_now_asset_score": round(win_now, 2),
            "dynasty_asset_score": round(dynasty_score, 2),
            "future_projection_score": round(future_projection, 2),
            "nfl_intelligence_score": round(nfl_score, 2),
            "market_liquidity_score": round(market, 2),
            "replacement_ppg": round(replacement_ppg, 2),
            "points_over_replacement": round(points_over_replacement, 2),
            "historical_ppg": round(recent_weighted_ppg, 2),
            "production_consistency_score": round(consistency_score, 2),
            "durability_score": round(durability_score, 2),
            "peak_ppg": round(peak_ppg, 2),
            "best_position_rank": int(best_position_rank),
            "value_per_dollar": round(value_per_dollar, 4),
            "projected_surplus_value": round(projected_surplus_value, 2),
            "salary_tier": _salary_tier(salary),
            "evidence_profile": evidence_profile,
            "rookie_asset_score": round(rookie_asset_score, 2),
            "contract_efficiency_score": round(score, 2),
            "contract_efficiency_grade": _grade(score),
            "updated_at": now,
        }

        rows.append(row)

    _rank_percentiles(
        rows,
        "contract_efficiency_score",
        "league_contract_rank",
        "league_contract_percentile",
    )
    _rank_percentiles(
        rows,
        "contract_efficiency_score",
        "position_contract_rank",
        "position_contract_percentile",
        group_key="pos",
    )

    for r in rows:
        r["contract_efficiency_summary"] = _summary(r)

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    build_player_contract_efficiency()
