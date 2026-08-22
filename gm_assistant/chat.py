from gm_assistant.router import ask_gm


# ============================================================
# CHAT + REASONING LAYER v3 (FINAL FIX)
# Fixes player intent extraction issue
# ============================================================

def chat_gm(message: str, snap: dict | None = None) -> str:
    result = ask_gm(message, snap)

    # -----------------------------
    # ERROR HANDLING
    # -----------------------------
    if "error" in result:
        return (
            "🤖 GM Assistant\n\n"
            f"⚠️ Issue: {result['error']}\n"
            "Try asking about a player on your roster."
        )

    # -----------------------------
    # TEAM ANALYSIS
    # -----------------------------
    if "contender_score" in result:
        return (
            "🏈 GM Assistant — Team Analysis\n\n"
            f"📊 Contender Score: {result['contender_score']}/100\n"
            f"🏗 Roster Quality: {result['roster_quality']}/100\n"
            f"💰 Cap Health: {result['cap_health']}/100\n\n"
            f"🧠 Recommendation:\n{result['recommended_move']}\n\n"
            f"💪 Strengths: {', '.join(result['strengths']) if result['strengths'] else 'None'}\n"
            f"⚠️ Weaknesses: {', '.join(result['weaknesses']) if result['weaknesses'] else 'None'}"
        )

    # -----------------------------
    # TRADE RESPONSE (FIXED)
    # -----------------------------
    if "decision" in result:
        explanation = []

        tv = result["trade_value_score"]
        cv = result["contract_value_score"]
        risk = result["contract_risk_score"]

        if tv < 40:
            explanation.append("Low trade value → limited market interest")
        if cv < 50:
            explanation.append("Inefficient contract → reduced long-term value")
        if risk > 70:
            explanation.append("High risk contract → volatility concern")
        if tv > 75:
            explanation.append("Elite asset → high trade leverage")

        return (
            "🔁 Trade Advisor\n\n"
            f"Player: {result['player_name']}\n"
            f"Decision: {result['decision']}\n\n"
            f"📊 Metrics:\n"
            f"• Trade Value: {tv:.1f}/100\n"
            f"• Contract Value: {cv:.1f}/100\n"
            f"• Risk: {risk:.1f}/100\n\n"
            f"🧠 Why:\n" + "\n".join([f"• {e}" for e in explanation]) + "\n\n"
            f"💬 Summary:\n{result['summary']}"
        )

    # -----------------------------
    # DEFAULT
    # -----------------------------
    return (
        "🤖 GM Assistant\n\n"
        "Try:\n"
        "• How is my team?\n"
        "• Should I trade Bo Nix?\n"
    )
