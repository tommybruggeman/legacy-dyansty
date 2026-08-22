from __future__ import annotations

from gm_assistant.skills.base import SkillResult


def _roster_exit_base(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_roster_exit_decision
    return answer_roster_exit_decision(question, owner_team_name, understanding)


def _team_base(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_team_review
    return answer_team_review(question, owner_team_name, understanding)


def _trade_base(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_trade_strategy
    return answer_trade_strategy(question, owner_team_name, understanding)


def _rewrite(base: dict, decision: str, old: str, new: str) -> dict:
    summary = base.get("summary", "")
    if old in summary:
        summary = summary.replace(old, new)
    base["decision"] = decision
    base["summary"] = summary
    return base


def answer_move_candidates(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _roster_exit_base(question, owner_team_name, understanding)
    return _rewrite(
        base,
        "MOVE_CANDIDATES",
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My move-candidate board is based on contract burden, age/contract risk, production, and asset value.",
    )


def answer_cut_recommendations(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _roster_exit_base(question, owner_team_name, understanding)
    return _rewrite(
        base,
        "CUT_RECOMMENDATIONS",
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My cut/churn board separates bad contracts from truly replaceable roster spots.",
    )


def answer_trade_candidates(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _roster_exit_base(question, owner_team_name, understanding)
    return _rewrite(
        base,
        "TRADE_CANDIDATES",
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My trade-candidate board ranks players to market-check first, not players to dump.",
    )


def answer_roster_cloggers(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _roster_exit_base(question, owner_team_name, understanding)
    return _rewrite(
        base,
        "ROSTER_CLOGGERS",
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My roster-clogger board looks for players whose roster spot, contract, role, or market value may not justify holding.",
    )


def answer_protect_players(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.gm_brain import _contract_best_value_answer
    base = _contract_best_value_answer(question, owner_team_name)
    base["decision"] = "PROTECT_PLAYERS"
    base["summary"] = base.get("summary", "").replace(
        "Best contract values:",
        "Players/contracts I would protect first:"
    )
    return base


def answer_trade_package(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _trade_base(question, owner_team_name, understanding)
    base["decision"] = "TRADE_PACKAGE"
    base["summary"] = base.get("summary", "").replace(
        "Trade strategy: I would build from leverage first, not from panic.",
        "Trade package framework: build from leverage first, not from panic."
    )
    return base


def answer_trade_picks(question: str, owner_team_name: str, understanding: dict) -> dict:
    summary = """My pick-trade rule: only trade picks if they turn into a clear weekly lineup upgrade.

I would trade picks when:
1. You are buying a real RB/TE starter.
2. The player helps your current contention window.
3. The contract does not create a future cap trap.

I would not trade picks for lateral depth. If the deal does not move your starting lineup, keep the flexibility."""
    return SkillResult("TRADE_PICKS_STRATEGY", summary, 0.78).to_dict()


def answer_all_in_strategy(question: str, owner_team_name: str, understanding: dict) -> dict:
    summary = """I would consider an all-in move only if it upgrades a true weekly weak spot.

The all-in checklist:
1. Do not move Josh Allen unless the return is overwhelming.
2. Use QB/WR surplus before touching your core.
3. Target RB or TE help that changes your starting lineup.
4. Avoid paying premium prices for aging depth.

My lean: targeted contention push, not reckless all-in."""
    return SkillResult("ALL_IN_STRATEGY", summary, 0.8).to_dict()


def answer_position_upgrades(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _team_base(question, owner_team_name, understanding)
    base["decision"] = "POSITION_UPGRADES"
    base["summary"] = base.get("summary", "").replace(
        "Here is my GM read on this roster using the current graph context.",
        "My upgrade priority starts with where the roster has the least weekly/cap leverage."
    )
    return base


def answer_depth_review(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _roster_exit_base(question, owner_team_name, understanding)
    return _rewrite(
        base,
        "DEPTH_REVIEW",
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My depth review separates useful insurance from players who are mostly occupying roster/cap space.",
    )


def answer_production_review(question: str, owner_team_name: str, understanding: dict) -> dict:
    from gm_assistant.roster_understanding_answer import answer_position_review
    base = answer_position_review(question, owner_team_name, {"positions": []})
    base["decision"] = str(understanding.get("intent") or "PRODUCTION_REVIEW")
    base["summary"] = base.get("summary", "").replace(
        "Lean: use this group to identify where you have weekly strength, movable depth, and replacement-level roster spots.",
        "Lean: production is useful, but I would still cross-check source confidence before making trade or cut decisions."
    )
    return base


def answer_sell_veterans(question: str, owner_team_name: str, understanding: dict) -> dict:
    base = _roster_exit_base(question, owner_team_name, understanding)
    return _rewrite(
        base,
        "SELL_VETERANS",
        "I understood this as a roster-exit question, so I ranked your actual roster by move pressure: contract burden, age/contract risk, production, and asset value.",
        "My veteran-sell board is based on age risk, contract pressure, replaceability, and whether selling now improves your future flexibility.",
    )


def answer_trade_deadline_plan(question: str, owner_team_name: str, understanding: dict) -> dict:
    summary = """My trade-deadline plan:

1. **Buy only where it changes the lineup.**
   Prioritize RB or TE help if the player becomes a real starter, not just depth.

2. **Market-check expensive or fragile contracts.**
   Do not dump value, but listen on players whose contract risk could hurt your next window.

3. **Protect your elite QB advantage.**
   Josh Allen should not be part of a normal deadline move. Use surplus QB/WR depth first.

4. **Keep picks unless they buy weekly points.**
   Picks should move only for a clear playoff-impact starter.

My lean: targeted buyer, not reckless all-in."""
    from gm_assistant.skills.base import SkillResult
    return SkillResult("TRADE_DEADLINE_PLAN", summary, 0.84).to_dict()
