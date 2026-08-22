from __future__ import annotations


def _is_best_value_question(question: str) -> bool:
    q = (question or "").lower()
    return any(x in q for x in [
        "best value",
        "best contract",
        "best contracts",
        "value contract",
        "most efficient",
        "points per dollar",
        "ppd",
    ])


def compose_contract_value_answer(data: dict, *, question: str) -> str:
    rows = data.get("points_per_dollar") or data.get("contracts") or data.get("rows") or []

    if _is_best_value_question(question):
        ranked = sorted(
            rows,
            key=lambda r: (
                float(r.get("points_per_dollar") or 0),
                float(r.get("ppg") or r.get("season_ppg") or 0),
            ),
            reverse=True,
        )[:8]

        if not ranked:
            return "I could not find enough contract data to rank your best values yet."

        lines = [
            "Your best contract values are the players giving you the most usable production per dollar.\n"
        ]

        for i, r in enumerate(ranked, 1):
            lines.append(
                f"{i}. {r.get('player') or r.get('player_name')} ({r.get('pos', '-')}) — "
                f"${r.get('salary')}/yr, {r.get('years')} yrs, "
                f"{round(float(r.get('ppg') or r.get('season_ppg') or 0), 2)} PPG, "
                f"{round(float(r.get('points_per_dollar') or 0), 2)} pts/$."
            )

        lines.append(
            "\nLean: these are the contracts I would be careful not to throw into deals casually unless they are clearly upgrading your weekly lineup."
        )
        return "\n".join(lines)

    # Default: liability / hurting contracts.
    liabilities = data.get("liabilities") or data.get("contract_liabilities") or rows

    ranked = sorted(
        liabilities,
        key=lambda r: float(r.get("liability") or r.get("liability_score") or r.get("contract_risk") or 0),
        reverse=True,
    )[:5]

    if not ranked:
        return "I could not find enough contract data to identify the contracts hurting you most yet."

    lines = [
        "The contracts hurting you most are the ones combining real salary, multiple years, and weak weekly return.\n"
    ]

    for r in ranked:
        name = r.get("player") or r.get("player_name")
        salary = r.get("salary")
        years = r.get("years")
        liability = r.get("liability") or r.get("liability_score") or r.get("contract_risk") or 0
        action = r.get("recommended_action") or r.get("action") or "monitor"

        lines.append(
            f"{name}: ${salary}/{years} yrs, liability {round(float(liability or 0))}, "
            f"recommended action: {action}."
        )

    return "\n\n".join(lines)
