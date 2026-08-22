INTENT_REGISTRY = {
    "rookie_draft_pick_decision": {
        "keywords": ["draft", "rookie", "pick", "1.01", "1.02", "1.03", "second pick", "first pick"],
        "required_context": [
            "rookie_draft_board",
            "team_future_context",
            "roster_strength_context",
            "contract_context",
        ],
        "answer_shape": "ranked_rookie_options",
    },
    "trade_target_search": {
        "keywords": ["trade for", "target", "upgrade", "buy", "package"],
        "required_context": [
            "player_universe",
            "team_future_context",
            "roster_strength_context",
            "contract_context",
        ],
        "answer_shape": "trade_targets",
    },
    "contract_pain_analysis": {
        "keywords": ["contract", "hurting", "overpaid", "drop", "dead cap", "cut"],
        "required_context": [
            "player_universe",
            "contract_context",
            "team_future_context",
        ],
        "answer_shape": "contract_rankings",
    },
    "free_agent_targets": {
        "keywords": ["free agent", "fa", "waiver", "available"],
        "required_context": [
            "player_universe",
            "team_future_context",
            "roster_strength_context",
        ],
        "answer_shape": "free_agent_targets",
    },
    "team_direction": {
        "keywords": ["team look", "rebuild", "contend", "direction", "strategy"],
        "required_context": [
            "team_future_context",
            "roster_strength_context",
            "contract_context",
        ],
        "answer_shape": "team_strategy",
    },
}
