TEST_CASES = [
    {
        "name": "garrett_wilson_contract_followup",
        "owner": "Tommy Bruggeman",
        "questions": [
            "Should I trade Garrett Wilson?",
            "Why?",
            "I'm trying to win now. Does that change your answer?",
            "What if I could get Breece Hall?",
            "Would you cut him if no one trades for him?",
            "What would have to happen for him to become worth his contract?",
        ],
        "expected_checks": [
            "contract",
            "salary",
            "win-now",
            "trade",
            "cut",
        ],
    },
    {
        "name": "qb_depth_to_rb_upgrade",
        "owner": "Tommy Bruggeman",
        "questions": [
            "How can I use my QB depth to upgrade RB?",
            "Who should I target?",
            "Which owner is most likely to need a QB?",
            "Build me one realistic trade.",
            "What would make the other owner say yes?",
        ],
        "expected_checks": [
            "QB",
            "RB",
            "target",
            "trade",
            "other owner",
        ],
    },
    {
        "name": "contender_vs_rebuilder_flip",
        "owner": "Tommy Bruggeman",
        "questions": [
            "What should my team do if I am contending?",
            "Now answer as if I am rebuilding.",
            "Which players change from hold to sell?",
            "Which contracts become dangerous?",
            "What is the first move you would make?",
        ],
        "expected_checks": [
            "contend",
            "rebuild",
            "sell",
            "contract",
            "move",
        ],
    },
]
