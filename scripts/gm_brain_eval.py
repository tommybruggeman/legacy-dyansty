from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from gm_assistant.conversation.engine import GMConversationEngine
from tests.gm_brain.test_cases import TEST_CASES


OUT_DIR = Path("tests/gm_brain/results")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def stringify_answer(answer: Any) -> str:
    if isinstance(answer, str):
        return answer

    try:
        return json.dumps(answer, indent=2, default=str)
    except Exception:
        return str(answer)


def score_answer(answer_text: str, expected_checks: list[str]) -> dict:
    text = answer_text.lower()

    hits = []
    misses = []

    for check in expected_checks:
        if check.lower() in text:
            hits.append(check)
        else:
            misses.append(check)

    return {
        "checks_total": len(expected_checks),
        "checks_hit": len(hits),
        "checks_missed": len(misses),
        "hits": hits,
        "misses": misses,
        "rough_score": round((len(hits) / max(len(expected_checks), 1)) * 10, 1),
    }


def run_case(case: dict, save: bool = True) -> dict:
    name = case["name"]
    owner = case["owner"]
    questions = case["questions"]
    expected_checks = case.get("expected_checks", [])

    transcript = []
    engine = GMConversationEngine(owner)

    print("\n" + "=" * 80)
    print(f"TEST CASE: {name}")
    print(f"OWNER: {owner}")
    print("=" * 80)

    for i, question in enumerate(questions, start=1):
        prompt = question

        print("\n" + "-" * 80)
        print(f"QUESTION {i}: {question}")
        print("-" * 80)

        start = time.perf_counter()
        raw_answer = engine.ask(prompt)
        elapsed = round(time.perf_counter() - start, 2)

        answer_text = stringify_answer(raw_answer)
        score = score_answer(answer_text, expected_checks)

        print("\nANSWER:\n")
        print(answer_text)
        print("\nSCORE:")
        print(json.dumps(score, indent=2))
        print(f"\nTIME: {elapsed}s")

        transcript.append({
            "question_number": i,
            "question": question,
            "prompt_sent": prompt,
            "resolved_question": raw_answer.get("resolved_question") if isinstance(raw_answer, dict) else None,
            "state": raw_answer.get("state") if isinstance(raw_answer, dict) else None,
            "answer": raw_answer,
            "answer_text": answer_text,
            "score": score,
            "elapsed_seconds": elapsed,
        })


    result = {
        "name": name,
        "owner": owner,
        "transcript": transcript,
    }

    if save:
        out_path = OUT_DIR / f"{name}.json"
        out_path.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nSaved result: {out_path}")

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Run one test case by name")
    parser.add_argument("--list", action="store_true", help="List test cases")
    args = parser.parse_args()

    if args.list:
        for case in TEST_CASES:
            print(case["name"])
        return

    cases = TEST_CASES

    if args.case:
        cases = [c for c in TEST_CASES if c["name"] == args.case]
        if not cases:
            raise SystemExit(f"No test case found named: {args.case}")

    summary = []

    for case in cases:
        result = run_case(case)
        final_scores = [
            item["score"]["rough_score"]
            for item in result["transcript"]
        ]
        avg_score = round(sum(final_scores) / max(len(final_scores), 1), 1)
        summary.append({
            "name": result["name"],
            "questions": len(result["transcript"]),
            "avg_rough_score": avg_score,
        })

    print("\n" + "=" * 80)
    print("EVAL SUMMARY")
    print("=" * 80)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
