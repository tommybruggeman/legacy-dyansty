from __future__ import annotations

from typing import Any

from auth import service_client


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _owned_players(owner_team_name: str) -> list[dict[str, Any]]:
    sb = service_client()

    try:
        rows = (
            sb.table("player_graph")
            .select("*")
            .eq("current_owner", owner_team_name)
            .execute()
            .data
            or []
        )
    except Exception:
        rows = (
            sb.table("player_universe")
            .select("*")
            .eq("current_owner", owner_team_name)
            .execute()
            .data
            or []
        )

    return rows



def _league_graph() -> list[dict[str, Any]]:
    sb = service_client()

    try:
        return sb.table("league_graph").select("*").execute().data or []
    except Exception:
        return []


def _trade_partner_fits(owner_team_name: str, send_pos: str, target_needs: list[str] | None = None) -> list[dict[str, Any]]:
    teams = [t for t in _league_graph() if t.get("owner_team_name") != owner_team_name]
    target_needs = target_needs or ["RB", "TE"]

    fits = []

    for t in teams:
        strengths = set(t.get("strengths") or [])
        needs = set(t.get("needs") or [])
        pos_summary = t.get("pos_summary") or {}

        score = 0
        reasons = []

        if send_pos in needs:
            score += 35
            reasons.append(f"needs {send_pos}")

        for need in target_needs:
            if need in strengths:
                score += 20
                reasons.append(f"has {need} strength")

        if send_pos == "QB":
            qb = pos_summary.get("QB") or {}
            if _num(qb.get("top_ppg")) < 30:
                score += 20
                reasons.append("could use superflex QB stability")

        if send_pos == "WR":
            wr = pos_summary.get("WR") or {}
            if _num(wr.get("top_ppg")) < 40:
                score += 12
                reasons.append("could use WR production")

        if score > 0:
            fits.append({
                "team": t.get("owner_team_name"),
                "score": score,
                "reasons": reasons,
                "strengths": list(strengths),
                "needs": list(needs),
            })

    return sorted(fits, key=lambda x: x["score"], reverse=True)[:3]


def _player_value(p: dict[str, Any]) -> float:
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))
    contract = _num(p.get("contract_efficiency_score"))
    salary = _num(p.get("salary"))

    value = dynasty * 0.45 + min(ppg * 4, 80) * 0.35 + contract * 0.20

    if salary >= 30 and contract < 35:
        value -= 10
    elif salary >= 20 and contract < 35:
        value -= 6

    return round(max(value, 0), 1)


def _tier(value: float) -> str:
    if value >= 70:
        return "premium anchor"
    if value >= 55:
        return "strong starter"
    if value >= 42:
        return "movable starter"
    if value >= 30:
        return "depth asset"
    return "throw-in"


def _role(p: dict[str, Any]) -> str:
    pos = p.get("pos")
    salary = _num(p.get("salary"))
    contract = _num(p.get("contract_efficiency_score"))
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))

    if dynasty >= 65 or (pos == "QB" and ppg >= 18):
        return "core_hold"

    if salary >= 20 and contract < 35 and dynasty >= 45:
        return "name_value_shop"

    if contract >= 65 and salary <= 12 and ppg >= 8:
        return "cheap_value_hold"

    if pos in {"QB", "WR"} and dynasty >= 35:
        return "surplus_leverage"

    if salary <= 5 and dynasty < 35:
        return "throw_in_or_churn"

    return "neutral"


