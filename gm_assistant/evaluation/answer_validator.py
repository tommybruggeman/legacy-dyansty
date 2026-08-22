def validate_answer(question: str, route: dict, answer: str) -> dict:
    q = question.lower()
    a = answer.lower()
    shape = route.get("answer_shape")

    failures = []

    if shape == "ranked_rookie_options":
        if route.get("entities", {}).get("pick") and route["entities"]["pick"] not in a:
            failures.append("Answer did not reference the requested pick.")

        rookie_terms = ["rookie", "draft", "1.01", "1.02", "1.03", "prospect"]
        if not any(t in a for t in rookie_terms):
            failures.append("Answer did not appear to discuss rookie draft options.")

        trade_partner_terms = ["chasen", "connor", "grady", "dylan", "trade partner"]
        if any(t in a for t in trade_partner_terms) and "rookie" not in a:
            failures.append("Answer likely routed to trade partners instead of rookie draft decision.")

    if "players" in q or "who" in q or "options" in q:
        if not any(str(i) + "." in answer for i in range(1, 6)):
            failures.append("Answer did not provide a ranked/listed set of options.")

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "retry_instruction": build_retry_instruction(route, failures),
    }

def build_retry_instruction(route: dict, failures: list[str]) -> str:
    if not failures:
        return ""

    if route.get("answer_shape") == "ranked_rookie_options":
        pick = route.get("entities", {}).get("pick") or "the requested pick"
        return (
            f"The prior answer failed. Regenerate as a rookie draft decision for {pick}. "
            "Give 3-5 specific rookie player options, explain fit, upside, risk, and final recommendation. "
            "Do not answer with trade partners unless discussing trade-down as a separate option."
        )

    return "The prior answer failed validation. Regenerate and directly answer the user's question."
