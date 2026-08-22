from __future__ import annotations

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected


TESTS = [
    {
        "name": "rookie_102",
        "question": "I have pick #1.02. Who should I look at that fits my team?",
        "must_include": ["rookie", "trade", "tier gap"],
        "bad_include": ["specific question first"],
    },
    {
        "name": "rookie_draft_general",
        "question": "Who should I draft in the rookie draft?",
        "must_include": ["rookie", "value"],
        "bad_include": ["specific question first"],
    },
]


def run_tests(owner_name: str = "Tommy Bruggeman") -> bool:
    print("\\n🧪 Running rookie decision tests...\\n")

    passed = 0
    failed = 0

    state = {"team_goal": "championship"}

    for test in TESTS:
        ans = answer_gm_question_protected(
            test["question"],
            owner_name,
            conversation_state=state,
        )

        summary = str(ans.get("summary") or "")
        lower = summary.lower()

        missing = [
            x for x in test["must_include"]
            if x.lower() not in lower
        ]

        bad_hits = [
            x for x in test["bad_include"]
            if x.lower() in lower
        ]

        if not missing and not bad_hits:
            passed += 1
            print(f"✅ {test['name']}")
        else:
            failed += 1
            print(f"❌ {test['name']}")
            print("Missing:", missing)
            print("Bad:", bad_hits)
            print(summary[:700])

    print("\\n" + "=" * 70)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    ok = run_tests()
    raise SystemExit(0 if ok else 1)
