from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows


CAP = 225.0


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _phase(settings):
    # v1 fallback. Later this can read true league phase.
    phase = str(settings.get("league_phase") or "").upper()

    if phase:
        return phase

    # Default current app work is offseason/preseason.
    return "OFFSEASON"


def _market_value_from_efficiency(r):
    pos = r.get("pos")
    ppg = _num(r.get("expected_ppg"))
    pct = _num(r.get("position_contract_percentile"))
    rookie = _num(r.get("rookie_asset_score"))
    profile = r.get("evidence_profile")

    base = {
        "QB": 10,
        "RB": 8,
        "WR": 8,
        "TE": 5,
    }.get(pos, 5)

    value = base + (ppg * 0.9) + (pct / 100 * 8)

    if pos == "QB":
        value *= 1.2
    if profile == "ROOKIE_PROSPECT":
        value += rookie / 100 * 8

    return max(1.0, round(value, 1))


def _recommended_years(pos, profile, age=None):
    if profile == "ROOKIE_PROSPECT":
        return 2
    if pos == "QB":
        return 3
    if pos == "RB":
        return 1
    if pos in {"WR", "TE"}:
        return 2
    return 1


def build_market_allocation():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    settings_rows = sb.table("league_settings").select("*").execute().data or []
    settings = {r.get("key"): r.get("value") for r in settings_rows}
    phase = _phase(settings)

    contracts = load_internal_contract_rows(sb)
    rosters = sb.table("rosters_current").select("*").execute().data or []
    players = sb.table("players").select("*").execute().data or []
    eff = sb.table("player_contract_efficiency").select("*").execute().data or []
    assets = sb.table("roster_asset_values").select("*").execute().data or []

    contract_by_id = {str(r.get("sleeper_player_id")): r for r in contracts}
    roster_by_id = {str(r.get("player_id")): r for r in rosters}
    eff_by_id = {str(r.get("sleeper_id")): r for r in eff}

    # Use only fantasy positions
    fantasy_positions = {"QB", "RB", "WR", "TE"}

    market_rows = []

    for p in players:
        sid = str(p.get("sleeper_id"))
        pos = p.get("position")
        if pos not in fantasy_positions:
            continue

        contract = contract_by_id.get(sid)
        roster = roster_by_id.get(sid)
        e = eff_by_id.get(sid, {})

        has_contract = contract is not None
        rostered = roster is not None
        current_owner = contract.get("owner_name") if contract else (roster.get("team_id") if roster else None)

        profile = e.get("evidence_profile")
        rookie_asset = _num(e.get("rookie_asset_score"))

        if has_contract:
            pool = "TRADE"
            reason = "Player has an active league contract."
        elif profile == "ROOKIE_PROSPECT" or rookie_asset >= 50:
            pool = "ROOKIE_DRAFT" if phase in {"OFFSEASON", "DRAFT"} else "FAAB"
            reason = "Rookie/prospect profile without active league contract."
        elif phase in {"OFFSEASON", "AUCTION"}:
            pool = "FA_AUCTION"
            reason = "No active league contract during offseason/auction phase."
        else:
            pool = "FAAB"
            reason = "No active league contract during in-season phase."

        estimated_value = _market_value_from_efficiency(e) if e else 1.0

        market_rows.append({
            "sleeper_id": sid,
            "player_name": p.get("full_name"),
            "pos": pos,
            "nfl_team": p.get("team"),
            "market_pool": pool,
            "rostered": rostered,
            "has_contract": has_contract,
            "current_owner": current_owner,
            "estimated_market_value": estimated_value,
            "recommended_years": _recommended_years(pos, profile),
            "pool_reason": reason,
            "updated_at": now,
        })

    # Team budget context
    owners = sorted(set(r.get("owner_name") for r in contracts if r.get("owner_name")) | set(a.get("owner_team_name") for a in assets if a.get("owner_team_name")))

    budget_rows = []

    assets_by_owner = {}
    for a in assets:
        assets_by_owner.setdefault(a.get("owner_team_name"), []).append(a)

    for owner in owners:
        owner_contracts = [c for c in contracts if c.get("owner_name") == owner]
        cap_used = sum(_num(c.get("salary")) for c in owner_contracts)

        dead = 0.0
        try:
            dead_rows = sb.table("cap_adjustments").select("*").eq("owner_name", owner).execute().data or []
            dead = sum(_num(r.get("amount")) for r in dead_rows)
        except Exception:
            dead = 0.0

        cap_available = max(0.0, CAP - cap_used - dead)

        owner_assets = assets_by_owner.get(owner, [])

        pos_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
        quality_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}

        for a in owner_assets:
            pos = a.get("pos")
            if pos in pos_counts:
                pos_counts[pos] += 1
                if _num(a.get("win_now_asset_score")) >= 50 or _num(a.get("dynasty_asset_score")) >= 50:
                    quality_counts[pos] += 1

        needs = {
            "QB": max(0, 2 - quality_counts["QB"]),
            "RB": max(0, 3 - quality_counts["RB"]),
            "WR": max(0, 4 - quality_counts["WR"]),
            "TE": max(0, 1 - quality_counts["TE"]),
        }

        total_needs = sum(needs.values())
        pressure = min(100.0, total_needs * 18 + max(0, 25 - cap_available) * 1.5)

        if cap_available <= 0:
            max_bid = 0
        elif total_needs >= 4:
            max_bid = cap_available * 0.35
        elif total_needs >= 2:
            max_bid = cap_available * 0.50
        else:
            max_bid = cap_available * 0.70

        if total_needs >= 4:
            strategy = "SPREAD_BUDGET"
        elif total_needs >= 2:
            strategy = "BALANCED_BUYING"
        else:
            strategy = "CAN_CONCENTRATE_SPEND"

        budget_rows.append({
            "owner_team_name": owner,
            "cap_available": round(cap_available, 2),
            "starter_needs": needs,
            "roster_pressure_score": round(pressure, 2),
            "max_single_player_bid": round(max_bid, 2),
            "budget_strategy": strategy,
            "updated_at": now,
        })

    budget_by_owner = {r["owner_team_name"]: r for r in budget_rows}

    # Personalized prices
    price_rows = []

    # Limit v1 to relevant market players: FA/draft/FAAB and top trade assets
    relevant_market = [
        r for r in market_rows
        if r["market_pool"] in {"ROOKIE_DRAFT", "FA_AUCTION", "FAAB"}
    ]

    for owner in owners:
        b = budget_by_owner[owner]
        needs = b["starter_needs"]
        cap_available = _num(b["cap_available"])
        max_single = _num(b["max_single_player_bid"])

        for p in relevant_market:
            pos = p["pos"]
            fair = _num(p["estimated_market_value"])

            need = _num(needs.get(pos, 0))
            fit = min(100.0, 45 + need * 20)

            if need <= 0:
                fit -= 20

            affordability = 100.0 if fair <= max_single else max(0.0, 100 - (fair - max_single) * 8)

            bid_multiplier = 1.0
            if need >= 2:
                bid_multiplier += 0.15
            elif need == 1:
                bid_multiplier += 0.05
            else:
                bid_multiplier -= 0.20

            if b["budget_strategy"] == "SPREAD_BUDGET":
                bid_multiplier -= 0.15
            elif b["budget_strategy"] == "CAN_CONCENTRATE_SPEND":
                bid_multiplier += 0.10

            recommended_bid = min(max_single, fair * bid_multiplier)
            walkaway = min(cap_available, max(recommended_bid + 2, fair * (bid_multiplier + 0.15)))

            summary = (
                f"{p['player_name']} fair value is about ${fair:.1f}. "
                f"For {owner}, recommended bid is ${recommended_bid:.1f} with walkaway around ${walkaway:.1f}. "
                f"Team fit {fit:.0f}/100, affordability {affordability:.0f}/100. "
                f"Budget strategy: {b['budget_strategy']}."
            )

            price_rows.append({
                "sleeper_id": p["sleeper_id"],
                "owner_team_name": owner,
                "player_name": p["player_name"],
                "pos": pos,
                "market_pool": p["market_pool"],
                "fair_contract_value": round(fair, 2),
                "recommended_bid": round(recommended_bid, 2),
                "walkaway_price": round(walkaway, 2),
                "recommended_years": p["recommended_years"],
                "team_fit_score": round(fit, 2),
                "affordability_score": round(affordability, 2),
                "recommendation_summary": summary,
                "updated_at": now,
            })

    if market_rows:
        sb.table("player_market_pool").upsert(market_rows, on_conflict="sleeper_id").execute()

    if budget_rows:
        sb.table("team_budget_context").upsert(budget_rows, on_conflict="owner_team_name").execute()

    if price_rows:
        sb.table("personalized_player_price").upsert(price_rows, on_conflict="sleeper_id,owner_team_name").execute()

    print(f"Upserted market pool rows: {len(market_rows)}")
    print(f"Upserted team budget rows: {len(budget_rows)}")
    print(f"Upserted personalized price rows: {len(price_rows)}")


if __name__ == "__main__":
    build_market_allocation()
