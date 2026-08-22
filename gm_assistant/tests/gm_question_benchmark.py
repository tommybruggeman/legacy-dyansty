from __future__ import annotations

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question
from gm_assistant.nlu.parser import parse_gm_question
from gm_assistant.planner.reasoning_planner import build_execution_plan
from gm_assistant.executor.executor import execute_plan


QUESTIONS = [
    "I want to win the championship this year",
    "how does my team look?",
    "how does my team stack up against the league?",
    "what is my biggest weakness?",
    "what is my biggest strength?",
    "what contracts are hurting me the most?",
    "which players should I not trade?",
    "should I trade Garrett Wilson?",
    "should I cut Garrett Wilson?",
    "how does Garrett Wilson's contract work with my goal to win now?",
    "should I hold Isiah Pacheco or move him?",
    "who are my best win-now players?",
    "who are my worst win-now players?",
    "what type of RB should I target?",
    "what type of TE should I target?",
    "should I use picks to upgrade this team?",
    "should I trade a QB for RB help?",
    "which teams should I call first?",
    "what would a fair Garrett Wilson trade look like?",
    "am I too focused on trading?",
    "what should I do if no one wants my bad contracts?",
    "should I prioritize lineup points or future value?",
    "what is the safest path to improve?",
    "what is the aggressive path to improve?",
    "what is the one move I should make first?",
    "I'm looking for an RB to add to my team. Who do you recommend and what would that trade look like to get him?",
    "I'm looking for an RB to add to my team. Give me 5 options that fit my team either FAs or trade targets and why do you think that's a good target.",
    "I have pick #1.02. Who should I look at that fits my team?",
    "Which FAs should I target? Give me 5 options.",
    "Which team has the best player contracts?",
    "Which player at each position has the best contract per dollar? Show me the top 2 at each position.",
]


def judge(parsed, plan, answer):
    issues = []

    if parsed.intent == "unknown":
        issues.append("NLU_UNKNOWN")

    if answer.get("summary") in [None, ""]:
        issues.append("EMPTY_SUMMARY")

    summary = str(answer.get("summary") or "")

    if "I’d answer the specific question first" in summary:
        issues.append("GENERIC_WRITER")

    if "Here is how I would read this from a GM lens" in summary:
        issues.append("LEGACY_TEMPLATE")

    if "Run the player lookup first" in summary:
        issues.append("MISSING_EVIDENCE")

    if "acquire_player" in str(answer.get("intent")):
        issues.append("BAD_ROUTE_ACQUIRE")

    if "should I cut Garrett Wilson" in parsed.raw_question and answer.get("decision") == "DROP":
        issues.append("BAD_DROP")

    if "fair Garrett Wilson trade" in parsed.raw_question and parsed.intent != "trade_package":
        issues.append("BAD_NLU_FAIR_TRADE")

    return issues


def main():
    owner = "Tommy Bruggeman"
    conversation_state = None

    for i, q in enumerate(QUESTIONS, 1):
        parsed = parse_gm_question(q)
        plan = build_execution_plan(parsed)
        execution = execute_plan(plan, question=q, owner_team_name=owner)

        answer = answer_gm_question(q, owner, conversation_state=conversation_state)
        conversation_state = answer.get("conversation_state", conversation_state)

        issues = judge(parsed, plan, answer)

        print("\n" + "=" * 120)
        print(f"{i}. QUESTION: {q}")
        print("-" * 120)

        print("NLU")
        print(" intent:", parsed.intent)
        print(" players:", parsed.player_names)
        print(" positions:", parsed.positions)
        print(" count:", parsed.count_requested)
        print(" target_pool:", parsed.target_pool)
        print(" decision_type:", parsed.decision_type)
        print(" confidence:", parsed.confidence)

        print("\nPLAN")
        print(" objective:", plan.objective)
        print(" expected_output:", plan.expected_output)
        print(" steps:", [s.name for s in plan.steps])

        print("\nEXECUTION")
        print(" success_count:", execution.success_count, "/", len(execution.results))
        for r in execution.results:
            status = "✅" if r.success else "❌"
            print(f" {status} {r.name}: {r.message or 'ok'}")

        print("\nANSWER")
        print(" answer_type:", answer.get("answer_type"))
        print(" answer_intent:", answer.get("intent"))
        print(" decision:", answer.get("decision"))

        print("\nSUMMARY")
        print(answer.get("summary"))

        print("\nDIAGNOSTIC")
        if issues:
            print(" ❌", ", ".join(issues))
        else:
            print(" ✅ PASS")


if __name__ == "__main__":
    main()
