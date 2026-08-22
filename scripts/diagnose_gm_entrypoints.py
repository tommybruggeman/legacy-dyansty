from __future__ import annotations

QUESTIONS = [
    "what are the biggest weaknesses on my team",
    "which contracts are hurting me the most",
    "how do you like my team",
    "who should I target",
    "I have pick 1.02 this year in the rookie draft who should I look at?",
]

OWNER = "Tommy Bruggeman"


def print_result(label, res):
    print("\n" + "-" * 100)
    print(label)
    print("TYPE:", res.get("answer_type") if isinstance(res, dict) else type(res))
    print("INTENT:", res.get("intent") if isinstance(res, dict) else None)
    print("SUMMARY:")
    if isinstance(res, dict):
        print(res.get("summary") or res)
    else:
        print(res)


def main():
    print("=" * 100)
    print("GM ENTRYPOINT DIAGNOSTIC")
    print("=" * 100)

    from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question

    try:
        from gm_assistant.router import route_question
    except Exception as e:
        route_question = None
        print("Could not import route_question:", e)

    for q in QUESTIONS:
        print("\n" + "=" * 100)
        print("QUESTION:", q)

        try:
            res = answer_gm_question(q, OWNER)
            print_result("gm_brain.answer_gm_question", res)
        except Exception as e:
            print("answer_gm_question ERROR:", repr(e))

        if route_question:
            try:
                res = route_question(q, OWNER)
                print_result("gm_assistant.router.route_question", res)
            except Exception as e:
                print("route_question ERROR:", repr(e))


if __name__ == "__main__":
    main()
