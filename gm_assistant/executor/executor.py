from __future__ import annotations

from gm_assistant.executor.models import ExecutionResult
from gm_assistant.executor.registry import CAPABILITIES
from gm_assistant.planner.models import ExecutionPlan


def execute_plan(
    plan: ExecutionPlan,
    *,
    question: str,
    owner_team_name: str,
) -> ExecutionResult:
    output = ExecutionResult()

    for step in plan.steps:
        capability = CAPABILITIES.get(step.name)

        if not capability:
            from gm_assistant.executor.models import CapabilityResult

            output.results.append(
                CapabilityResult(
                    name=step.name,
                    success=False,
                    data=None,
                    message=f"No registered capability for {step.name}",
                )
            )
            continue

        result = capability(
            question=question,
            owner_team_name=owner_team_name,
            params=step.params,
        )
        output.results.append(result)

    return output
