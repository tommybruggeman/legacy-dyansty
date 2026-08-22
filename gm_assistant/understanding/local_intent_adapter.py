from __future__ import annotations

import re
from typing import Any, Dict


POSITIONS = ["QB", "RB", "WR", "TE"]


KNOWN_PLAYERS = [
    "Josh Allen",
    "Jared Goff",
    "Bryce Young",
    "Garrett Wilson",
    "Isiah Pacheco",
    "Cortland Sutton",
    "Brandon Aiyuk",
    "DK Metcalf",
    "Omarion Hampton",
    "Aaron Jones",
    "Kyle Pitts",
    "Jake Ferguson",
    "Marvin Mims",
    "Romeo Doubs",
    "Jalen Milroe",
    "Matthew Golden",
]


def _clean(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip())


def _find_players(question: str) -> list[str]:
    q = question.lower()
    found = []
    for p in KNOWN_PLAYERS:
        if p.lower() in q:
            found.append(p)
    return found


def _find_positions(question: str) -> list[str]:
    q = question.upper()
    found = []
    for pos in POSITIONS:
        if re.search(rf"\b{pos}S?\b", q):
            found.append(pos)
    return found


def understand_locally(question: str) -> Dict[str, Any]:
    q = _clean(question)
    lq = q.lower()

    players = _find_players(q)
    positions = _find_positions(q)

    base = {
        "intent": "UNKNOWN",
        "domain": "general",
        "players": players,
        "positions": positions,
        "action": None,
        "scope": "team",
        "confidence": 0.55,
        "source": "local_intent_adapter",
        "question": question,
        "route_hint": "unknown",
        "needs_player_lookup": bool(players),
        "is_rookie_question": "rookie" in lq or "draft" in lq,
        "is_comparison": " vs " in lq or "compare" in lq,
        "is_value_question": "value" in lq or "worth" in lq,
    }

    if players:
        base["scope"] = "single_player"

    if positions:
        base["scope"] = "position"



    if "trade deadline plan" in lq:
        base.update(intent="TRADE_DEADLINE_PLAN", domain="team", action="deadline_plan", confidence=0.9, route_hint="trade_deadline_plan")
        return base

    if "trade for veterans" in lq:
        base.update(intent="BUY_VETERANS", domain="trade", action="buy_veterans", confidence=0.88, route_hint="buy_veterans")
        return base

    if "sell veterans" in lq:
        base.update(intent="SELL_VETERANS", domain="trade", action="sell_veterans", confidence=0.88, route_hint="sell_veterans")
        return base

    if players and ("should i move" in lq or "move " in lq):
        base.update(intent="PLAYER_TRADE_DECISION", domain="player", action="move", scope="single_player", confidence=0.88, route_hint="player_trade_decision")
        return base

    if "move on" in lq:
        base.update(intent="MOVE_CANDIDATES", domain="roster", action="move", confidence=0.88, route_hint="move_candidates")
        return base

    if "trade first" in lq or "which qb should i trade" in lq:
        base.update(intent="TRADE_CANDIDATES", domain="trade", action="trade_candidates", confidence=0.88, route_hint="trade_candidates")
        return base

    if "not trade" in lq:
        base.update(intent="PROTECT_PLAYERS", domain="roster", action="protect", confidence=0.88, route_hint="protect_players")
        return base

    if "roster clogger" in lq or "cloggers" in lq:
        base.update(intent="ROSTER_CLOGGERS", domain="roster", action="churn", confidence=0.9, route_hint="roster_cloggers")
        return base

    if "churn" in lq or "droppable" in lq or "bench player" in lq or "should i cut" in lq or lq == "who should i cut?":
        base.update(intent="CUT_RECOMMENDATIONS", domain="roster", action="cut", confidence=0.9, route_hint="cut_recommendations")
        return base

    if "trade package" in lq or "package" in lq or "what kind of trade" in lq:
        base.update(intent="TRADE_PACKAGE", domain="trade", action="trade_package", confidence=0.88, route_hint="trade_package")
        return base

    if "trade picks" in lq or "trade my rookie pick" in lq:
        base.update(intent="TRADE_PICKS_STRATEGY", domain="trade", action="trade_picks", confidence=0.88, route_hint="trade_picks")
        return base

    if "all-in" in lq or "all in" in lq:
        base.update(intent="ALL_IN_STRATEGY", domain="team", action="all_in", confidence=0.88, route_hint="all_in_strategy")
        return base

    if "positions should i upgrade" in lq or "biggest roster hole" in lq or "how do i fix" in lq:
        base.update(intent="POSITION_UPGRADES", domain="team", action="upgrade", confidence=0.86, route_hint="position_upgrades")
        return base

    if "depth pieces" in lq or "bench useful" in lq:
        base.update(intent="DEPTH_REVIEW", domain="roster", action="depth_review", confidence=0.86, route_hint="depth_review")
        return base

    if "least valuable" in lq:
        base.update(intent="LEAST_VALUABLE_PLAYER", domain="roster", action="least_value", confidence=0.86, route_hint="least_valuable_player")
        return base

    if "best production" in lq:
        base.update(intent="BEST_PRODUCTION", domain="production", action="best_production", confidence=0.86, route_hint="best_production")
        return base

    if "worst production" in lq:
        base.update(intent="WORST_PRODUCTION", domain="production", action="worst_production", confidence=0.86, route_hint="worst_production")
        return base

    if "above contract" in lq:
        base.update(intent="OUTPRODUCING_CONTRACT", domain="production", action="above_contract", confidence=0.86, route_hint="outproducing_contract")
        return base

    if "below contract" in lq:
        base.update(intent="UNDERPRODUCING_CONTRACT", domain="production", action="below_contract", confidence=0.86, route_hint="underproducing_contract")
        return base

    if "cheap contract should i protect" in lq or "sneaky hold" in lq:
        base.update(intent="PROTECT_PLAYERS", domain="roster", action="protect", confidence=0.86, route_hint="protect_players")
        return base

    if any(x in lq for x in ["should i trade", "trade "]):
        base.update(intent="PLAYER_TRADE_DECISION", domain="player", action="trade", confidence=0.82, route_hint="player_trade_decision")
        return base

    if any(x in lq for x in ["should i cut", "drop ", "cut "]):
        base.update(intent="PLAYER_CUT_DECISION", domain="player", action="cut", confidence=0.82, route_hint="player_cut_decision")
        return base

    if any(x in lq for x in ["should i keep", "should i hold", "not trade", "protect"]):
        base.update(intent="PLAYER_HOLD_DECISION", domain="player", action="hold", confidence=0.82, route_hint="player_hold_decision")
        return base

    if "what should i ask for" in lq or "ask for" in lq:
        base.update(intent="TRADE_RETURN_VALUE", domain="trade", action="trade_return", confidence=0.86, route_hint="trade_return_value")
        return base

    if "sell high" in lq:
        base.update(intent="SELL_HIGH", domain="trade", action="sell", confidence=0.86, route_hint="sell_high")
        return base

    if "sneaky hold" in lq:
        base.update(intent="SNEAKY_HOLD", domain="player", action="hold", confidence=0.86, route_hint="sneaky_hold")
        return base

    if "3 step plan" in lq or "plan" in lq or "next move" in lq or "this week" in lq:
        base.update(intent="GM_PLAN", domain="team", action="plan", confidence=0.84, route_hint="gm_plan")
        return base

    if "summary" in lq or "what would you do" in lq:
        base.update(intent="GM_SUMMARY", domain="team", action="summary", confidence=0.82, route_hint="gm_summary")
        return base

    if "question should i be asking" in lq:
        base.update(intent="QUESTION_RECOMMENDATION", domain="team", action="question_recommendation", confidence=0.9, route_hint="question_recommendation")
        return base

    if "blind spot" in lq:
        base.update(intent="BLIND_SPOT", domain="team", action="blind_spot", confidence=0.9, route_hint="blind_spot")
        return base

    if "overpaid" in lq or "worst contract" in lq or "hurting me" in lq:
        base.update(intent="CONTRACT_AUDIT", domain="contract", action="audit", confidence=0.84, route_hint="contract_audit")
        return base

    if "best contract" in lq or "underpaid" in lq or "cheap contract" in lq:
        base.update(intent="CONTRACT_BEST_VALUE", domain="contract", action="best_value", confidence=0.84, route_hint="contract_best_value")
        return base

    if "fallback" in lq or "unreliable data" in lq or "source work" in lq or "production confidence" in lq:
        base.update(intent="DATA_QUALITY_REVIEW", domain="data", action="data_quality", confidence=0.9, route_hint="data_quality_review")
        return base

    if "production" in lq or "producing" in lq:
        base.update(intent="PRODUCTION_REVIEW", domain="production", action="production_review", confidence=0.82, route_hint="production_review")
        return base

    if positions:
        base.update(intent="POSITION_REVIEW", domain="position", action="review", confidence=0.8, route_hint="position_review")
        return base

    return base
