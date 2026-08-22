from __future__ import annotations

from typing import Any

from gm_assistant.engines.trade_constructor_engine import write_trade_lanes, write_trade_partner_recommendation


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def write_gm_answer(evidence: dict[str, Any]) -> dict[str, Any]:
    intent = evidence.get("intent")
    player = evidence.get("player") or {}
    facts = evidence.get("facts") or []
    owner = evidence.get("owner_team_name")

    if player:
        if intent == "cut_decision":
            summary = _write_cut(evidence)
        elif intent == "trade_decision":
            summary = _write_trade(evidence)
        elif intent == "hold_decision":
            summary = _write_hold(evidence)
        else:
            summary = _write_player_general(evidence)
    else:
        summary = _write_team_general(evidence)

    return {
        "answer_type": "conversational_gm_answer",
        "intent": intent,
        "owner_team_name": owner,
        "player": player,
        "facts": facts,
        "summary": summary,
    }


def _write_cut(evidence: dict[str, Any]) -> str:
    p = evidence["player"]

    name = p.get("player_name")
    salary = _num(p.get("salary"))
    years = _num(p.get("years"))
    contract = _num(p.get("contract_efficiency_score"))
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))
    trade = _num(
        p.get("trade_value_score")
        or p.get("market_value_score")
        or p.get("dynasty_trade_value")
        or p.get("dynasty_asset_score")
    )
    dead = round(salary * years * 0.5, 1)

    if dynasty >= 55 or trade >= 45:
        rec = "I would not cut him."
        why = "The contract can be a problem and the player can still have real trade value. Those are different things."
        action = "Shop him first. If the market is dead, then revisit the cut conversation."
    elif salary <= 5 and ppg < 6 and dynasty < 30:
        rec = "Yes — I would be comfortable cutting him."
        why = "That profile is more replaceable, and the roster spot/flexibility probably matters more than waiting."
        action = "Cut or churn unless you have a specific reason to stash him."
    elif contract < 30 and salary >= 15:
        rec = "I would not cut him as the first move."
        why = "This looks more like a bad-contract management problem than a pure cut."
        action = "Market-check him, look for a salary dump, or try to convert him into a cheaper positional need."
    else:
        rec = "I would hold for now."
        why = "There is not enough evidence that cutting creates more value than keeping the option open."
        action = "Keep him available in trade talks, but do not force the move."

    return (
        f"{rec}\n\n"
        f"On **{name}**, my read is: {why}\n\n"
        f"He is at **${salary:g}** with **{years:g} years** left. "
        f"Contract efficiency is **{contract:.1f}**, dynasty score is **{dynasty:.1f}**, "
        f"trade value is **{trade:.1f}**, and expected PPG is around **{ppg:.1f}**. "
        f"The rough dead-cap exposure is about **${dead:g}** total.\n\n"
        f"My GM move: **{action}**"
    )


def _write_trade(evidence: dict[str, Any]) -> str:
    p = evidence["player"]

    name = p.get("player_name")
    pos = p.get("pos")
    salary = _num(p.get("salary"))
    years = _num(p.get("years"))
    contract = _num(p.get("contract_efficiency_score"))
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))
    trade = _num(
        p.get("trade_value_score")
        or p.get("market_value_score")
        or p.get("dynasty_trade_value")
        or p.get("dynasty_asset_score")
    )

    if dynasty >= 60 and contract >= 45:
        stance = "I would not actively shop him unless someone is overpaying."
        target = "Only move him for a clear premium."
    elif dynasty >= 50 and contract < 35:
        stance = "I would quietly shop him."
        target = "You are trying to sell the name/player value before the contract drags down your flexibility."
    elif trade < 30:
        stance = "I would not expect a strong market."
        target = "He is probably more useful as a hold, throw-in, or salary-balancing piece."
    else:
        stance = "I would market-check him."
        target = "The goal is to see whether another manager prices him above your internal value."

    return (
        f"{stance}\n\n"
        f"For **{name}** ({pos}), the important distinction is player value vs. contract value. "
        f"He has a dynasty score of **{dynasty:.1f}**, trade value of **{trade:.1f}**, "
        f"contract efficiency of **{contract:.1f}**, and he costs **${salary:g}/{years:g} yrs**.\n\n"
        f"My GM move: **{target}** "
        f"I would be looking for RB/TE help, cheaper production, cap relief, or picks depending on the other manager."
    )


