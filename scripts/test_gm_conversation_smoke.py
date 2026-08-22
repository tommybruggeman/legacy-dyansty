from __future__ import annotations

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question


OWNER = "Tommy Bruggeman"

QUESTIONS = [
    "How does my team look?",
    "What scares you about my roster?",
    "If you owned this team what would you do?",
    "Who do you think I overvalue?",
    "Which contracts make me uncomfortable?",
    "Should I be rebuilding?",
    "Can I win this year?",
    "What's my biggest blind spot?",
    "Give me three trades.",
    "I want Bijan Robinson. How do I get him?",
    "Who on my bench should I cut?",
    "If I do nothing this season what happens?",
    "If you took over my franchise for five years what would your plan be?",
    "Which team in my league should I trade with?",
    "Who do you think my most valuable player actually is?",
    "Who is my worst contract?",
    "Convince me NOT to trade Garrett Wilson.",
    "Pretend you're my assistant GM.",
]


def main():
    errors = 0

    print("\n" + "=" * 100)
    print("GM CONVERSATION SMOKE TEST")
    print("=" * 100)

    for i, q in enumerate(QUESTIONS, 1):
        print("\n" + "=" * 100)
        print(f"QUESTION {i}: {q}")

        try:
            res = answer_gm_question(q, OWNER)
            answer_type = res.get("answer_type")
            intent = res.get("intent")
            summary = res.get("summary") or str(res)

            print(f"TYPE: {answer_type}")
            print(f"INTENT: {intent}")
            print("-" * 100)
            print(summary)

        except Exception as e:
            errors += 1
            print("ERROR:", repr(e))

    print("\n" + "=" * 100)
    print(f"COMPLETE — errors: {errors}/{len(QUESTIONS)}")
    print("=" * 100)


if __name__ == "__main__":
    main()
