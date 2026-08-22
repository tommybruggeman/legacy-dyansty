from __future__ import annotations

from collections import defaultdict


def _num(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _money(value) -> str:
    return f"${_num(value):g}"


def _top(rows, key, limit=5, reverse=True):
    return sorted(
        rows,
        key=lambda r: _num(r.get(key)),
        reverse=reverse,
    )[:limit]


def build_team_intelligence(rows: list[dict]) -> dict:
    rows = rows or []

    if not rows:
        return {
            "status": "empty",
            "summary": "No roster asset data available.",
        }

    total_salary = sum(_num(r.get("salary")) for r in rows)
    roster_size = len(rows)

    by_pos = defaultdict(list)
    for r in rows:
        by_pos[r.get("pos") or "UNK"].append(r)

    pos_summary = {}
    for pos, players in by_pos.items():
        pos_summary[pos] = {
            "count": len(players),
            "salary": round(sum(_num(p.get("salary")) for p in players), 2),
            "avg_asset": round(
                sum(_num(p.get("asset_value_score")) for p in players)
                / max(len(players), 1),
                2,
            ),
            "best": _top(players, "asset_value_score", 1)[0].get("player_name"),
            "weakest": _top(players, "asset_value_score", 1, reverse=False)[0].get("player_name"),
        }

    core = _top(rows, "asset_value_score", 6)
    best_contracts = _top(rows, "contract_value_score", 6)
    worst_contracts = _top(rows, "contract_value_score", 6, reverse=False)

    sell_candidates = [
        r for r in rows
        if str(r.get("asset_recommendation") or "").upper() in {"SELL", "CUT / SELL"}
    ]

    young_upside = [
        r for r in rows
        if _num(r.get("age"), 99) <= 24
        and _num(r.get("asset_value_score")) >= 35
    ]

    aging_risk = [
        r for r in rows
        if (
            (r.get("pos") == "RB" and _num(r.get("age")) >= 27)
            or (r.get("pos") == "WR" and _num(r.get("age")) >= 30)
            or (r.get("pos") == "TE" and _num(r.get("age")) >= 31)
            or (r.get("pos") == "QB" and _num(r.get("age")) >= 36)
        )
    ]

    qb_count = len(by_pos.get("QB", []))
    rb_count = len(by_pos.get("RB", []))
    wr_count = len(by_pos.get("WR", []))
    te_count = len(by_pos.get("TE", []))

    risks = []
    opportunities = []

    if qb_count < 3:
        risks.append("Superflex QB depth is fragile.")
    if wr_count < 5:
        risks.append("WR depth is thin for a dynasty roster.")
    if te_count <= 1:
        risks.append("TE depth has little insulation.")
    if aging_risk:
        risks.append(
            "There is age-curve risk with "
            + ", ".join(r.get("player_name", "Unknown") for r in aging_risk[:4])
            + "."
        )

    if rb_count >= 7:
        opportunities.append("RB depth can be used as trade leverage.")
    if young_upside:
        opportunities.append(
            "Young upside base includes "
            + ", ".join(r.get("player_name", "Unknown") for r in _top(young_upside, "asset_value_score", 4))
            + "."
        )
    if sell_candidates:
        opportunities.append(
            "There are movable sell/cut candidates: "
            + ", ".join(r.get("player_name", "Unknown") for r in sell_candidates[:5])
            + "."
        )

    return {
        "status": "ok",
        "roster_size": roster_size,
        "total_salary": round(total_salary, 2),
        "position_summary": pos_summary,
        "core": core,
        "best_contracts": best_contracts,
        "worst_contracts": worst_contracts,
        "sell_candidates": sell_candidates[:8],
        "young_upside": _top(young_upside, "asset_value_score", 8),
        "aging_risk": _top(aging_risk, "age", 8),
        "risks": risks,
        "opportunities": opportunities,
    }


def format_gm_orchestration(question: str, intel: dict) -> str:
    if intel.get("status") != "ok":
        return intel.get("summary", "No team intelligence available.")

    core_names = ", ".join(
        r.get("player_name", "Unknown")
        for r in intel.get("core", [])[:5]
    )

    weak_names = ", ".join(
        f"{r.get('player_name', 'Unknown')} ({_money(r.get('salary'))}/{r.get('years', 0)} yrs)"
        for r in intel.get("worst_contracts", [])[:5]
    )

    lines = []

    lines.append("## GM Read")
    lines.append("")
    lines.append(
        f"You have {intel['roster_size']} rostered players carrying about {_money(intel['total_salary'])} in salary."
    )

    if core_names:
        lines.append(f"Your current core is built around: **{core_names}**.")

    lines.append("")
    lines.append("### Situation")
    for pos, data in sorted(intel.get("position_summary", {}).items()):
        lines.append(
            f"- **{pos}**: {data['count']} players, {_money(data['salary'])} total salary, "
            f"best asset: {data['best']}, weakest spot: {data['weakest']}."
        )

    lines.append("")
    lines.append("### Opportunities")
    opportunities = intel.get("opportunities") or ["No obvious league-breaking opportunity yet. Stay patient and wait for a team-specific pressure point."]
    for item in opportunities:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("### Risks")
    risks = intel.get("risks") or ["No major structural roster risk detected."]
    for item in risks:
        lines.append(f"- {item}")

    lines.append("")
    lines.append("### First moves I would consider")
    lines.append(f"- Shop or package the weakest contract/value spots first: {weak_names}.")
    lines.append("- Preserve your best young/core assets unless the deal clearly tiers you up.")
    lines.append("- Use position depth to target another team's weakness instead of making isolated 1-for-1 swaps.")

    return "\n".join(lines)
