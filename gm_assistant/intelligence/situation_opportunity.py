from __future__ import annotations

import pandas as pd


def _num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def build_situation_opportunity(ctx: dict | None) -> dict:
    ctx = ctx or {}
    my_roster = ctx.get("my_roster", pd.DataFrame()).copy()
    team_summary = ctx.get("team_summary", {}) or {}

    if my_roster.empty:
        return {
            "status": "no_roster",
            "summary": "No roster data available.",
            "opportunities": [],
            "risks": [],
            "recommended_actions": [],
        }

    cap_used = _num(team_summary.get("cap_used"))
    cap_limit = _num(team_summary.get("salary_cap"), 225)
    cap_space = cap_limit - cap_used

    roster_size = len(my_roster)

    pos_counts = (
        my_roster["pos"].fillna("UNK").value_counts().to_dict()
        if "pos" in my_roster.columns
        else {}
    )

    opportunities = []
    risks = []
    recommended_actions = []

    if cap_space >= 25:
        opportunities.append({
            "type": "cap_space",
            "label": "Aggressive buyer window",
            "reason": f"You have roughly ${cap_space:.1f} in cap space.",
            "priority": "high",
        })
        recommended_actions.append(
            "Use cap space as a weapon: target expensive veterans or salary-stressed teams."
        )
    elif cap_space <= 5:
        risks.append({
            "type": "cap_pressure",
            "label": "Limited flexibility",
            "reason": f"You only have roughly ${cap_space:.1f} in cap space.",
            "priority": "high",
        })
        recommended_actions.append(
            "Look for salary dumps, 2-for-1 consolidation trades, or expendable bench cuts."
        )
    else:
        opportunities.append({
            "type": "balanced_cap",
            "label": "Moderate flexibility",
            "reason": f"You have roughly ${cap_space:.1f} in cap space.",
            "priority": "medium",
        })

    if roster_size >= 22:
        risks.append({
            "type": "roster_crunch",
            "label": "Roster crunch",
            "reason": f"You are carrying {roster_size} players.",
            "priority": "medium",
        })
        recommended_actions.append(
            "Package depth before waivers or rookie additions force uncomfortable cuts."
        )

    qb_count = pos_counts.get("QB", 0)
    rb_count = pos_counts.get("RB", 0)
    wr_count = pos_counts.get("WR", 0)
    te_count = pos_counts.get("TE", 0)

    if qb_count < 3:
        risks.append({
            "type": "superflex_qb_depth",
            "label": "Superflex QB fragility",
            "reason": f"You only have {qb_count} QBs.",
            "priority": "high",
        })
        recommended_actions.append(
            "Add a cheaper QB2/QB3 before bye weeks or injuries inflate prices."
        )

    if rb_count >= 7:
        opportunities.append({
            "type": "rb_depth",
            "label": "RB trade leverage",
            "reason": f"You have {rb_count} RBs.",
            "priority": "medium",
        })

    if wr_count < 5:
        risks.append({
            "type": "wr_depth",
            "label": "Thin WR room",
            "reason": f"You only have {wr_count} WRs.",
            "priority": "medium",
        })

    if te_count <= 1:
        risks.append({
            "type": "te_depth",
            "label": "No TE insulation",
            "reason": f"You only have {te_count} TE listed.",
            "priority": "low",
        })

    if not recommended_actions:
        recommended_actions.append(
            "Hold core assets, monitor league pressure points, and wait for a specific buy/sell opening."
        )

    return {
        "status": "ok",
        "summary": "Situation and opportunity scan completed.",
        "cap": {
            "cap_limit": cap_limit,
            "cap_used": cap_used,
            "cap_space": cap_space,
        },
        "roster": {
            "roster_size": roster_size,
            "pos_counts": pos_counts,
        },
        "opportunities": opportunities,
        "risks": risks,
        "recommended_actions": recommended_actions,
    }


def format_situation_opportunity(report: dict) -> str:
    if report.get("status") != "ok":
        return report.get("summary", "No situation report available.")

    cap = report.get("cap", {})
    roster = report.get("roster", {})

    lines = [
        "## Situation & Opportunity Intelligence",
        "",
        f"You are using ${cap.get('cap_used', 0):.1f} of ${cap.get('cap_limit', 0):.1f}, leaving roughly ${cap.get('cap_space', 0):.1f}.",
        f"Roster size: {roster.get('roster_size', 0)} players.",
        f"Position counts: {roster.get('pos_counts', {})}.",
        "",
        "### Opportunities",
    ]

    for item in report.get("opportunities", []):
        lines.append(f"- **{item['label']}**: {item['reason']}")

    lines.append("")
    lines.append("### Risks")

    for item in report.get("risks", []):
        lines.append(f"- **{item['label']}**: {item['reason']}")

    lines.append("")
    lines.append("### Recommended next moves")

    for action in report.get("recommended_actions", []):
        lines.append(f"- {action}")

    return "\n".join(lines)
