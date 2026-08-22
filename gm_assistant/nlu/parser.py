from __future__ import annotations

import re
from gm_assistant.nlu.schema import ParsedGMQuestion


def parse_gm_question(question: str) -> ParsedGMQuestion:
    q = question.lower().strip()
    parsed = ParsedGMQuestion(raw_question=question, intent="unknown")

    parsed.count_requested = extract_count(q)
    parsed.positions = extract_positions(q)
    parsed.player_names = extract_possible_players(question)

    if is_target_search(q, parsed.positions):
        parsed.intent = "target_recommendations"
        parsed.decision_type = "acquire"
        parsed.target_pool = "mixed"
        parsed.needs_roster = True
        parsed.needs_market = True
        parsed.needs_team_fit = True
        parsed.confidence = 0.93
        return parsed

    if ("rb" in parsed.positions or "running back" in q) and any(x in q for x in ["looking for", "add", "recommend", "options", "target"]):
        parsed.intent = "target_recommendations"
        parsed.decision_type = "acquire"
        parsed.target_pool = "mixed"
        parsed.needs_roster = True
        parsed.needs_market = True
        parsed.needs_team_fit = True
        parsed.confidence = 0.9
        return parsed

    if "contract" in q and parsed.player_names:
        parsed.intent = "player_contract_fit"
        parsed.needs_player_lookup = True
        parsed.needs_contracts = True
        parsed.needs_team_fit = True
        parsed.confidence = 0.9
        return parsed

    if any(x in q for x in ["lineup points", "future value", "points or future", "win now or future"]):
        parsed.intent = "strategy_tradeoff"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        parsed.confidence = 0.88
        return parsed

    if any(x in q for x in ["win the championship", "championship this year", "win now", "all in", "contend"]):
        parsed.intent = "change_team_goal"
        parsed.team_goal = "win_now"
        parsed.confidence = 0.96
        return parsed

    if "1.02" in q or "rookie pick" in q:
        parsed.intent = "rookie_pick_fit"
        parsed.target_pool = "rookie"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["free agent", "free agents", "waiver", "waivers"]) or re.search(r"\bfas\b", q):
        parsed.intent = "free_agent_targets"
        parsed.target_pool = "fa"
        parsed.needs_market = True
        parsed.needs_team_fit = True
        parsed.needs_roster = True
        return parsed

    if "points per dollar" in q or "per dollar" in q or "best contract" in q or "best player contracts" in q:
        parsed.intent = "contract_value_ranking"
        parsed.is_league_wide = True
        parsed.needs_contracts = True
        parsed.needs_market = True
        return parsed

    if ("rb" in parsed.positions or "running back" in q) and any(x in q for x in ["looking for", "add", "recommend", "target", "options"]):
        parsed.intent = "target_recommendations"
        parsed.decision_type = "acquire"
        parsed.target_pool = "mixed"
        parsed.needs_roster = True
        parsed.needs_market = True
        parsed.needs_team_fit = True
        parsed.confidence = 0.9
        return parsed

    if "fair" in q and "trade" in q:
        parsed.intent = "trade_package"
        parsed.decision_type = "trade"
        parsed.needs_player_lookup = bool(parsed.player_names)
        parsed.needs_market = True
        parsed.needs_team_fit = True
        return parsed

    if ("rb" in parsed.positions or "running back" in q) and any(x in q for x in ["target", "add", "recommend", "options"]):
        parsed.intent = "target_recommendations"
        parsed.decision_type = "acquire"
        parsed.target_pool = "mixed"
        parsed.needs_roster = True
        parsed.needs_market = True
        parsed.needs_team_fit = True
        return parsed

    if "contract" in q and parsed.player_names:
        parsed.intent = "player_contract_fit"
        parsed.needs_player_lookup = True
        parsed.needs_contracts = True
        parsed.needs_team_fit = True
        return parsed

    if "contracts" in q or "bad contracts" in q or "hurting" in q:
        parsed.intent = "contract_audit"
        parsed.needs_roster = True
        parsed.needs_contracts = True
        return parsed

    if any(x in q for x in ["players should i not trade", "should not trade", "untouchable", "do not trade", "core players"]):
        parsed.intent = "core_player_review"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["best win-now players", "best win now players", "worst win-now players", "worst win now players"]):
        parsed.intent = "win_now_player_ranking"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["type of rb", "rb should i target", "running back should i target"]):
        parsed.intent = "rb_archetype"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["type of te", "te should i target", "tight end should i target"]):
        parsed.intent = "te_archetype"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["use picks", "trade picks", "use draft picks", "spend picks"]):
        parsed.intent = "pick_strategy"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["qb for rb", "quarterback for rb", "qb depth", "trade a qb"]):
        parsed.intent = "qb_surplus_strategy"
        parsed.needs_roster = True
        parsed.needs_market = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["teams should i call", "who should i call", "trade partners", "which managers"]):
        parsed.intent = "trade_partner_search"
        parsed.needs_market = True
        parsed.needs_roster = True
        return parsed

    if any(x in q for x in ["too focused on trading", "too trade focused", "do i need to trade"]):
        parsed.intent = "non_trade_paths"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["safest path", "safe path", "low risk path"]):
        parsed.intent = "safe_path"
        parsed.needs_roster = True
        return parsed

    if any(x in q for x in ["aggressive path", "all-in path", "all in path"]):
        parsed.intent = "aggressive_path"
        parsed.needs_roster = True
        return parsed

    if any(x in q for x in ["one move", "first move", "move i should make first"]):
        parsed.intent = "first_move"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["cut", "drop", "release"]):
        parsed.intent = "player_drop_decision"
        parsed.decision_type = "cut"
        parsed.needs_player_lookup = True
        parsed.needs_contracts = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["trade", "shop", "move", "sell", "hold"]):
        parsed.intent = "player_trade_decision"
        parsed.decision_type = "trade"
        parsed.needs_player_lookup = bool(parsed.player_names)
        parsed.needs_contracts = True
        parsed.needs_market = True
        parsed.needs_team_fit = True
        return parsed

    if any(x in q for x in ["how does my team look", "stack up", "where do i stand"]):
        parsed.intent = "team_overview"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if "weakness" in q or "weak spot" in q or "biggest need" in q:
        parsed.intent = "team_needs"
        parsed.needs_roster = True
        parsed.needs_team_fit = True
        return parsed

    if "strength" in q:
        parsed.intent = "team_strengths"
        parsed.needs_roster = True
        return parsed

    parsed.intent = "unknown"
    return parsed


