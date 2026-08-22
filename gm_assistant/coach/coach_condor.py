from gm_assistant.models.brain_decision import BrainDecision


class CoachCondor:

    def speak(self, decision: BrainDecision) -> str:

        out = []

        for i, option in enumerate(decision.options, start=1):

            out.append(f"{i}. {option.title}")
            out.append("")

            out.append(option.recommendation)
            out.append("")

            if option.reasons:
                out.append("Why I like it:")

                for r in option.reasons:
                    out.append(f"• {r}")

                out.append("")

            if option.risks:
                out.append("Things I'd watch:")

                for r in option.risks:
                    out.append(f"• {r}")

                out.append("")

            if option.upside:
                out.append(f"Upside: {option.upside}")

            if option.downside:
                out.append(f"Risk: {option.downside}")

            out.append("")
            out.append("--------------------------------")
            out.append("")

        out.append(decision.overall_take)

        return "\n".join(out)
