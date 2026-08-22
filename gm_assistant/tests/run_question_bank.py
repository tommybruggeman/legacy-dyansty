from __future__ import annotations

from gm_assistant.gm_brain import answer_gm_question
from gm_assistant.tests.question_bank_100 import QUESTIONS

OWNER = "Tommy Bruggeman"


BAD_PATTERNS = [
    "I’d evaluate the roster by direction first",
    "A good GM answer should not just list",
    "Run the player lookup first",
    "No roster data available",
    "No team future context available yet",
    "asset None",
    "ppg 0.0",
    "this roster intent is not handled",
    "I understood this as",
]


def grade(summary: str) -> tuple[str, list[str]]:
    hits = [p for p in BAD_PATTERNS if p.lower() in (summary or "").lower()]

    if not summary:
        hits.append("empty summary")

    if hits:
        return "FAIL", hits

    if len(summary) < 120:
        return "WEAK", ["too short"]

    return "PASS", []


def main():
    results = []

    for i, q in enumerate(QUESTIONS, 1):
        print(f"Running {i}/{len(QUESTIONS)}: {q}")
        try:
            res = answer_gm_question(q, OWNER)
            decision = res.get("decision") or res.get("intent") or res.get("answer_type")
            summary = res.get("summary") or ""
            status, issues = grade(summary)

            results.append({
                "num": i,
                "question": q,
                "status": status,
                "decision": decision,
                "issues": issues,
                "summary": summary,
            })

        except Exception as e:
            results.append({
                "num": i,
                "question": q,
                "status": "ERROR",
                "decision": None,
                "issues": [str(e)],
                "summary": "",
            })

    passed = len([r for r in results if r["status"] == "PASS"])
    weak = len([r for r in results if r["status"] == "WEAK"])
    failed = len([r for r in results if r["status"] == "FAIL"])
    errors = len([r for r in results if r["status"] == "ERROR"])

    print("\nSUMMARY")
    print("PASS:", passed)
    print("WEAK:", weak)
    print("FAIL:", failed)
    print("ERROR:", errors)

    print("\nPROBLEM QUESTIONS")
    for r in results:
        if r["status"] != "PASS":
            print("\n" + "=" * 100)
            print(f'{r["num"]}. {r["question"]}')
            print("STATUS:", r["status"])
            print("DECISION:", r["decision"])
            print("ISSUES:", r["issues"])
            print("SUMMARY:")
            print(r["summary"][:1200])

    print("\nDECISION COUNTS")
    counts = {}
    for r in results:
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1

    for k, v in sorted(counts.items(), key=lambda x: str(x[0])):
        print(k, v)


if __name__ == "__main__":
    main()
