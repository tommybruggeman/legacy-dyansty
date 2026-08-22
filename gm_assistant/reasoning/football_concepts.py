from __future__ import annotations


def answer_football_concept(question: str, owner_team_name: str):
    q = str(question or "").lower()

    if "offensive line" in q or "line matter" in q:
        return {
            "answer_type": "football_concept",
            "conversation_mode": "explain",
            "owner_team_name": owner_team_name,
            "summary": (
                "Offensive line matters a lot for RB valuation, but it should not be treated as the whole answer.\n\n"
                "For a player like Hampton, I would use offensive line as a multiplier on opportunity. "
                "If the role is strong and the line is good, the floor rises fast because efficient touches become easier. "
                "If the line is bad, the player can still hit, but he needs more receiving work, goal-line volume, or elite talent to overcome it.\n\n"
                "So in the engine, OL should influence: rushing efficiency, touchdown expectation, weekly floor, and confidence in rookie projection."
            ),
        }

    if "coaching" in q or "coach" in q or "scheme" in q:
        return {
            "answer_type": "football_concept",
            "conversation_mode": "explain",
            "owner_team_name": owner_team_name,
            "summary": (
                "Coaching should matter a lot for rookies, especially RBs and TEs.\n\n"
                "For Jeanty, coaching is part of the opportunity thesis. If the staff wants to run the ball, invested premium capital in him, and has a scheme that can feature one back, that raises his projection before he ever plays an NFL snap.\n\n"
                "But coaching should not override talent or draft capital by itself. I would treat it as a context multiplier: it can push a strong prospect into a great fantasy bet, but it should not turn a weak prospect into a great asset alone."
            ),
        }

    if "draft capital" in q:
        return {
            "answer_type": "football_concept",
            "conversation_mode": "explain",
            "owner_team_name": owner_team_name,
            "summary": (
                "Draft capital should matter most before we have NFL production.\n\n"
                "For rookies, draft capital is one of the best signals we have because it tells us how much the NFL valued the player and how likely the team is to give him opportunity. "
                "For established veterans, it should fade into the background because actual NFL production, role, durability, and efficiency become much stronger evidence.\n\n"
                "So the engine should weigh draft capital heavily for Jeanty or Hampton, lightly for early-career players, and almost not at all for someone like Josh Allen now."
            ),
        }

    return {
        "answer_type": "football_concept",
        "conversation_mode": "explain",
        "owner_team_name": owner_team_name,
        "summary": (
            "This is a football-context question, so I would answer it by separating talent, opportunity, scheme, role security, and contract value. "
            "The key is knowing which evidence matters most for that player’s career stage."
        ),
    }
