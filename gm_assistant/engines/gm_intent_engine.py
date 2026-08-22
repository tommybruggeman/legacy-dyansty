from __future__ import annotations


def classify_gm_intent(question: str) -> str:
    q = (question or "").lower()

    if any(x in q for x in ["next move", "what now", "next step", "where do we go from here"]):
        return "next_move"

    if any(x in q for x in ["how does my team look", "how does team look", "evaluate my team", "roster look"]):
        return "team_overview"

    if any(x in q for x in ["what scares you", "risk", "worried", "danger", "concern"]):
        return "team_risk_analysis"

    if any(x in q for x in ["if you owned this team", "if you were me", "what would you do", "assistant gm", "take over"]):
        return "gm_takeover_plan"

    if any(x in q for x in ["overvalue", "too attached", "overrate"]):
        return "overvalued_players"

    if any(x in q for x in ["undervalue", "sleeping on", "underappreciate"]):
        return "undervalued_players"

    if any(x in q for x in ["uncomfortable", "worst contract", "bad contract", "contracts make me"]):
        return "contract_cleanup"

    if any(x in q for x in ["rebuilding", "rebuild", "retool", "tear it down"]):
        return "team_direction"

    if any(x in q for x in ["can i win", "win this year", "contend", "title odds", "championship"]):
        return "contention_check"

    if any(x in q for x in ["blind spot", "missing", "not seeing"]):
        return "blind_spot"

    if any(x in q for x in ["give me three trades", "trade ideas", "trade packages", "build me a trade"]):
        return "trade_ideas"

    if any(x in q for x in ["i want", "how do i get", "go get", "acquire"]):
        return "acquire_player_plan"

    if any(x in q for x in ["bench should i cut", "bench cut", "who should i cut", "who should i drop"]):
        return "bench_cut"

    if any(x in q for x in ["do nothing", "stand pat", "if i make no moves"]):
        return "do_nothing_projection"

    if any(x in q for x in ["five years", "5 years", "long term plan", "franchise plan"]):
        return "five_year_plan"

    if any(x in q for x in ["which team", "trade with", "trade partner"]):
        return "trade_partner"

    if any(x in q for x in ["most valuable player", "best asset", "franchise player"]):
        return "most_valuable_player"

    if any(x in q for x in ["convince me not", "talk me out of"]):
        return "counterargument"

    if any(x in q for x in ["cut", "drop", "release", "move on from"]):
        return "cut_decision"

    if any(x in q for x in ["trade", "sell", "shop"]):
        return "trade_decision"

    if any(x in q for x in ["hold", "keep"]):
        return "hold_decision"

    return "general_gm_question"
