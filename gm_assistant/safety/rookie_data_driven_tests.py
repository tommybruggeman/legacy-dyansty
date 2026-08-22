from __future__ import annotations

from gm_assistant.router import answer_gm_question


def run_tests() -> bool:
    print("\\n🧪 Running rookie data-driven tests...\\n")

    ans = answer_gm_question(
        "Who should I draft in the rookie draft?",
        "Tommy Bruggeman",
        conversation_state={"team_goal": "championship"},
    )

    summary = str(ans.get("summary") or "")
    lower = summary.lower()

    bad_phrases = [
        "temporary board",
        "hardcoded",
        "ashton jeanty",  # should only appear if real DB loads him; this guards the temp board mistake
        "tetairoa mcmillan",
    ]

    bad_hits = [p for p in bad_phrases if p in lower]

    ok = not bad_hits and (
        "data-driven draft recommendation" in lower
        or "rookie candidate board connected" in lower
        or "cannot honestly name a player from data" in lower
    )

    if ok:
        print("✅ rookie_recommendation_not_hardcoded")
    else:
        print("❌ rookie_recommendation_not_hardcoded")
        print("Bad hits:", bad_hits)
        print(summary[:900])

    print("\\n" + "=" * 70)
    print(f"Passed: {1 if ok else 0}")
    print(f"Failed: {0 if ok else 1}")
    print("=" * 70)

    return ok


if __name__ == "__main__":
    ok = run_tests()
    raise SystemExit(0 if ok else 1)
