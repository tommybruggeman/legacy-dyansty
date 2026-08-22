from __future__ import annotations

from typing import Any

from gm_assistant.planner.models import GMPlan


def execute_plan(plan: GMPlan, owner_team_name: str, understanding: dict) -> dict[str, Any]:
    context: dict[str, Any] = {
        "plan": plan.to_dict(),
        "owner_team_name": owner_team_name,
        "understanding": understanding,
        "loaded": {},
        "warnings": [],
    }

    for task in plan.tasks:
        try:
            if task.name == "load_roster":
                from gm_assistant.pipeline.evidence.roster_evidence import build_roster_evidence
                pack = build_roster_evidence(plan.question, owner_team_name, understanding)
                context["loaded"]["roster"] = pack.roster
                context["loaded"]["player"] = pack.player
                context["loaded"]["team_context"] = pack.team_context
                context["warnings"].extend(pack.notes)

            elif task.name == "load_player":
                # Already loaded by roster evidence for now.
                context["loaded"].setdefault("target_player", context["loaded"].get("player"))

            else:
                # Planner v1 records planned tasks even before all task executors exist.
                context["loaded"].setdefault("_planned_only", []).append({
                    "name": task.name,
                    "params": task.params,
                })

        except Exception as e:
            context["warnings"].append(f"{task.name} failed: {e}")

    return context
