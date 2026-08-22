from __future__ import annotations

from gm_assistant.engines.base import BaseDecisionEngine


class RookieDraftEngine(BaseDecisionEngine):
    name = "rookie_draft_engine"

    def can_handle(self, route: dict) -> float:
        if route.get("intent") == "rookie_draft_pick_decision":
            return 1.0
        if route.get("answer_shape") == "ranked_rookie_options":
            return 0.9
        return 0.0

    def required_context(self, route: dict) -> list[str]:
        return [
            "rookie_draft_board",
            "team_future_context",
            "roster_strength_context",
            "contract_context",
        ]

    def execute(self, question: str, owner_team_name: str, route: dict) -> dict:
        pick = route.get("entities", {}).get("pick") or "1.02"

        # Prefer the real rookie cross-elastic engine if available.
        try:
            from snapshot.intelligence.rookies.rookie_cross_elastic_engine import RookieCrossElasticEngine

            engine = RookieCrossElasticEngine()
            result = engine.pick(float(pick))

            best = result.get("best_pick", {}) or {}
            alternatives = result.get("alternatives", []) or result.get("options", []) or []

            options = []
            if best:
                options.append(best)
            options.extend(alternatives[:4])

            lines = []
            for i, option in enumerate(options[:5], start=1):
                player = option.get("player", option)
                name = (
                    player.get("player_name")
                    or player.get("name")
                    or option.get("player_name")
                    or option.get("name")
                    or option.get("archetype")
                    or "Unknown rookie"
                )
                pos = player.get("pos") or option.get("pos") or ""
                ev = option.get("ev") or option.get("expected_value") or option.get("value")
                fit = option.get("fit_score") or option.get("marginal_gain") or option.get("team_fit")

                detail = f"{i}. {name}"
                if pos:
                    detail += f" ({pos})"
                if ev is not None:
                    detail += f" — EV {round(float(ev), 2)}"
                if fit is not None:
                    detail += f", fit/marginal {round(float(fit), 2)}"
                lines.append(detail)

            if not lines:
                raise ValueError("Rookie engine returned no usable options.")

            summary = (
                f"At {pick}, I would treat this as a rookie draft decision, not a trade-partner question.\n\n"
                + "\n".join(lines)
                + "\n\nMy lean: take the highest-value rookie unless the tier is flat enough that trading down still keeps you in the same player band."
            )

            return {
                "decision": "ROOKIE_DRAFT_PICK_DECISION",
                "summary": summary,
                "confidence": 0.86,
                "data": {
                    "pick": pick,
                    "engine_result": result,
                    "engine": self.name,
                },
                "missing_context": [],
            }

        except Exception as e:
            summary = (
                f"At {pick}, this should route to rookie draft options. I could not fully execute the rookie value engine yet, "
                f"so I would return a fallback structure instead of answering with trade partners.\n\n"
                "1. Best player available rookie\n"
                "2. Best RB/TE roster-fit rookie\n"
                "3. Highest-upside rookie if the board is flat\n"
                "4. Trade-down only if the same tier is available later\n\n"
                f"Engine note: {e}"
            )

            return {
                "decision": "ROOKIE_DRAFT_PICK_DECISION",
                "summary": summary,
                "confidence": 0.55,
                "data": {
                    "pick": pick,
                    "engine": self.name,
                    "fallback": True,
                },
                "missing_context": ["rookie_engine_execution"],
            }
