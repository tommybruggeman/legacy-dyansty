from __future__ import annotations


def compose_target_recommendations(targets: list[dict], *, position: str = "RB") -> str:
    if not targets:
        return "I ran the target scan, but I could not find enough ranked targets yet."

    lines = []
    lines.append(f"Here are the {position} targets I would start with.")
    lines.append("")
    lines.append("I’m ranking these by win-now usefulness, contract fit, and gettability — not just raw name value.")
    lines.append("")

    for i, t in enumerate(targets[:5], 1):
        player = t.get("player")
        pos = t.get("pos")
        owner = t.get("owner") or "FA"
        ppg = float(t.get("ppg") or 0)
        salary = float(t.get("salary") or 0)
        years = float(t.get("years") or 0)
        fit = float(t.get("fit_score") or 0)
        contract = float(t.get("contract_score") or 0)
        why = t.get("why") or "fits the roster need"

        if owner == "FA":
            move = "Add if the role is real; this is a low-cost depth/insulation play."
        else:
            move = "Start with a WR/QB surplus offer or a pick-based opener. Do not overpay unless he enters your playoff lineup."

        lines.append(
            f"{i}. **{player}** ({pos}, {owner}) — "
            f"{ppg:.1f} PPG, ${salary:.0f}/{years:.0f} yrs, "
            f"contract {contract:.1f}, fit {fit:.1f}."
        )
        lines.append(f"   Why: {why}.")
        lines.append(f"   Move: {move}")

    lines.append("")
    lines.append("My GM move: call on the most gettable player first, not necessarily the biggest name. The target has to improve your weekly playoff lineup.")

    return "\n".join(lines)