def _write_hold(evidence: dict[str, Any]) -> str:
    p = evidence["player"]

    name = p.get("player_name")
    salary = _num(p.get("salary"))
    years = _num(p.get("years"))
    contract = _num(p.get("contract_efficiency_score"))
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))

    if dynasty >= 55 and contract < 35:
        stance = "Hold him, but not blindly."
        reason = "The talent/value is real, but the contract means you should always know the market."
    elif contract >= 55:
        stance = "Yes — he is a clean hold."
        reason = "The contract is doing enough work that you do not need to force action."
    else:
        stance = "He is a soft hold."
        reason = "I would not dump him, but I would include him in the right deal."

    return (
        f"{stance}\n\n"
        f"On **{name}**, {reason} He is at **${salary:g}/{years:g} yrs**, "
        f"with contract efficiency **{contract:.1f}**, dynasty score **{dynasty:.1f}**, "
        f"and expected PPG around **{ppg:.1f}**.\n\n"
        f"My GM move: keep the asset, but let price dictate whether he becomes movable."
    )


def _write_player_general(evidence: dict[str, Any]) -> str:
    p = evidence["player"]

    name = p.get("player_name")
    salary = _num(p.get("salary"))
    years = _num(p.get("years"))
    contract = _num(p.get("contract_efficiency_score"))
    dynasty = _num(p.get("dynasty_asset_score"))
    ppg = _num(p.get("expected_ppg") or p.get("season_ppg"))

    return (
        f"On **{name}**, I see a real decision point rather than a simple yes/no.\n\n"
        f"He is at **${salary:g}/{years:g} yrs**, with contract efficiency **{contract:.1f}**, "
        f"dynasty score **{dynasty:.1f}**, and expected PPG around **{ppg:.1f}**.\n\n"
        f"My read: do not treat the player and the contract as the same thing. "
        f"If the player still has market value, shop before cutting. If the contract is fair, hold unless the deal improves your roster direction."
    )


