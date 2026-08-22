from __future__ import annotations

from gm_assistant.gm_brain import answer_gm_question


QUESTION_GROUPS = {

    "team_strategy": [
        "How does my team look?",
        "Evaluate my roster.",
        "What do you think of my team?"
    ],

    "team_strength": [
        "What is my biggest strength?",
        "Where is my roster strongest?",
        "What am I best at?"
    ],

    "team_weakness": [
        "What is my biggest weakness?",
        "Where is my roster weakest?",
        "What should I improve first?"
    ],

    "contracts": [
        "Which contracts are hurting me?",
        "What are my worst contracts?",
        "Where am I wasting cap space?"
    ],

    "garrett_trade": [
        "Should I trade Garrett Wilson?",
        "Should I move Garrett Wilson?",
        "Would you sell Garrett Wilson?"
    ],

    "garrett_cut": [
        "Should I cut Garrett Wilson?",
        "Would you drop Garrett Wilson?",
        "Is Garrett Wilson worth keeping?"
    ],

    "rookie_pick": [
        "I have pick 1.02. Who should I draft?",
        "Who fits my team best at 1.02?",
        "With the second pick who should I take?"
    ],

    "rookie_trade_down": [
        "Should I trade down from 1.02?",
        "Would you move the second pick?",
        "Should I keep pick 1.02?"
    ],

    "rookie_best_player": [
        "Who is the best player available at 1.02?",
        "Give me your top rookie options.",
        "Rank my rookie choices."
    ],

    "free_agents": [
        "Which FAs should I target?",
        "Who should I add off waivers?",
        "Give me five free agent targets."
    ],

    "rb_targets": [
        "Which RB should I target?",
        "Find me an RB upgrade.",
        "Who is a realistic RB trade target?"
    ],

    "te_targets": [
        "Which TE should I target?",
        "Find me a TE upgrade.",
        "Who is a realistic TE trade target?"
    ],

    "trade_package": [
        "Build me a Garrett Wilson trade.",
        "Give me a fair Garrett Wilson package.",
        "What trade gets Garrett Wilson moved?"
    ],

    "league": [
        "How does my team compare to the league?",
        "How do I stack up?",
        "Where do I rank?"
    ],
}


def main():

    conversation_state = None

    total = 0

    for category, questions in QUESTION_GROUPS.items():

        print()
        print("=" * 120)
        print(category.upper())
        print("=" * 120)

        for q in questions:

            total += 1

            try:

                ans = answer_gm_question(
                    q,
                    "Tommy Bruggeman",
                    conversation_state=conversation_state,
                )

                conversation_state = ans.get(
                    "conversation_state",
                    conversation_state,
                )

                print()
                print("-" * 100)
                print("QUESTION :", q)
                print("TYPE     :", ans.get("answer_type"))
                print("INTENT   :", ans.get("intent"))
                print("DECISION :", ans.get("decision"))

                summary = ans.get("summary", "")

                if len(summary) > 350:
                    summary = summary[:350] + "..."

                print("SUMMARY  :", summary)

            except Exception as e:

                print()
                print("FAILED :", q)
                print(e)

    print()
    print("=" * 120)
    print(f"TOTAL QUESTIONS: {total}")
    print("=" * 120)


if __name__ == "__main__":
    main()
