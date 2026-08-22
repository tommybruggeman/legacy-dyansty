from __future__ import annotations

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question

OWNER = "Tommy Bruggeman"

THREAD = [
    "How does my team look?",
    "What scares you about my roster?",
    "Should I trade Garrett Wilson?",
    "Convince me not to trade him.",
    "What would you ask for?",
    "What about his contract?",
    "Okay then what is my next move?",
    "Give me three trades.",
    "Who should I cut from the bench?",
    "If you owned this team what would you do?",
]


def main():
    state = None

    for i, q in enumerate(THREAD, 1):
        print("\n" + "=" * 100)
        print(f"TURN {i}: {q}")

        res = answer_gm_question(q, OWNER, conversation_state=state)
        state = res.get("conversation_state")

        print("TYPE:", res.get("answer_type"))
        print("INTENT:", res.get("intent"))
        print("RESOLVED:", res.get("resolved_question"))
        print("-" * 100)
        print(res.get("summary") or res)

        print("-" * 100)
        print("STATE:", {
            "current_player": state.get("current_player") if state else None,
            "current_topic": state.get("current_topic") if state else None,
            "last_intent": state.get("last_intent") if state else None,
        })


if __name__ == "__main__":
    main()