def _write_team_general(evidence: dict[str, Any]) -> str:
    facts = evidence.get("facts") or []
    intent = evidence.get("intent")
    owner = evidence.get("owner_team_name")

    liabilities = [f for f in facts if f.get("kind") == "roster_liability"]
    top_liability = liabilities[0] if liabilities else None

    def liability_lines(limit=5):
        if not liabilities:
            return []
        return [f"- {f.get('text')}" for f in liabilities[:limit]]

    if intent == "team_overview":
        lines = [
            "You look like a **soft retool / bubble team**.",
            "",
            "The honest read: your QB room gives you a real foundation, but the roster is not balanced enough yet to blindly push all-in. RB and TE are the pressure points, and the cap/value tension is mostly coming from expensive skill players who still have name value.",
            "",
            "So I would not panic. But I also would not sit still.",
        ]

        if liabilities:
            lines += ["", "The first pressure points I would review:"] + liability_lines(5)

        lines += [
            "",
            "My GM move: keep the QB foundation intact, market-check the inefficient contracts, and try to turn WR/QB surplus into RB/TE help or cheaper production."
        ]
        return "\n".join(lines)

    if intent == "team_risk_analysis":
        lines = [
            "What scares me is not that your roster is bad. It is that the roster can get stuck in the middle.",
            "",
            "You have enough QB value to avoid a full rebuild, but RB and TE are thin enough that you may not have a clean weekly edge unless you fix them. The risk is paying contender prices while still having retool-level weak spots.",
        ]

        if liabilities:
            lines += ["", "The contracts/players that create the most risk:"] + liability_lines(4)

        lines += [
            "",
            "My GM read: do not chase expensive RB fixes. Fix the roster through value trades, cheap production, rookie upside, and selective consolidation."
        ]
        return "\n".join(lines)

    if intent == "gm_takeover_plan":
        lines = [
            "If I took this team over, I would not tear it down.",
            "",
            "My plan would be: protect the QB base, clean up the inefficient contracts, and aggressively hunt RB/TE value. This is a soft retool, not a rebuild.",
            "",
            "First 3 moves:",
            "1. Market-check the biggest inefficient contracts instead of cutting real asset value.",
            "2. Use WR/QB surplus to chase RB/TE help or future picks.",
            "3. Keep cheap producers because they let you stay competitive while fixing the cap."
        ]

        if top_liability:
            d = top_liability.get("data") or {}
            lines += [
                "",
                f"The first call I would make is on **{d.get('player')}**. Not to dump him — to see whether the league values him above your contract-adjusted number."
            ]

        return "\n".join(lines)

    if intent == "contract_cleanup":
        lines = [
            "The contracts that should make you uncomfortable are not automatically the most expensive ones. They are the ones where salary, years, and production do not line up.",
        ]

        if liabilities:
            lines += ["", "I would start here:"] + liability_lines(5)

        lines += [
            "",
            "My rule: if the player still has dynasty/name value, shop before cutting. If the player has no market and no weekly role, churn the spot."
        ]
        return "\n".join(lines)

    if intent == "contention_check":
        return (
            "You can stay competitive, but I would not call this a clean title team yet.\n\n"
            "The QB room keeps you dangerous in superflex. The problem is that RB and TE do not currently give you enough weekly leverage, and some of your cap is tied up in inefficient contracts.\n\n"
            "My answer: you are a **bubble contender / soft retool**. Try to improve, but do not burn premium future value unless the move clearly fixes RB or TE."
        )

    if intent == "team_direction":
        return (
            "I would not fully rebuild this.\n\n"
            "You have enough QB foundation to avoid a teardown. The better path is a soft retool: keep the cornerstone value, move inefficient contracts when the market lets you, and add cheaper RB/TE production.\n\n"
            "The mistake would be acting like a desperate contender or a full rebuilder. You are in the middle, so the edge is patience plus selective aggression."
        )

    if intent == "blind_spot":
        lines = [
            "Your biggest blind spot is probably treating player quality and roster fit as the same thing.",
            "",
            "Some of your players are good assets, but not all of them are good fits at their current contracts. Garrett Wilson is the clean example: useful dynasty value, but a tough contract. Pacheco is another pressure point because the RB contract/production profile is uncomfortable.",
            "",
            "The move is not to dump good players. The move is to ask: who does the league value more than my roster should?"
        ]
        return "\n".join(lines)

    if intent == "do_nothing_projection":
        return (
            "If you do nothing, I think you stay competitive but probably capped.\n\n"
            "The QB room keeps your floor high, but RB and TE are still likely to cost you weekly ceiling. The bigger issue is opportunity cost: every week you wait, expensive inefficient contracts can become harder to move.\n\n"
            "Doing nothing is not fatal. But it probably means you are betting on internal improvement instead of actively fixing the roster imbalance."
        )

    if intent == "five_year_plan":
        return (
            "My five-year plan would be simple: build around durable QB value and stop paying premium prices for fragile production.\n\n"
            "Year 1: clean up contract inefficiencies and add cheap RB/TE points.\n"
            "Year 2: consolidate surplus into one or two weekly difference-makers.\n"
            "Years 3-5: keep the QB room strong, cycle RBs cheaply, and use picks as flexibility rather than trophies.\n\n"
            "This team should not be managed like a rebuild. It should be managed like a portfolio: protect the assets that compound and move the ones that decay."
        )

    if intent == "overvalued_players":
        lines = [
            "The player type I would be careful overvaluing is the expensive name who still feels like a core asset but is not giving you enough weekly or contract-adjusted return.",
            "",
            "From the current evidence, the first names I would sanity-check are:"
        ]
        if liabilities:
            lines += liability_lines(4)
        lines += [
            "",
            "That does not mean sell them for cheap. It means stop valuing them only by name and start valuing them by what they do for this roster."
        ]
        return "\n".join(lines)

    if intent == "most_valuable_player":
        return (
            "Your most valuable player is still **Josh Allen**.\n\n"
            "In a superflex league, elite QB value is the hardest thing to replace. Even with the salary, he gives you weekly ceiling, long-term stability, and trade leverage. Garrett Wilson and Omarion Hampton matter, but Allen is the franchise anchor."
        )

    if intent == "trade_ideas":
        return write_trade_lanes(owner)

    if intent == "bench_cut":
        lines = [
            "For bench cuts, I would not start with name-value players. I would start with players who have low salary, low weekly role, and limited trade market.",
        ]
        churn = [f for f in liabilities if "churn" in str((f.get("data") or {}).get("action", ""))]
        if churn:
            lines += ["", "First churn candidates:"] + [f"- {f.get('text')}" for f in churn[:5]]
        elif liabilities:
            lines += ["", "Closest candidates from the current evidence:"] + liability_lines(5)
        lines += ["", "My rule: shop real assets first, cut replacement-level bench players first."]
        return "\n".join(lines)

    if intent == "trade_partner":
        return write_trade_partner_recommendation(owner)

    if intent == "acquire_player_plan":
        target = evidence.get("player_name") or "that player"
        return (
            f"If you want **{target}**, I would treat it like a premium acquisition, not a casual buy.\n\n"
            "The way to get a true difference-maker is usually one of three paths:\n"
            "1. Send a premium name plus a smaller asset.\n"
            "2. Use QB value in superflex to force the conversation.\n"
            "3. Offer flexibility: picks, salary relief, or a 2-for-1 that helps the other team reset.\n\n"
            "But I would only do it if the move fixes your roster shape. Do not buy a star just to buy a star."
        )

    if intent == "counterargument":
        return (
            "The argument against trading Garrett Wilson is this: you might be blaming the contract for a roster-construction problem.\n\n"
            "Garrett is expensive, yes. But he is still one of your few players with real dynasty name value. If you move him just because the contract is uncomfortable, you risk turning a premium asset into a short-term patch.\n\n"
            "I would trade him only if the return clearly fixes RB/TE or gives you meaningful cap/pick flexibility. Otherwise, hold and let the market come to you."
        )

    if intent == "next_move" and top_liability:
        d = top_liability.get("data") or {}
        lines = [
            f"Your next move should be to **market-check {d.get('player')}**.",
            "",
            f"That does not mean dump him. It means he is the clearest contract/value pressure point: **${d.get('salary'):g}/{d.get('years'):g} yrs**, contract **{d.get('contract'):.1f}**, dynasty **{d.get('dynasty'):.1f}**, PPG **{d.get('ppg'):.1f}**.",
            "",
            "The play is to see whether another manager still values the name/player more than your internal contract-adjusted model does.",
        ]
        if liabilities:
            lines += ["", "The first pressure points I would review:"] + liability_lines(5)
        lines += ["", "My GM move: protect real dynasty value, shop expensive inefficient contracts before cutting them, and turn surplus into RB/TE help, cheaper production, picks, or cap flexibility."]
        return "\n".join(lines)

    lines = ["Here is how I would read this from a GM lens."]
    if liabilities:
        lines += ["", "The first pressure points I would review:"] + liability_lines(5)
    lines += ["", "My GM move: protect real dynasty value, shop expensive inefficient contracts before cutting them, and turn surplus into RB/TE help, cheaper production, picks, or cap flexibility."]
    return "\n".join(lines)


def _player_sort_value(p: dict) -> float:
    return (
        _num(p.get("dynasty_asset_score")) * 0.45
        + _num(p.get("expected_ppg") or p.get("season_ppg")) * 2.5
        + _num(p.get("contract_efficiency_score")) * 0.25
    )
