from __future__ import annotations

from datetime import datetime, timezone
from collections import defaultdict

from auth import service_client


TARGET_TABLE = "league_graph"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _team_profile(players):
    by_pos = defaultdict(list)

    for p in players:
        by_pos[p.get("pos")].append(p)

    pos_summary = {}

    for pos in ["QB", "RB", "WR", "TE"]:
        group = by_pos.get(pos, [])
        group_sorted = sorted(
            group,
            key=lambda p: (
                _num(p.get("expected_ppg")),
                _num(p.get("dynasty_asset_score")),
                _num(p.get("contract_efficiency_score")),
            ),
            reverse=True,
        )

        starters = group_sorted[:3 if pos in {"RB", "WR"} else 2]

        pos_summary[pos] = {
            "count": len(group),
            "top_players": [p.get("player_name") for p in group_sorted[:4]],
            "top_ppg": round(sum(_num(p.get("expected_ppg")) for p in starters), 2),
            "avg_dynasty": _avg([_num(p.get("dynasty_asset_score")) for p in group]),
            "avg_contract": _avg([_num(p.get("contract_efficiency_score")) for p in group]),
            "salary_total": round(sum(_num(p.get("salary")) for p in group), 2),
        }

    strengths = []
    needs = []

    # Superflex: QB strength requires two useful starters.
    if pos_summary["QB"]["top_ppg"] >= 34 and pos_summary["QB"]["count"] >= 2:
        strengths.append("QB")
    elif pos_summary["QB"]["count"] < 2 or pos_summary["QB"]["top_ppg"] < 28:
        needs.append("QB")

    # RB: require real top-3 usable production. This should expose weak RB rooms.
    if pos_summary["RB"]["top_ppg"] >= 38:
        strengths.append("RB")
    elif pos_summary["RB"]["top_ppg"] < 30:
        needs.append("RB")

    # WR: deeper position, so only mark need if top-3 is meaningfully weak.
    if pos_summary["WR"]["top_ppg"] >= 45:
        strengths.append("WR")
    elif pos_summary["WR"]["top_ppg"] < 35:
        needs.append("WR")

    # TE: strength requires a real weekly edge, not just bodies.
    if pos_summary["TE"]["top_ppg"] >= 15:
        strengths.append("TE")
    elif pos_summary["TE"]["top_ppg"] < 10:
        needs.append("TE")

    expensive_contracts = sorted(
        players,
        key=lambda p: (
            _num(p.get("salary")),
            -_num(p.get("contract_efficiency_score")),
        ),
        reverse=True,
    )[:5]

    trade_chips = sorted(
        players,
        key=lambda p: (
            _num(p.get("dynasty_asset_score")) * 0.5
            + _num(p.get("expected_ppg")) * 2
            + _num(p.get("contract_efficiency_score")) * 0.15
        ),
        reverse=True,
    )[:8]

    return {
        "pos_summary": pos_summary,
        "strengths": strengths,
        "needs": needs,
        "expensive_contracts": [
            {
                "player": p.get("player_name"),
                "pos": p.get("pos"),
                "salary": _num(p.get("salary")),
                "years": _num(p.get("years")),
                "contract": _num(p.get("contract_efficiency_score")),
            }
            for p in expensive_contracts
        ],
        "trade_chips": [
            {
                "player": p.get("player_name"),
                "pos": p.get("pos"),
                "salary": _num(p.get("salary")),
                "years": _num(p.get("years")),
                "ppg": _num(p.get("expected_ppg")),
                "dynasty": _num(p.get("dynasty_asset_score")),
                "contract": _num(p.get("contract_efficiency_score")),
            }
            for p in trade_chips
        ],
    }


def build_league_graph():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    players = (
        sb.table("player_graph")
        .select("*")
        .not_.is_("current_owner", "null")
        .execute()
        .data
        or []
    )

    teams = defaultdict(list)
    for p in players:
        owner = p.get("current_owner")
        if owner:
            teams[owner].append(p)

    out = []

    for owner, roster in teams.items():
        profile = _team_profile(roster)

        out.append({
            "owner_team_name": owner,
            "player_count": len(roster),
            "strengths": profile["strengths"],
            "needs": profile["needs"],
            "pos_summary": profile["pos_summary"],
            "expensive_contracts": profile["expensive_contracts"],
            "trade_chips": profile["trade_chips"],
            "league_graph_summary": (
                f"{owner}: strengths={profile['strengths']}, needs={profile['needs']}, "
                f"players={len(roster)}."
            ),
            "updated_at": now,
        })

    if out:
        sb.table(TARGET_TABLE).delete().neq("owner_team_name", "__never__").execute()
        sb.table(TARGET_TABLE).upsert(out, on_conflict="owner_team_name").execute()

    print(f"Upserted {len(out)} league_graph rows")


if __name__ == "__main__":
    build_league_graph()