def build_trade_lanes(owner_team_name: str) -> dict[str, Any]:
    players = _owned_players(owner_team_name)

    evaluated = []
    for p in players:
        value = _player_value(p)
        evaluated.append({
            "player": p.get("player_name"),
            "pos": p.get("pos"),
            "salary": _num(p.get("salary")),
            "years": _num(p.get("years")),
            "expected_ppg": _num(p.get("expected_ppg") or p.get("season_ppg")),
            "dynasty": _num(p.get("dynasty_asset_score")),
            "contract": _num(p.get("contract_efficiency_score")),
            "value": value,
            "tier": _tier(value),
            "role": _role(p),
        })

    evaluated = sorted(evaluated, key=lambda x: x["value"], reverse=True)

    core = [p for p in evaluated if p["role"] == "core_hold"]
    shops = [p for p in evaluated if p["role"] == "name_value_shop"]
    surplus = [p for p in evaluated if p["role"] == "surplus_leverage"]
    cheap_holds = [p for p in evaluated if p["role"] == "cheap_value_hold"]
    churn = [p for p in evaluated if p["role"] == "throw_in_or_churn"]

    lanes = []

    if shops:
        p = shops[0]
        partners = _trade_partner_fits(owner_team_name, p["pos"], ["RB", "TE"])
        lanes.append({
            "lane": "Name-value reset",
            "send": [p["player"]],
            "target_return": "RB/TE starter plus cap relief, or a future 1st/2nd value package",
            "partners": partners,
            "why": (
                f"{p['player']} still has dynasty/name value, but the contract score "
                f"({p['contract']:.1f}) is not matching the salary (${p['salary']:g})."
            ),
            "risk": "Do not sell for pennies; this only works if another manager pays for the name.",
            "confidence": "medium-high",
        })

    qb_surplus = [p for p in surplus if p["pos"] == "QB"]
    if qb_surplus:
        p = qb_surplus[0]
        partners = _trade_partner_fits(owner_team_name, p["pos"], ["RB", "TE", "WR"])
        lanes.append({
            "lane": "Superflex leverage",
            "send": [p["player"]],
            "target_return": "starting RB, TE upgrade, or 2026/2027 1st value",
            "partners": partners,
            "why": (
                f"In superflex, usable QBs create leverage. {p['player']} can be more valuable "
                "to a QB-needy team than he is to your weekly lineup."
            ),
            "risk": "Do not weaken your QB foundation unless the return fixes a real roster weakness.",
            "confidence": "medium",
        })

    wr_surplus = [p for p in surplus if p["pos"] == "WR"]
    if wr_surplus:
        p = wr_surplus[0]
        partners = _trade_partner_fits(owner_team_name, p["pos"], ["RB", "TE"])
        lanes.append({
            "lane": "WR surplus conversion",
            "send": [p["player"]],
            "target_return": "cheaper RB production, TE help, or pick insulation",
            "partners": partners,
            "why": (
                f"{p['player']} gives you a movable WR asset. Your roster needs are more likely "
                "to be solved by converting WR value into RB/TE value."
            ),
            "risk": "WR depth is useful; only move it if the return changes your starting lineup or flexibility.",
            "confidence": "medium",
        })

    if cheap_holds:
        p = cheap_holds[0]
        lanes.append({
            "lane": "Do-not-sell-cheap value",
            "send": [p["player"]],
            "target_return": "only move as part of a premium consolidation",
            "why": (
                f"{p['player']} is cheap relative to expected production. That kind of contract helps "
                "you stay competitive while retooling."
            ),
            "risk": "Selling cheap value contracts can make the cap problem worse.",
            "confidence": "high",
        })

    if len(shops) >= 2:
        p1, p2 = shops[0], shops[1]
        lanes.append({
            "lane": "Two-for-one cleanup",
            "send": [p1["player"], p2["player"]],
            "target_return": "one cleaner weekly starter or a starter plus pick",
            "why": (
                "This is a consolidation path: turn two uncomfortable contracts into one cleaner asset."
            ),
            "risk": "Two-for-ones only work if the incoming player is a clear roster fit.",
            "confidence": "medium",
        })

    return {
        "answer_type": "trade_lanes",
        "owner_team_name": owner_team_name,
        "players": evaluated,
        "core_holds": core[:5],
        "shop_candidates": shops[:5],
        "surplus_candidates": surplus[:5],
        "cheap_holds": cheap_holds[:5],
        "churn_candidates": churn[:5],
        "lanes": lanes[:5],
    }


def write_trade_lanes(owner_team_name: str) -> str:
    data = build_trade_lanes(owner_team_name)
    lanes = data.get("lanes") or []

    if not lanes:
        return (
            "I do not see enough clean trade lanes yet. I would start by auditing contract pressure, "
            "team needs, and which teams need QB/WR help."
        )

    lines = [
        "I would not start by throwing random offers out. I would build from trade lanes.",
        "",
    ]

    for i, lane in enumerate(lanes[:3], 1):
        lines.append(f"**Trade lane {i}: {lane['lane']}**")
        lines.append(f"- Send: **{', '.join(lane['send'])}**")
        lines.append(f"- Ask for: {lane['target_return']}")
        lines.append(f"- Why: {lane['why']}")
        partners = lane.get("partners") or []
        if partners:
            partner_txt = "; ".join(
                f"{x['team']} ({', '.join(x.get('reasons') or [])})"
                for x in partners[:3]
            )
            lines.append(f"- Best partner fits: {partner_txt}")
        lines.append(f"- Risk: {lane['risk']}")
        lines.append("")

    lines.append(
        "My GM read: the goal is not just to win a trade calculator. The goal is to fix roster shape: "
        "RB/TE help, cleaner cap, or future flexibility without breaking your QB foundation."
    )

    return "\n".join(lines)



def write_trade_partner_recommendation(owner_team_name: str) -> str:
    data = build_trade_lanes(owner_team_name)
    lanes = data.get("lanes") or []

    partner_scores = {}

    for lane in lanes:
        for p in lane.get("partners") or []:
            team = p.get("team")
            if not team:
                continue

            rec = partner_scores.setdefault(team, {
                "team": team,
                "score": 0,
                "lanes": [],
                "reasons": set(),
            })

            rec["score"] += p.get("score", 0)
            rec["lanes"].append(lane.get("lane"))
            for reason in p.get("reasons") or []:
                rec["reasons"].add(reason)

    ranked = sorted(partner_scores.values(), key=lambda x: x["score"], reverse=True)

    if not ranked:
        return (
            "I do not have a clean trade partner yet. The archetype I would target is a team that needs QB/WR help "
            "and has RB/TE depth, picks, or cap flexibility."
        )

    lines = [
        "The first teams I would call are the ones where your surplus matches their roster shape.",
        "",
    ]

    for i, r in enumerate(ranked[:3], 1):
        lanes = ", ".join(dict.fromkeys(r["lanes"]))
        reasons = ", ".join(sorted(r["reasons"]))
        lines.append(f"{i}. **{r['team']}**")
        lines.append(f"   - Fit: {reasons}")
        lines.append(f"   - Best lanes: {lanes}")
        lines.append("")

    lines.append(
        "My GM read: do not shop everyone to everyone. Start with these teams because the league graph says "
        "they are more likely to value what you can afford to move."
    )

    return "\n".join(lines)
