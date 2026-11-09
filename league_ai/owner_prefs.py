"""Each owner's Front Office preferences from Settings (owner_gm_prefs): how long answers should be and whether to
plan for contending or rebuilding. Added to the turn's context after the cached league pack, so they cost no cache."""
from __future__ import annotations

from typing import Any

LENGTH = {
    "detailed": "This owner prefers detailed answers: give the full reasoning with the supporting numbers in the first answer "
                "(bullets, and a compact table where it helps) instead of the short default.",
}
PLAN = {
    "contend": "This owner says the team is contending this year: weigh moves for points this season over future value.",
    "rebuild": "This owner says the team is rebuilding: weigh younger players, picks and cheaper long contracts over points this season.",
}


def load(client: Any, user_id: str) -> dict[str, str]:
    try:
        rows = client.table("owner_gm_prefs").select("answer_length,team_plan").eq("user_id", user_id).limit(1).execute().data or []
    except Exception:
        return {}
    return rows[0] if rows else {}


def note(prefs: dict[str, str]) -> str:
    lines = [text for text in (LENGTH.get(prefs.get("answer_length") or ""), PLAN.get(prefs.get("team_plan") or "")) if text]
    return ("## Owner preferences\n" + "\n".join(f"- {line}" for line in lines)) if lines else ""


def with_prefs(week_context: str | None, prefs: dict[str, str]) -> str | None:
    extra = note(prefs)
    if not extra:
        return week_context
    return f"{week_context.strip()}\n\n{extra}" if week_context and week_context.strip() else extra
