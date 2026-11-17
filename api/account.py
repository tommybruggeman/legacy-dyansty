"""Account-level actions Apple asks for: delete my account, report a GM answer, and crash reports.

Shared by the app (api/main.py routes) and the website (pages/91_Account.py, pages/05_Front_Office.py),
so both halves behave the same.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

REASONS = ("wrong", "outdated", "harmful", "other")


def friendly(exc: Exception) -> str:
    """The database's own message for raised exceptions (e.g. the commissioner check), else a plain one."""
    text = str(getattr(exc, "message", "") or "")
    if not text and exc.args and isinstance(exc.args[0], dict):
        text = str(exc.args[0].get("message") or "")
    text = text or str(exc)
    if "delete_my_account" in text and ("does not exist" in text or "Could not find" in text):
        return "Account deletion isn't switched on yet. Please try again later."
    return text if len(text) < 200 else "Something went wrong. Please try again."


def delete_account(client: Any) -> dict[str, Any]:
    """Delete the signed-in owner's account (client must act as that owner)."""
    try:
        result = client.rpc("delete_my_account", {}).execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None
    return result.data if isinstance(result.data, dict) else {"ok": True}


def report_answer(client: Any, *, user_id: str, league_id: str | None, reason: str, question: str | None, answer: str | None,
                  conversation_id: str | None = None, note: str | None = None, source: str = "app") -> None:
    """Save an owner's flag on a GM answer."""
    if reason not in REASONS:
        raise ValueError("Pick a reason.")
    row = {
        "user_id": user_id, "league_id": league_id, "reason": reason, "source": source,
        "question": (question or "")[:2000] or None, "answer": (answer or "")[:8000] or None,
        "conversation_id": conversation_id or None, "note": (note or "").strip()[:500] or None,
    }
    try:
        client.table("league_ai_feedback").insert(row).execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None


def crash_line(user_id: str, report: dict[str, Any]) -> str:
    """One JSON line for the server log (Cloud Run keeps logs free). No email or names: the user is a short hash."""
    def clip(value: Any, n: int) -> str:
        return str(value or "")[:n]
    return json.dumps({
        "severity": "ERROR", "kind": "app_crash",
        "user": hashlib.sha256(user_id.encode()).hexdigest()[:10],
        "message": clip(report.get("message"), 500), "stack": clip(report.get("stack"), 4000),
        "screen": clip(report.get("screen"), 200), "build": clip(report.get("build"), 20),
        "version": clip(report.get("version"), 20), "os": clip(report.get("os"), 40),
    })
