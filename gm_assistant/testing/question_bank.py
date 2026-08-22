QUESTION_BANK = [

# ------------------------------------------------------------------
# TEAM STRATEGY
# ------------------------------------------------------------------

{
    "category": "Team Strategy",
    "expected_intent": "team_overview",
    "questions": [
        "How does my team look?",
        "Evaluate my roster.",
        "Give me an honest assessment of my team."
    ]
},

{
    "category": "Roster Weakness",
    "expected_intent": "team_needs",
    "questions": [
        "What is my biggest weakness?",
        "Where is my roster weakest?",
        "What should I improve first?"
    ]
},

# ------------------------------------------------------------------
# ROOKIE
# ------------------------------------------------------------------

{
    "category": "Rookie Pick",

    "expected_intent":"rookie_draft_pick_decision",

    "questions":[

        "I have pick 1.02. Who should I draft?",

        "Who fits my team best at 1.02?",

        "With the second pick who should I take?"
    ]
},

{
    "category":"Trade Down",

    "expected_intent":"rookie_draft_pick_decision",

    "questions":[

        "Should I trade down from 1.02?",

        "Would you move the second pick?",

        "Is keeping 1.02 my best move?"
    ]
},

# ------------------------------------------------------------------
# CONTRACTS
# ------------------------------------------------------------------

{
    "category":"Contracts",

    "expected_intent":"contract_pain_analysis",

    "questions":[

        "Which contracts are hurting me?",

        "What are my worst contracts?",

        "Where am I wasting cap space?"
    ]
},

]
