from __future__ import annotations

from typing import Any


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def reason_from_planned_evidence(evidence: dict[str, Any]) -> str | None:
    route = evidence.get("route")

    if route == "rookie_draft":
        return _reason_rookie_draft(evidence)

    if route == "target_recommendation":
        return _reason_targets(evidence)

    if route == "free_agency":
        return _reason_free_agency(evidence)

    if route == "acquire_player":
        return _reason_acquire_player(evidence)

    if route == "cut_or_churn":
        return _reason_cut_or_churn(evidence)

    return None


def _reason_rookie_draft(evidence: dict[str, Any]) -> str:
    rookies = evidence.get("rookie_targets") or []
    needs = evidence.get("team_needs") or []
    roster = evidence.get("roster") or []

    qbs = [p for p in roster if p.get("pos") == "QB"]
    rbs = [p for p in roster if p.get("pos") == "RB"]
    tes = [p for p in roster if p.get("pos") == "TE"]

    top = rookies[:5]

    best_rb = next((p for p in rookies if p.get("pos") == "RB"), None)
    best_wr = next((p for p in rookies if p.get("pos") == "WR"), None)
    best_qb = next((p for p in rookies if p.get("pos") == "QB"), None)

    lines = [
        "For **1.02**, I would not just take the second name on the board. I would make the pick solve your roster.",
        "",
        f"Your current roster shape says: QB depth is strong, RB/TE still matter most. You have **{len(qbs)} QBs**, **{len(rbs)} RBs**, and **{len(tes)} TEs** rostered.",
        "",
    ]

    if best_rb:
        lines.append(
            f"My first fit-based target would be **{best_rb.get('player_name')}** if he is there. "
            f"He is the top RB profile in this rookie graph: prospect score **{_num(best_rb.get('prospect_score')):.1f}**, "
            f"tier **{best_rb.get('prospect_tier')}**, role: {best_rb.get('fantasy_role') or 'unknown'}."
        )
        lines.append("")

    if best_qb:
        lines.append(
            f"**{best_qb.get('player_name')}** may be the market-value play, especially in Superflex, "
            f"but for your roster I would only take a QB if the league values him like a premium asset. "
            "You already have enough QB foundation, so QB at 1.02 is more about trade value than need."
        )
        lines.append("")

    if best_wr:
        lines.append(
            f"**{best_wr.get('player_name')}** is the WR pivot. I would consider him if the RB tier dries up "
            "or if you think he becomes a true alpha value."
        )
        lines.append("")

    lines.append("My board for **your team**:")
    ranked = []
    if best_rb:
        ranked.append(best_rb)
    if best_wr and best_wr not in ranked:
        ranked.append(best_wr)
    if best_qb and best_qb not in ranked:
        ranked.append(best_qb)

    for p in top:
        if p not in ranked:
            ranked.append(p)

    for i, p in enumerate(ranked[:5], 1):
        lines.append(
            f"{i}. **{p.get('player_name')}** ({p.get('pos')}) — "
            f"prospect {_num(p.get('prospect_score')):.1f}, {p.get('prospect_tier')}"
        )

    lines += [
        "",
        "My GM recommendation: if the best RB is available, I lean RB because it fixes the roster. "
        "If the QB goes 1.01 and the RB tier is clean, take the RB. If a manager overpays for 1.02 because they need QB, I would listen hard.",
    ]

    return "\n".join(lines)


def _reason_targets(evidence: dict[str, Any]) -> str:
    targets = evidence.get("targets") or []
    needs = evidence.get("team_needs") or []

    if not targets:
        return "I do not see clean target candidates yet. That means the next step is improving league/player graph coverage, not forcing a generic name."

    top = targets[:6]
    best = top[0]

    lines = [
        "Now we’re past archetypes. These are the actual players I would start with.",
        "",
        f"Your roster needs point toward **{', '.join(needs)}**, but the target has to be gettable. "
        "I want a player who helps your weekly lineup without costing the full future.",
        "",
        f"My favorite first call: **{best.get('player_name')}** from **{best.get('owner_team_name')}**.",
        f"He gives you **{best.get('pos')}** help with expected PPG **{_num(best.get('expected_ppg')):.1f}**, "
        f"dynasty score **{_num(best.get('dynasty_asset_score')):.1f}**, and contract score **{_num(best.get('contract_efficiency_score')):.1f}**.",
        "",
        "My target list:",
    ]

    for p in top:
        lines.append(
            f"- **{p.get('player_name')}** ({p.get('pos')}, {p.get('owner_team_name')}) — "
            f"PPG {_num(p.get('expected_ppg')):.1f}, dynasty {_num(p.get('dynasty_asset_score')):.1f}, "
            f"contract {_num(p.get('contract_efficiency_score')):.1f}, ${_num(p.get('salary')):g}/{_num(p.get('years')):g} yrs"
        )

    lines += [
        "",
        "My GM move: start with the teams that have RB/TE strength and need QB/WR help. "
        "Do not ask, 'Who is good?' Ask, 'Who helps me and is owned by a manager I can actually trade with?'",
    ]

    return "\n".join(lines)


