from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client


TARGET_TABLE = "player_gm_arguments"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _add(rows, base, argument_type, category, text, polarity, weight, source="gm_argument_engine"):
    if not text:
        return
    rows.append({
        **base,
        "argument_type": argument_type,
        "category": category,
        "argument_text": text,
        "polarity": polarity,
        "weight": weight,
        "source": source,
    })


def build_player_gm_arguments():
    sb = service_client()

    assets = sb.table("roster_asset_values").select("*").execute().data or []
    ce_rows = sb.table("player_contract_efficiency").select("*").execute().data or []
    nfl_rows = sb.table("player_nfl_intelligence").select("*").execute().data or []
    ev_rows = sb.table("player_evidence_weights").select("*").execute().data or []

    ce_by_key = {(str(r.get("sleeper_id")), str(r.get("owner_team_name"))): r for r in ce_rows}
    nfl_by_key = {(str(r.get("sleeper_id")), str(r.get("owner_team_name"))): r for r in nfl_rows}

    # Evidence is player-level, but IDs can duplicate. Prefer name+pos match later when needed.
    ev_by_id = {str(r.get("sleeper_id")): r for r in ev_rows}
    ev_by_name_pos = {}
    priority = {
        "ESTABLISHED_NFL": 4,
        "AGING_VETERAN": 3,
        "EARLY_CAREER": 2,
        "ROOKIE_PROSPECT": 1,
    }

    def norm(v):
        return str(v or "").strip().lower().replace(".", "").replace("'", "")

    for r in ev_rows:
        key = (norm(r.get("player_name")), str(r.get("pos") or ""))
        existing = ev_by_name_pos.get(key)
        if not existing or priority.get(r.get("evidence_profile"), 0) > priority.get(existing.get("evidence_profile"), 0):
            ev_by_name_pos[key] = r

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for a in assets:
        sid = str(a.get("sleeper_id"))
        owner = str(a.get("owner_team_name"))
        player = (a.get("player_name") or "").strip()
        pos = a.get("pos")

        key = (sid, owner)
        ce = ce_by_key.get(key, {})
        nfl = nfl_by_key.get(key, {})
        ev_by_id_row = ev_by_id.get(sid, {})
        ev_by_name_row = ev_by_name_pos.get((norm(player), str(pos or "")), {})

        def _profile_rank(ev):
            return priority.get(ev.get("evidence_profile"), 0)

        if _profile_rank(ev_by_name_row) >= _profile_rank(ev_by_id_row):
            ev = ev_by_name_row
        else:
            ev = ev_by_id_row

        profile = ce.get("evidence_profile") or ev.get("evidence_profile")
        salary = _num(a.get("salary"))
        years = _num(a.get("years"))
        contract_pct = _num(ce.get("position_contract_percentile"))
        contract_rank = ce.get("position_contract_rank")
        contract_grade = ce.get("contract_efficiency_grade")
        expected_ppg = _num(ce.get("expected_ppg"))
        historical_ppg = _num(ce.get("historical_ppg"))
        peak_ppg = _num(ce.get("peak_ppg"))
        por = _num(ce.get("points_over_replacement"))
        rookie_asset = _num(ce.get("rookie_asset_score") or ev.get("rookie_asset_score"))
        nfl_score = _num(nfl.get("nfl_intelligence_score"), 50)
        nfl_flags = nfl.get("nfl_intelligence_flags") or []
        market = _num(a.get("market_liquidity_score"))
        dynasty = _num(a.get("dynasty_asset_score"))
        win_now = _num(a.get("win_now_asset_score"))

        base = {
            "sleeper_id": sid,
            "owner_team_name": owner,
            "player_name": player,
            "pos": pos,
            "updated_at": now,
        }

        # Contract arguments
        if contract_pct >= 90:
            _add(
                rows, base, "contract", "league_relative_value",
                f"Relative to other {pos} contracts, this is a premium value profile: rank #{contract_rank} at the position.",
                "pro", 95,
            )
        elif contract_pct >= 65:
            _add(
                rows, base, "contract", "league_relative_value",
                f"The contract stacks up well against other {pos} deals, landing in the upper range of the position.",
                "pro", 80,
            )
        elif contract_pct <= 25:
            _add(
                rows, base, "contract", "league_relative_value",
                f"The contract is poor relative to other {pos} deals, landing near the bottom of the position.",
                "con", 90,
            )
        else:
            _add(
                rows, base, "contract", "league_relative_value",
                f"The contract is more middle-of-the-pack than special among {pos}s.",
                "neutral", 60,
            )

        if salary >= 30:
            _add(
                rows, base, "contract", "salary_pressure",
                f"${salary:g} is premium money, so the player needs to create a real weekly edge to justify it.",
                "con", 85,
            )
        elif salary <= 8:
            _add(
                rows, base, "contract", "salary_flexibility",
                f"At ${salary:g}, the contract does not lock up much cap and keeps flexibility intact.",
                "pro", 75,
            )
        else:
            _add(
                rows, base, "contract", "salary_context",
                f"${salary:g} is manageable, but it still needs stable role and production to feel like a clear win.",
                "neutral", 65,
            )

        # Evidence profile
        if profile == "ROOKIE_PROSPECT":
            _add(
                rows, base, "contract", "evidence_profile",
                "This should be treated as a rookie investment, not a proven NFL production contract.",
                "neutral", 90,
            )
            if rookie_asset >= 70:
                _add(
                    rows, base, "contract", "rookie_prestige",
                    "The rookie profile is strong enough that prospect prestige and expected opportunity deserve real weight.",
                    "pro", 90,
                )
            _add(
                rows, base, "contract", "rookie_risk",
                "The main risk is that the contract is based on projection rather than NFL proof.",
                "con", 75,
            )

        elif profile in {"ESTABLISHED_NFL", "AGING_VETERAN"}:
            _add(
                rows, base, "contract", "evidence_profile",
                "This player has enough NFL evidence that production history should matter more than prospect pedigree.",
                "neutral", 80,
            )

        elif profile == "EARLY_CAREER":
            _add(
                rows, base, "contract", "evidence_profile",
                "This is still partly a projection contract because the player is early in his NFL career.",
                "neutral", 75,
            )

        # Production arguments
        if expected_ppg >= 20 and pos == "QB":
            _add(
                rows, base, "contract", "elite_production",
                "The projected production is elite for superflex, which changes how expensive QB contracts should be judged.",
                "pro", 95,
            )
        elif expected_ppg >= 13 and pos in {"RB", "WR"}:
            _add(
                rows, base, "contract", "strong_projection",
                f"The projected production is strong enough to create meaningful value over replacement at {pos}.",
                "pro", 80,
            )
        elif expected_ppg < 10 and pos in {"RB", "WR"}:
            _add(
                rows, base, "contract", "production_gap",
                f"The expected production does not create enough separation from replacement-level {pos}s.",
                "con", 80,
            )

        if historical_ppg >= 20 and pos == "QB":
            _add(
                rows, base, "contract", "historical_output",
                "The recent NFL production history supports the price; this is not just projection.",
                "pro", 90,
            )
        elif historical_ppg >= 12 and pos in {"RB", "WR", "TE"}:
            _add(
                rows, base, "contract", "historical_output",
                "The player has shown usable NFL production before, so the contract is not purely speculative.",
                "pro", 75,
            )

        # NFL context arguments
        if nfl_score < 50:
            flag_text = ", ".join(nfl_flags) if nfl_flags else "unstable role/context"
            _add(
                rows, base, "contract", "nfl_context_risk",
                f"The current football context is a drag on the contract: {flag_text}.",
                "con", 95,
            )
        elif nfl_score >= 75:
            _add(
                rows, base, "contract", "nfl_context_stability",
                "The current NFL context looks stable enough to trust the role.",
                "pro", 75,
            )

        # Market/liquidity
        if market >= 70:
            _add(
                rows, base, "contract", "market_liquidity",
                "The name still has strong market liquidity, which protects the contract if you need to move it.",
                "pro", 75,
            )
        elif market < 40:
            _add(
                rows, base, "contract", "market_liquidity",
                "The market may not give you full credit for this player right now.",
                "con", 65,
            )

    if rows:
        sb.table(TARGET_TABLE).delete().neq("sleeper_id", "__never__").execute()
        sb.table(TARGET_TABLE).insert(rows).execute()

    print(f"Inserted {len(rows)} GM arguments")


if __name__ == "__main__":
    build_player_gm_arguments()
