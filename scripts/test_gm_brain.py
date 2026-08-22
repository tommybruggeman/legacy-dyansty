from __future__ import annotations

from gm_assistant.intent_router import classify_gm_intent
from gm_assistant.conversation_mode import classify_conversation_mode
from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question


OWNER = "Tommy Bruggeman"

TESTS = [
    "What do you think of the Brandon Aiyuk contract?",
    "Is Josh Allen worth $48?",
    "Is Garrett Wilson a bad contract?",
    "Is Omarion Hampton worth $12?",
    "Is Ashton Jeanty worth $15?",

    "What should I do with Brandon Aiyuk?",
    "Should I trade Garrett Wilson?",
    "Should I cut Kendre Miller?",
    "Should I keep Jared Goff?",
    "Should I extend Josh Allen?",

    "Who should I market-check first?",
    "Who are my best RB trade targets?",
    "Who are the cheapest WRs to buy?",
    "Who is overvalued in my league?",

    "How good is my roster?",
    "Where are my weaknesses?",
    "Can I win this year?",
    "Should I rebuild?",
    "How does my team compare to the rest of the league?",

    "Should I draft Hampton or Jeanty?",
    "Who should I draft at 1.02?",
    "Which rookie RB has the safest profile?",
    "Who has the highest ceiling?",

    "Build me a trade for Mark Andrews.",
    "Give me three trades that improve my team.",
    "Pretend you're my assistant GM.",
    "What move would you make first if you took over this team?",

    "Who has the best roster?",
    "Who is rebuilding?",
    "Who should I trade with?",
    "Which owners overvalue quarterbacks?",

    "Why is Aiyuk risky?",
    "How much does offensive line matter for Hampton?",
    "How much should coaching influence Jeanty's value?",
    "How much should draft capital matter?",
]


def main():
    for i, q in enumerate(TESTS, 1):
        print("\n" + "=" * 120)
        print(f"TEST {i}: {q}")
        print("-" * 120)

        intent = classify_gm_intent(q)
        mode = classify_conversation_mode(q)

        print("INTENT:", intent)
        print("MODE:", mode)

        try:
            res = answer_gm_question(q, OWNER)
            print("ANSWER TYPE:", res.get("answer_type") if isinstance(res, dict) else type(res))
            print("MODE OUT:", res.get("conversation_mode") if isinstance(res, dict) else None)
            print("\nANSWER:\n")
            print(res.get("summary", res) if isinstance(res, dict) else res)
        except Exception as e:
            print("ERROR:", repr(e))


if __name__ == "__main__":
    main()