def extract_count(q: str) -> int | None:
    m = re.search(r"\b(\d+)\b", q)
    if m:
        return int(m.group(1))
    if "five" in q:
        return 5
    if "top two" in q or "top 2" in q:
        return 2
    return None


def extract_positions(q: str) -> list[str]:
    positions = []
    mapping = {
        "qb": "QB",
        "quarterback": "QB",
        "rb": "RB",
        "running back": "RB",
        "wr": "WR",
        "receiver": "WR",
        "te": "TE",
        "tight end": "TE",
    }
    for key, val in mapping.items():
        if re.search(rf"\b{re.escape(key)}\b", q):
            positions.append(val)
    return sorted(set(positions))


def extract_possible_players(question: str) -> list[str]:
    known_markers = [
        "Garrett Wilson",
        "Isiah Pacheco",
        "Josh Allen",
        "Jared Goff",
        "Kyle Pitts",
        "Aaron Jones",
        "DK Metcalf",
        "Bryce Young",
    ]
    return [p for p in known_markers if p.lower() in question.lower()]



def is_target_search(q: str, positions: list[str]) -> bool:
    wants_position = "RB" in positions or "TE" in positions or "WR" in positions or "QB" in positions or "running back" in q
    wants_add = any(x in q for x in ["looking for", "add", "recommend", "options", "target", "who do you recommend"])
    no_named_player = True
    return wants_position and wants_add and no_named_player