def _reason_free_agency(evidence: dict[str, Any]) -> str:
    targets = evidence.get("fa_targets") or []
    needs = evidence.get("team_needs") or []

    lines = [
        "Free agency should be a patch, not the core plan.",
        "",
        f"For your roster, I would use FA to add **{', '.join(needs)}** insulation — cheap touches, injury outs, and TE depth.",
        "",
    ]

    if not targets:
        lines.append("I do not see trustworthy FA targets in the graph right now. That is better than forcing bad names.")
        lines.append("")
        lines.append("My GM move: improve FA ownership/eligibility tagging before acting.")
        return "\n".join(lines)

    lines.append("The FA names I would actually consider:")
    for p in targets[:8]:
        lines.append(
            f"- **{p.get('player_name')}** ({p.get('pos')}) — "
            f"PPG {_num(p.get('expected_ppg')):.1f}, dynasty {_num(p.get('dynasty_asset_score')):.1f}, "
            f"confidence {_num(p.get('data_confidence')):.2f}"
        )

    lines += [
        "",
        "My GM recommendation: do not overspend here. Use FA to cover weak spots while you use trades/picks to find real difference-makers.",
    ]

    return "\n".join(lines)


def _reason_acquire_player(evidence: dict[str, Any]) -> str:
    target = evidence.get("target_player_row")
    outgoing = evidence.get("possible_outgoing") or []

    if not target:
        return "I could not find that player cleanly in the graph yet, so I would not build a fake acquisition plan."

    lines = [
        f"If you want **{target.get('player_name')}**, the first question is not price. It is whether he solves the actual roster problem.",
        "",
        f"He is owned by **{target.get('owner_team_name') or 'unknown/FA'}** and profiles as: "
        f"{target.get('pos')}, PPG {_num(target.get('expected_ppg')):.1f}, "
        f"dynasty {_num(target.get('dynasty_asset_score')):.1f}, contract {_num(target.get('contract_efficiency_score')):.1f}.",
        "",
        "The outgoing pieces I would discuss before touching your true core:",
    ]

    for p in outgoing[:6]:
        lines.append(
            f"- **{p.get('player_name')}** ({p.get('pos')}) — "
            f"PPG {_num(p.get('expected_ppg')):.1f}, dynasty {_num(p.get('dynasty_asset_score')):.1f}, "
            f"contract {_num(p.get('contract_efficiency_score')):.1f}"
        )

    lines += [
        "",
        "My GM recommendation: lead with surplus or discomfort, not your foundation. "
        "If the seller needs QB, use that. If they need WR, Garrett/Aiyuk-type value can start the conversation. "
        "Do not include Josh Allen unless you are completely restructuring the franchise.",
    ]

    return "\n".join(lines)


def _reason_cut_or_churn(evidence: dict[str, Any]) -> str:
    cuts = evidence.get("cut_candidates") or []

    lines = [
        "For cuts, I would separate two questions:",
        "",
        "1. Who is a bad contract?",
        "2. Who is actually replaceable?",
        "",
        "You usually do not cut the bad contract first if the player still has market value. You cut the replaceable bench piece first.",
        "",
    ]

    if not cuts:
        lines.append("I do not see clean cut candidates yet.")
        return "\n".join(lines)

    lines.append("My cut/churn board:")
    shown = 0

    for p in cuts:
        if shown >= 6:
            break

        protected = bool(p.get("protected_from_cut"))
        tag = "review only" if protected else "cut/churn candidate"
        reasons = p.get("protect_reasons") or []

        lines.append(
            f"- **{p.get('player_name')}** ({p.get('pos')}) — "
            f"PPG {_num(p.get('expected_ppg')):.1f}, dynasty {_num(p.get('dynasty_asset_score')):.1f}, "
            f"contract {_num(p.get('contract_efficiency_score')):.1f}, churn {_num(p.get('churn_score')):.0f} "
            f"→ **{tag}**"
        )

        if protected and reasons:
            lines.append(f"  - Do not cut blindly: {', '.join(reasons)}")

        shown += 1

    lines += [
        "",
        "My GM recommendation: cut only the low-role, low-confidence, low-market players. "
        "If a player has production, dynasty value, or a cheap efficient contract, shop or hold before cutting.",
    ]

    return "\n".join(lines)
