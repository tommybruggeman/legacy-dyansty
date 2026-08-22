class AIIntelBot:
    """
    Deterministic AI-style reasoning layer.

    This does not call an external LLM yet.
    It contextualizes app data into coach-ready intelligence.
    """

    def rookie_intel(self, player: dict) -> dict:
        name = player.get("player_name") or player.get("name") or "Unknown"
        pos = player.get("pos") or "-"
        team = player.get("nfl_team") or player.get("team") or "-"
        score = float(player.get("score") or player.get("overall_score") or 0)

        flags = []
        boosts = []
        risks = []

        if not team or str(team).strip() in ["", "-", "None", "FA"]:
            risks.append("No confirmed NFL team/team-context attached")
            flags.append("NO_TEAM_CONTEXT")

        if score >= 75:
            boosts.append("Strong model score")
        elif score < 60:
            risks.append("Below premium rookie-score threshold")

        if pos == "QB":
            boosts.append("Superflex format boosts QB value")
        elif pos == "RB":
            risks.append("RB value is more landing-spot sensitive")
        elif pos == "WR":
            boosts.append("WR profile usually holds dynasty value better than RB")

        if "NO_TEAM_CONTEXT" in flags:
            stance = "working-watchlist, not board anchor"
        elif score >= 75:
            stance = "priority rookie-board target"
        elif score >= 65:
            stance = "draftable tier target"
        else:
            stance = "watchlist / late-round profile"

        summary = f"{name} is a {stance}. "
        if boosts:
            summary += "Boosts: " + "; ".join(boosts) + ". "
        if risks:
            summary += "Risks: " + "; ".join(risks) + "."

        return {
            "player_name": name,
            "pos": pos,
            "team": team,
            "score": round(score, 2),
            "stance": stance,
            "boosts": boosts,
            "risks": risks,
            "flags": flags,
            "summary": summary.strip(),
        }

    def rookie_board_intel(self, players: list[dict], limit: int = 10) -> dict:
        enriched = []

        for p in players:
            intel = self.rookie_intel(p)
            p = dict(p)
            p["ai_intel"] = intel
            p["ai_summary"] = intel["summary"]
            p["ai_flags"] = intel["flags"]

            # Final safety: no-team players cannot sit above valid-team premium profiles
            adjusted_score = float(p.get("score") or p.get("overall_score") or 0)
            if "NO_TEAM_CONTEXT" in intel["flags"]:
                adjusted_score -= 18

            p["ai_adjusted_score"] = round(adjusted_score, 2)
            enriched.append(p)

        enriched = sorted(
            enriched,
            key=lambda x: (
                x.get("ai_adjusted_score", 0),
                0 if x.get("ai_flags") else 1,
            ),
            reverse=True,
        )

        for i, p in enumerate(enriched, start=1):
            p["ai_overall_rank"] = i

        return {
            "players": enriched[:limit],
            "flags": self.board_flags(enriched),
        }

    def board_flags(self, players: list[dict]) -> list[str]:
        flags = []

        no_team_count = sum(
            1 for p in players
            if "NO_TEAM_CONTEXT" in (p.get("ai_flags") or [])
        )

        if no_team_count:
            flags.append(f"{no_team_count} rookie rows have no confirmed team context")

        if not players:
            flags.append("No rookie players available for AI intel")

        return flags
