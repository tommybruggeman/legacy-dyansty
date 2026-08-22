from __future__ import annotations

from gm_assistant.gm_brain import answer_gm_question


BASELINE_TESTS = [
    {
        "name": "goal_update",
        "question": "I want to win the championship this year",
        "must_include": ["championship", "weekly", "lineup"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "contract_audit",
        "question": "what contracts are hurting me the most?",
        "must_include": ["Garrett Wilson", "Isiah Pacheco", "liability"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "garrett_trade",
        "question": "should I trade Garrett Wilson?",
        "must_include": ["Garrett Wilson", "shop", "not dump"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "garrett_cut",
        "question": "should I cut Garrett Wilson?",
        "must_include": ["Garrett Wilson", "not cut"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "rb_targets",
        "question": "what type of RB should I target?",
        "must_include": ["RB", "target", "weekly"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "fa_targets",
        "question": "Which FAs should I target? Give me 5 options.",
        "must_include": ["free agents"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "trade_partners",
        "question": "which teams should I call first?",
        "must_include": ["fit", "RBs", "TEs"],
        "bad_include": ["specific question first"],
    },
]


def run_regression_tests(owner_name: str = "Tommy Bruggeman") -> bool:
    print("\\n🧠 Running GM Brain regression tests...\\n")

    passed = 0
    failed = 0

    conversation_state = None

    for test in BASELINE_TESTS:
        question = test["question"]
        answer = answer_gm_question(
            question,
            owner_name,
            conversation_state=conversation_state,
        )

        conversation_state = answer.get("conversation_state", conversation_state)

        summary = str(answer.get("summary") or "")
        summary_lower = summary.lower()

        missing = [
            phrase for phrase in test.get("must_include", [])
            if phrase.lower() not in summary_lower
        ]

        bad_hits = [
            phrase for phrase in test.get("bad_include", [])
            if phrase.lower() in summary_lower
        ]

        ok = not missing and not bad_hits

        if ok:
            passed += 1
            print(f"✅ {test['name']}")
        else:
            failed += 1
            print(f"❌ {test['name']}")
            print(f"   Question: {question}")
            if missing:
                print(f"   Missing: {missing}")
            if bad_hits:
                print(f"   Bad hits: {bad_hits}")
            print(f"   Summary: {summary[:500]}")

    print("\\n" + "=" * 70)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    ok = run_regression_tests()
    raise SystemExit(0 if ok else 1)
