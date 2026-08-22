class AIBrain:
    """
    Central intelligence layer.

    Goal:
    - normalize messy rows
    - detect data quality issues
    - adjust weights by context
    - create coach-ready reasoning
    """

    def __init__(self, league_format=None):
        self.league_format = league_format or {
            "superflex": True,
            "ppr": 0.5,
            "first_down_bonus": 0.5,
            "salary_cap": 225,
        }

    def data_quality(self, row):
        flags = []
        confidence = 100

        team = row.get("nfl_team") or row.get("team") or row.get("current_team")
        source = row.get("source") or row.get("source_type")
        score_fields = [
            "final_rookie_score",
            "rank_score",
            "asset_score",
            "brain_score",
            "future_score",
            "contract_score",
            "situation_score",
            "win_now_score",
        ]

        if not team or str(team).strip() in ["", "-", "None", "FA"]:
            flags.append("NO_TEAM_CONTEXT")
            confidence -= 18

        if source in [None, "", "computed_from_player_universe"]:
            flags.append("WEAK_SOURCE")
            confidence -= 20

        if not any(row.get(f) is not None for f in score_fields):
            flags.append("NO_SCORE_CONTEXT")
            confidence -= 25

        return {
            "confidence": max(0, min(100, confidence)),
            "flags": flags,
        }

    def weighted_player_score(self, row, mode="dynasty"):
        dq = self.data_quality(row)

        asset = float(row.get("asset_score") or row.get("brain_score") or row.get("rank_score") or row.get("final_rookie_score") or 0)
        future = float(row.get("future_score") or 0)
        contract = float(row.get("contract_score") or 0)
        situation = float(row.get("situation_score") or row.get("team_need_fit_score") or 0)
        win_now = float(row.get("win_now_score") or row.get("present_score") or 0)
        risk = float(row.get("risk_score") or 0)

        if mode == "contend":
            score = asset * 0.30 + win_now * 0.30 + situation * 0.20 + contract * 0.12 + future * 0.08
        elif mode == "rebuild":
            score = asset * 0.30 + future * 0.35 + contract * 0.20 + situation * 0.10 + win_now * 0.05
        elif mode == "rookie":
            prospect = float(row.get("prospect_score") or asset)
            pos_value = float(row.get("positional_value_score") or 0)
            source_quality = 100 if "consensus" in str(row.get("source")) else 45
            score = prospect * 0.45 + future * 0.25 + situation * 0.12 + pos_value * 0.08 + source_quality * 0.10
        else:
            score = asset * 0.35 + future * 0.25 + contract * 0.18 + situation * 0.14 + win_now * 0.08

        if "NO_TEAM_CONTEXT" in dq["flags"]:
            score -= 8
        if "WEAK_SOURCE" in dq["flags"]:
            score -= 4

        score -= risk * 0.10

        return round(score, 2)

    def explain_player(self, row, mode="dynasty"):
        score = self.weighted_player_score(row, mode=mode)
        dq = self.data_quality(row)

        name = row.get("player_name") or row.get("name") or "Unknown"
        pos = row.get("pos") or "-"
        team = row.get("nfl_team") or row.get("team") or "-"

        strengths = []
        risks = []

        if float(row.get("future_score") or 0) >= 65:
            strengths.append("strong future profile")
        if float(row.get("situation_score") or row.get("team_need_fit_score") or 0) >= 60:
            strengths.append("good situation fit")
        if float(row.get("contract_score") or 0) >= 70:
            strengths.append("contract efficient")
        if pos == "QB" and self.league_format.get("superflex"):
            strengths.append("superflex positional leverage")

        if "NO_TEAM_CONTEXT" in dq["flags"]:
            risks.append("missing team context")
        if "WEAK_SOURCE" in dq["flags"]:
            risks.append("weak source quality")
        if float(row.get("risk_score") or 0) >= 60:
            risks.append("elevated risk")

        if score >= 75:
            stance = "priority target"
        elif score >= 65:
            stance = "strong hold / draftable target"
        elif score >= 55:
            stance = "watchlist or price-sensitive target"
        else:
            stance = "avoid unless cost is cheap"

        return {
            "name": name,
            "pos": pos,
            "team": team,
            "ai_score": score,
            "confidence": dq["confidence"],
            "flags": dq["flags"],
            "stance": stance,
            "strengths": strengths,
            "risks": risks,
            "summary": f"{name} ({pos}, {team}) grades as {stance} at {score} confidence {dq['confidence']}."
        }

    def rank_rows(self, rows, mode="dynasty"):
        ranked = []

        for row in rows:
            row = dict(row)
            intel = self.explain_player(row, mode=mode)
            row["ai_score"] = intel["ai_score"]
            row["ai_confidence"] = intel["confidence"]
            row["ai_flags"] = intel["flags"]
            row["ai_stance"] = intel["stance"]
            row["ai_summary"] = intel["summary"]
            ranked.append(row)

        ranked = sorted(
            ranked,
            key=lambda r: (
                r.get("ai_score", 0),
                r.get("ai_confidence", 0),
            ),
            reverse=True,
        )

        for i, row in enumerate(ranked, start=1):
            row["ai_rank"] = i

        return ranked
