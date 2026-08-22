from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client


TARGET_TABLE = "player_gm_thoughts"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _add(thoughts, category, text, weight=50):
    if text:
        thoughts.append({
            "category": category,
            "text": text,
            "weight": weight,
        })


def build_player_gm_thoughts():
    sb = service_client()

    assets = sb.table("roster_asset_values").select("*").execute().data or []
    nfl = sb.table("player_nfl_intelligence").select("*").execute().data or []
    contract_efficiency = sb.table("player_contract_efficiency").select("*").execute().data or []

    nfl_by_key = {
        (str(r.get("sleeper_id")), str(r.get("owner_team_name"))): r
        for r in nfl
    }

    contract_eff_by_key = {
        (str(r.get("sleeper_id")), str(r.get("owner_team_name"))): r
        for r in contract_efficiency
    }

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for a in assets:
        key = (str(a.get("sleeper_id")), str(a.get("owner_team_name")))
        n = nfl_by_key.get(key, {})
        ce = contract_eff_by_key.get(key, {})

        player = (a.get("player_name") or "").strip()
        pos = a.get("pos")
        salary = _num(a.get("salary"))
        years = _num(a.get("years"))
        contract = _num(a.get("contract_value_score"))
        engine = _num(a.get("engine_player_score"))
        recent = _num(a.get("win_now_asset_score"))
        market = _num(a.get("market_liquidity_score"))
        dynasty = _num(a.get("dynasty_asset_score"))
        age_risk = _num(a.get("decline_risk_score"))
        nfl_score = _num(n.get("nfl_intelligence_score"), 50)
        nfl_grade = n.get("nfl_intelligence_grade")
        flags = n.get("nfl_intelligence_flags") or []

        contract_eff_score = _num(ce.get("contract_efficiency_score"))
        contract_eff_grade = ce.get("contract_efficiency_grade")
        contract_eff_rank = ce.get("position_contract_rank")
        contract_eff_pct = _num(ce.get("position_contract_percentile"))
        contract_eff_summary = ce.get("contract_efficiency_summary")
        evidence_profile = ce.get("evidence_profile")

        thoughts = []

        # Talent
        if engine >= 80:
            _add(thoughts, "talent", f"{player} still profiles like a premium player.", 90)
        elif engine >= 60:
            _add(thoughts, "talent", f"{player} still has real player value, even if the asset is not perfect.", 70)
        elif engine < 45:
            _add(thoughts, "talent", f"{player} is not carrying enough player value to get much patience.", 75)

        # Contract
        if pos == "QB" and salary >= 35 and engine >= 80:
            _add(thoughts, "contract", f"The contract is expensive, but elite QB scarcity can justify paying up in superflex.", 95)
        elif salary >= 30 and contract < 40:
            _add(thoughts, "contract", f"The contract is heavy enough that it needs weekly difference-maker production to feel justified.", 90)
        elif salary >= 10 and contract < 55:
            _add(thoughts, "contract", f"The number is not impossible, but the contract needs a clean role and health profile to feel good.", 80)
        elif salary <= 8 and contract >= 45:
            _add(thoughts, "contract", f"The contract is cheap enough that it gives you flexibility.", 75)
        elif contract >= 65:
            _add(thoughts, "contract", f"The contract is playable, but not necessarily a massive edge.", 60)

        # League-relative contract efficiency
        if contract_eff_summary:
            if contract_eff_pct >= 90:
                _add(
                    thoughts,
                    "contract_efficiency",
                    f"League-relative contract view: {contract_eff_summary}",
                    95,
                )
            elif contract_eff_pct >= 65:
                _add(
                    thoughts,
                    "contract_efficiency",
                    f"League-relative contract view: {contract_eff_summary}",
                    80,
                )
            elif contract_eff_pct <= 25:
                _add(
                    thoughts,
                    "contract_efficiency",
                    f"League-relative contract concern: {contract_eff_summary}",
                    90,
                )
            else:
                _add(
                    thoughts,
                    "contract_efficiency",
                    f"League-relative contract view: {contract_eff_summary}",
                    70,
                )

        if evidence_profile == "ROOKIE_PROSPECT":
            _add(
                thoughts,
                "evidence_profile",
                f"This player should be evaluated more like a rookie investment than a proven NFL production profile.",
                85,
            )
        elif evidence_profile in {"ESTABLISHED_NFL", "AGING_VETERAN"}:
            _add(
                thoughts,
                "evidence_profile",
                f"This player should be evaluated mostly on NFL production history, durability, role, and current context.",
                75,
            )

        # NFL context
        flag_text = ", ".join(flags)
        if nfl_score < 50:
            _add(thoughts, "nfl_context", f"The current NFL context is unstable: {flag_text}.", 95)
        elif nfl_score >= 75:
            _add(thoughts, "nfl_context", f"The NFL context looks stable enough to trust the role.", 75)

        if "buried_depth_chart" in flags:
            _add(thoughts, "role", f"The depth chart is a real concern right now.", 90)
        if any("injury" in str(f).lower() for f in flags):
            _add(thoughts, "health", f"The injury/availability profile adds real uncertainty.", 90)

        # Market
        if market >= 65:
            _add(thoughts, "market", f"The league should still recognize the name/value if you test the market.", 80)
        elif market < 40:
            _add(thoughts, "market", f"The market may not give you full credit for the player right now.", 75)

        # Trajectory
        if recent < 45 and dynasty >= 50:
            _add(thoughts, "trajectory", f"The long-term name value is stronger than the recent production signal.", 80)
        if age_risk >= 55:
            _add(thoughts, "trajectory", f"The age/decline profile makes the asset more fragile.", 80)

        for t in thoughts:
            rows.append({
                "sleeper_id": a.get("sleeper_id"),
                "owner_team_name": a.get("owner_team_name"),
                "player_name": player,
                "pos": pos,
                "category": t["category"],
                "thought": t["text"],
                "weight": t["weight"],
                "source": "gm_thoughts_builder",
                "updated_at": now,
            })

    if rows:
        sb.table(TARGET_TABLE).delete().neq("sleeper_id", "__never__").execute()
        sb.table(TARGET_TABLE).insert(rows).execute()

    print(f"Inserted {len(rows)} GM thoughts")


if __name__ == "__main__":
    build_player_gm_thoughts()
