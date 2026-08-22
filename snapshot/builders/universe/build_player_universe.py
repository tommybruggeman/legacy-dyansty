from __future__ import annotations

from datetime import datetime, timezone

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows
from snapshot.builders.universe.player_identity_resolver import norm_name


TARGET_TABLE = "player_universe"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _norm(v):
    return norm_name(v)


def _best_by_score(rows, key="contract_efficiency_score"):
    if not rows:
        return {}
    return sorted(rows, key=lambda r: _num(r.get(key)), reverse=True)[0]


def build_player_universe():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    players = sb.table("players").select("*").execute().data or []
    sleeper_players = sb.table("sleeper_players").select("*").execute().data or []
    contracts = load_internal_contract_rows(sb)
    rosters = sb.table("rosters_current").select("*").execute().data or []
    dynasty = sb.table("player_dynasty_asset_engine").select("*").execute().data or []
    nfl = sb.table("player_nfl_intelligence").select("*").execute().data or []
    ce = sb.table("player_contract_efficiency").select("*").execute().data or []
    market = sb.table("player_market_pool").select("*").execute().data or []
    weekly = sb.table("player_weekly_stats").select("*").execute().data or []
    season = sb.table("player_season_stats").select("*").execute().data or []
    identity = sb.table("player_identity_context").select("*").execute().data or []

    by_id = {}

    def touch(sid):
        sid = str(sid or "").strip()
        if not sid:
            return None
        by_id.setdefault(sid, {"sleeper_id": sid})
        return by_id[sid]

    # Base players
    for p in players:
        r = touch(p.get("sleeper_id"))
        if not r:
            continue
        r["player_name"] = r.get("player_name") or p.get("full_name")
        r["search_name"] = r.get("search_name") or _norm(p.get("full_name"))
        r["pos"] = r.get("pos") or p.get("position")
        r["nfl_team"] = r.get("nfl_team") or p.get("team")

    for p in sleeper_players:
        r = touch(p.get("sleeper_player_id"))
        if not r:
            continue
        r["player_name"] = r.get("player_name") or p.get("full_name")
        r["search_name"] = r.get("search_name") or p.get("search_name") or _norm(p.get("full_name"))
        r["pos"] = r.get("pos") or p.get("position")
        r["nfl_team"] = r.get("nfl_team") or p.get("team")
        r["nfl_status"] = r.get("nfl_status") or p.get("status")
        r["active"] = p.get("is_active")

    # Contracts
    for c in contracts:
        r = touch(c.get("sleeper_player_id"))
        if not r:
            continue
        r["player_name"] = r.get("player_name") or c.get("player_name")
        r["pos"] = r.get("pos") or c.get("player_position")
        r["current_owner"] = c.get("owner_name")
        r["has_contract"] = True
        r["salary"] = _num(c.get("salary"))
        r["years"] = _num(c.get("contract_years_left"))
        r["contract_total_years"] = _num(c.get("contract_total_years"))
        r["is_rookie_contract"] = c.get("is_rookie")

    # Rosters
    for rr in rosters:
        r = touch(rr.get("player_id"))
        if not r:
            continue
        r["current_owner"] = r.get("current_owner") or rr.get("team_id")
        r["roster_status"] = rr.get("status")

    # Identity
    for ident in identity:
        r = touch(ident.get("sleeper_id"))
        if not r:
            continue

        for field in [
            "player_name",
            "search_name",
            "pos",
            "nfl_team",
            "college",
            "draft_year",
            "draft_round",
            "draft_pick",
            "rookie_class_year",
            "years_exp",
        ]:
            if ident.get(field) is not None and not r.get(field):
                r[field] = ident.get(field)

    # Dynasty
    for d in dynasty:
        r = touch(d.get("sleeper_id"))
        if not r:
            continue
        r["player_name"] = r.get("player_name") or d.get("player_name")
        r["pos"] = r.get("pos") or d.get("pos")
        r["nfl_team"] = r.get("nfl_team") or d.get("nfl_team")
        r["dynasty_asset_score"] = _num(d.get("dynasty_asset_score"))
        r["future_projection_score"] = _num(d.get("future_projection_score"))
        r["rookie_asset_score"] = _num(d.get("rookie_asset_score"))
        r["market_consensus_score"] = _num(d.get("market_consensus_score"))

    # NFL intelligence: prefer exact owner row if present later, otherwise best score
    nfl_by_id = {}
    for n in nfl:
        nfl_by_id.setdefault(str(n.get("sleeper_id")), []).append(n)

    for sid, rows in nfl_by_id.items():
        n = _best_by_score(rows, "nfl_intelligence_score")
        r = touch(sid)
        if not r:
            continue
        r["player_name"] = r.get("player_name") or n.get("player_name")
        r["pos"] = r.get("pos") or n.get("pos")
        r["nfl_team"] = r.get("nfl_team") or n.get("nfl_team")
        r["nfl_status"] = r.get("nfl_status") or n.get("nfl_status")
        r["active"] = r.get("active") if r.get("active") is not None else n.get("active")
        r["nfl_intelligence_score"] = _num(n.get("nfl_intelligence_score"))
        r["nfl_intelligence_grade"] = n.get("nfl_intelligence_grade")
        r["nfl_intelligence_flags"] = n.get("nfl_intelligence_flags") or []

    # Contract efficiency
    ce_by_id = {}
    for x in ce:
        ce_by_id.setdefault(str(x.get("sleeper_id")), []).append(x)

    for sid, rows in ce_by_id.items():
        x = _best_by_score(rows, "contract_efficiency_score")
        r = touch(sid)
        if not r:
            continue
        r["player_name"] = r.get("player_name") or x.get("player_name")
        r["pos"] = r.get("pos") or x.get("pos")
        r["contract_efficiency_score"] = _num(x.get("contract_efficiency_score"))
        r["contract_efficiency_grade"] = x.get("contract_efficiency_grade")
        r["position_contract_rank"] = int(_num(x.get("position_contract_rank"), 999))
        r["position_contract_percentile"] = _num(x.get("position_contract_percentile"))
        r["expected_ppg"] = _num(x.get("expected_ppg"))
        r["historical_ppg"] = _num(x.get("historical_ppg"))

    # Market pool
    for m in market:
        r = touch(m.get("sleeper_id"))
        if not r:
            continue
        r["player_name"] = r.get("player_name") or m.get("player_name")
        r["pos"] = r.get("pos") or m.get("pos")
        r["market_pool"] = m.get("market_pool")
        r["estimated_market_value"] = _num(m.get("estimated_market_value"))
        r["recommended_years"] = _num(m.get("recommended_years"))
        r["current_owner"] = r.get("current_owner") or m.get("current_owner")

    # Weekly latest
    weekly_by_id = {}
    for w in weekly:
        weekly_by_id.setdefault(str(w.get("sleeper_id")), []).append(w)

    for sid, rows in weekly_by_id.items():
        rows = sorted(rows, key=lambda x: (int(_num(x.get("season"))), int(_num(x.get("week")))), reverse=True)
        w = rows[0]
        r = touch(sid)
        if not r:
            continue
        r["canonical_player_id"] = r.get("canonical_player_id") or w.get("canonical_player_id")
        r["gsis_id"] = r.get("gsis_id") or w.get("gsis_id")
        r["player_name"] = r.get("player_name") or w.get("player_name")
        r["pos"] = r.get("pos") or w.get("pos")
        r["nfl_team"] = r.get("nfl_team") or w.get("team")
        r["latest_season"] = int(_num(w.get("season")))
        r["latest_week"] = int(_num(w.get("week")))
        r["latest_week_points"] = _num(w.get("fantasy_points"))
        r["latest_week_ppr"] = _num(w.get("fantasy_points_ppr"))

    # Season latest
    season_by_id = {}
    for s in season:
        season_by_id.setdefault(str(s.get("sleeper_id")), []).append(s)

    for sid, rows in season_by_id.items():
        rows = sorted(rows, key=lambda x: int(_num(x.get("season"))), reverse=True)
        ss = rows[0]
        r = touch(sid)
        if not r:
            continue
        r["player_name"] = r.get("player_name") or ss.get("player_name")
        r["pos"] = r.get("pos") or ss.get("pos")
        r["season_ppg"] = _num(ss.get("fantasy_ppg_ppr") or ss.get("fantasy_ppg"))
        r["season_games"] = _num(ss.get("games"))

    # ------------------------------------------------------------
    # Merge duplicate identities:
    # Some nflverse rows use gsis-style IDs like 00-0037740 while
    # app/contract rows use Sleeper IDs like 8146. Merge by name+pos.
    # Prefer the row with an owner/contract, but preserve stats/IDs.
    # ------------------------------------------------------------
    grouped = {}

    for sid, r in list(by_id.items()):
        name_key = _norm(r.get("player_name"))
        pos_key = r.get("pos")
        if not name_key or pos_key not in {"QB", "RB", "WR", "TE"}:
            continue
        grouped.setdefault((name_key, pos_key), []).append((sid, r))

    merged_by_id = {}

    def _priority(item):
        sid, r = item
        return (
            1 if r.get("has_contract") else 0,
            1 if r.get("current_owner") else 0,
            _num(r.get("contract_efficiency_score")),
            _num(r.get("dynasty_asset_score")),
            0 if str(sid).startswith("00-") else 1,
        )

    for key, items in grouped.items():
        primary_sid, primary = sorted(items, key=_priority, reverse=True)[0]
        merged = dict(primary)

        for sid, r in items:
            if sid == primary_sid:
                continue

            # Preserve alternate IDs
            if str(sid).startswith("00-"):
                merged["gsis_id"] = merged.get("gsis_id") or sid
            else:
                merged["canonical_player_id"] = merged.get("canonical_player_id") or sid

            for k, v in r.items():
                if v is None:
                    continue

                # Fill blanks
                if merged.get(k) in [None, "", 0, 0.0, []]:
                    merged[k] = v

                # Prefer real latest stat info if duplicate has it
                if k in {
                    "latest_season", "latest_week", "latest_week_points",
                    "latest_week_ppr", "season_ppg", "season_games",
                    "historical_ppg"
                } and _num(v) > _num(merged.get(k)):
                    merged[k] = v

                # Preserve gsis/canonical ids
                if k in {"gsis_id", "canonical_player_id"} and v and not merged.get(k):
                    merged[k] = v

        merged_by_id[primary_sid] = merged

    by_id = merged_by_id

    # ------------------------------------------------------------
    # Second-pass stat repair:
    # player_season_stats/player_weekly_stats often store GSIS IDs in
    # the column named sleeper_id. After duplicate identity merge, use
    # gsis_id + normalized name/pos to repair season_ppg/latest stats.
    # ------------------------------------------------------------
    by_gsis = {}
    by_namepos = {}

    for sid, r in by_id.items():

        if r.get("player_name") == "Omarion Hampton":
            print("\n==============================")
            print("DEBUG OMARION HAMPTON")
            print({
                "college": r.get("college"),
                "draft_year": r.get("draft_year"),
                "draft_round": r.get("draft_round"),
                "draft_pick": r.get("draft_pick"),
                "rookie_class_year": r.get("rookie_class_year"),
                "years_exp": r.get("years_exp"),
            })

        if r.get("gsis_id"):
            by_gsis[str(r.get("gsis_id"))] = r

        nk = (_norm(r.get("player_name")), r.get("pos"))
        if nk[0] and nk[1] in {"QB", "RB", "WR", "TE"}:
            by_namepos[nk] = r

    for ss in season:
        gsis_or_sid = str(ss.get("sleeper_id") or "").strip()
        nk = (_norm(ss.get("player_name")), ss.get("pos"))

        r = by_gsis.get(gsis_or_sid) or by_namepos.get(nk)
        if not r:
            continue

        ppg = _num(ss.get("fantasy_ppg_ppr") or ss.get("fantasy_ppg"))
        games = _num(ss.get("games"))

        if ppg > _num(r.get("season_ppg")):
            r["season_ppg"] = ppg
            r["season_games"] = games
            r["gsis_id"] = r.get("gsis_id") or gsis_or_sid

    for w in weekly:
        gsis_or_sid = str(w.get("sleeper_id") or "").strip()
        nk = (_norm(w.get("player_name")), w.get("pos"))

        r = by_gsis.get(gsis_or_sid) or by_namepos.get(nk)
        if not r:
            continue

        season_num = int(_num(w.get("season")))
        week_num = int(_num(w.get("week")))
        current_key = (int(_num(r.get("latest_season"))), int(_num(r.get("latest_week"))))

        if (season_num, week_num) >= current_key:
            r["latest_season"] = season_num
            r["latest_week"] = week_num
            r["latest_week_points"] = _num(w.get("fantasy_points"))
            r["latest_week_ppr"] = _num(w.get("fantasy_points_ppr"))
            r["gsis_id"] = r.get("gsis_id") or gsis_or_sid

    # If contract efficiency is missing, compute a fallback so the GM brain
    # does not treat missing data as true zero efficiency.
    for sid, r in by_id.items():
        salary = _num(r.get("salary"))
        years = _num(r.get("years"))
        ppg = _num(r.get("expected_ppg") or r.get("season_ppg"))
        dynasty = _num(r.get("dynasty_asset_score"))
        ce = _num(r.get("contract_efficiency_score"))

        if ce <= 0 and salary > 0:
            production_value = min(ppg * 5, 80)
            asset_value = dynasty * 0.35
            salary_penalty = min(salary * 1.3, 55)
            years_penalty = max(years - 1, 0) * 4

            fallback = max(0, min(100, production_value + asset_value - salary_penalty - years_penalty))
            r["contract_efficiency_score"] = round(fallback, 2)
            r["contract_efficiency_grade"] = r.get("contract_efficiency_grade") or "FALLBACK"

        if _num(r.get("expected_ppg")) <= 0 and ppg > 0:
            r["expected_ppg"] = ppg

    out = []

    for sid, r in by_id.items():
        pos = r.get("pos")
        if pos not in {"QB", "RB", "WR", "TE"}:
            continue

        has_contract = bool(r.get("has_contract", False))
        current_owner = r.get("current_owner")
        market_pool = r.get("market_pool")

        if not market_pool:
            if has_contract:
                market_pool = "TRADE"
            elif _num(r.get("rookie_asset_score")) >= 50:
                market_pool = "ROOKIE_DRAFT"
            else:
                market_pool = "FA_AUCTION"

        summary = (
            f"{r.get('player_name')} ({pos}) — owner={current_owner or 'FA'}, "
            f"contract={'yes' if has_contract else 'no'}, market={market_pool}, "
            f"salary=${_num(r.get('salary')):g}, years={_num(r.get('years')):g}, "
            f"dynasty={_num(r.get('dynasty_asset_score')):.1f}, "
            f"contract_eff={_num(r.get('contract_efficiency_score')):.1f}, "
            f"expected_ppg={_num(r.get('expected_ppg')):.1f}."
        )

        out.append({
            "sleeper_id": sid,
            "canonical_player_id": r.get("canonical_player_id"),
            "gsis_id": r.get("gsis_id"),
            "player_name": r.get("player_name"),
            "search_name": r.get("search_name") or _norm(r.get("player_name")),
            "pos": pos,
            "nfl_team": r.get("nfl_team"),
            "nfl_status": r.get("nfl_status"),
            "active": r.get("active"),

            # ----------------------------
            # Identity
            # ----------------------------
            "college": r.get("college"),
            "draft_year": r.get("draft_year"),
            "draft_round": r.get("draft_round"),
            "draft_pick": r.get("draft_pick"),
            "rookie_class_year": r.get("rookie_class_year"),
            "years_exp": r.get("years_exp"),

            # ---------- Identity ----------
            "college": r.get("college"),
            "draft_year": r.get("draft_year"),
            "draft_round": r.get("draft_round"),
            "draft_pick": r.get("draft_pick"),
            "rookie_class_year": r.get("rookie_class_year"),
            "years_exp": r.get("years_exp"),
            "current_owner": current_owner,
            "roster_status": r.get("roster_status"),
            "has_contract": has_contract,
            "salary": _num(r.get("salary")),
            "years": _num(r.get("years")),
            "contract_total_years": _num(r.get("contract_total_years")),
            "is_rookie_contract": r.get("is_rookie_contract"),
            "market_pool": market_pool,
            "estimated_market_value": _num(r.get("estimated_market_value")),
            "recommended_years": _num(r.get("recommended_years")),
            "dynasty_asset_score": _num(r.get("dynasty_asset_score")),
            "future_projection_score": _num(r.get("future_projection_score")),
            "rookie_asset_score": _num(r.get("rookie_asset_score")),
            "market_consensus_score": _num(r.get("market_consensus_score")),
            "nfl_intelligence_score": _num(r.get("nfl_intelligence_score")),
            "nfl_intelligence_grade": r.get("nfl_intelligence_grade"),
            "nfl_intelligence_flags": r.get("nfl_intelligence_flags") or [],
            "contract_efficiency_score": _num(r.get("contract_efficiency_score")),
            "contract_efficiency_grade": r.get("contract_efficiency_grade"),
            "position_contract_rank": r.get("position_contract_rank"),
            "position_contract_percentile": _num(r.get("position_contract_percentile")),
            "expected_ppg": _num(r.get("expected_ppg")),
            "historical_ppg": _num(r.get("historical_ppg")),
            "latest_season": r.get("latest_season"),
            "latest_week": r.get("latest_week"),
            "latest_week_points": _num(r.get("latest_week_points")),
            "latest_week_ppr": _num(r.get("latest_week_ppr")),
            "season_ppg": _num(r.get("season_ppg")),
            "season_games": _num(r.get("season_games")),
            "player_universe_summary": summary,
            "updated_at": now,
        })

    if out:
        # Clear stale duplicate rows before rebuilding universe.
        sb.table(TARGET_TABLE).delete().neq("sleeper_id", "__never__").execute()
        sb.table(TARGET_TABLE).upsert(out, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(out)} player_universe rows")


if __name__ == "__main__":
    build_player_universe()
