def _num(v, default=50):
    try:
        return float(v)
    except Exception:
        return default


class ContractAgent:
    name = "contract"

    def evaluate(self, row: dict) -> dict:
        salary = _num(row.get("salary"), 0)
        contract_score = _num(row.get("contract_score") or row.get("contract_value_score"), 50)

        if salary <= 0:
            summary = "No contract context."
            confidence = 35
        elif contract_score >= 70:
            summary = "Contract looks efficient."
            confidence = 75
        elif contract_score >= 45:
            summary = "Contract is neutral."
            confidence = 65
        else:
            summary = "Contract may be painful."
            confidence = 70

        return {
            "agent": self.name,
            "grade": round(contract_score, 2),
            "salary": salary,
            "summary": summary,
            "confidence": confidence,
        }
