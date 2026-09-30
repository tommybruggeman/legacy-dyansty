"""Memory for League AI: private owner notes, the shared league notebook, saved conversations.

These are the only tables League AI writes. Everything runs through the
asker's own authenticated client, so row-level security keeps owner notes
private to that owner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from league_ai.tools import ToolSpec

MAX_NOTES_IN_PACK = 40
MAX_MESSAGES_SAVED = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Memory:
    def __init__(self, client: Any, *, league_id: str, user_id: str, league_team_id: str | None, owner_name: str):
        self.client = client
        self.league_id = league_id
        self.user_id = user_id
        self.league_team_id = league_team_id
        self.owner_name = owner_name

    # ---- notes ---------------------------------------------------------------
    def private_notes(self, limit: int = MAX_NOTES_IN_PACK) -> list[str]:
        try:
            rows = (self.client.table("league_ai_memory").select("note, created_at").eq("league_id", self.league_id).eq("scope", "owner")
                    .eq("user_id", self.user_id).eq("active", True).order("created_at", desc=True).limit(limit).execute().data or [])
        except Exception:
            return []
        return [f"{str(r.get('created_at') or '')[:10]}: {r['note']}" for r in reversed(rows)]

    def league_notes(self, limit: int = MAX_NOTES_IN_PACK) -> list[str]:
        try:
            rows = (self.client.table("league_ai_memory").select("note, created_at").eq("league_id", self.league_id).eq("scope", "league")
                    .eq("active", True).order("created_at", desc=True).limit(limit).execute().data or [])
        except Exception:
            return []
        return [f"{str(r.get('created_at') or '')[:10]}: {r['note']}" for r in reversed(rows)]

    def remember(self, scope: str, note: str) -> dict[str, Any]:
        scope = (scope or "owner").strip().lower()
        if scope not in {"owner", "league"}:
            return {"error": "scope must be 'owner' or 'league'"}
        note = " ".join(str(note or "").split())
        if len(note) < 3:
            return {"error": "note is empty"}
        note = note[:600]
        row = {"league_id": self.league_id, "scope": scope, "user_id": self.user_id if scope == "owner" else None,
               "league_team_id": self.league_team_id, "note": note, "created_by": self.user_id, "active": True}
        try:
            self.client.table("league_ai_memory").insert(row).execute()
        except Exception as exc:
            return {"error": f"could not save note: {type(exc).__name__}"}
        return {"saved": True, "scope": scope, "note": note}

    def forget(self, contains: str) -> dict[str, Any]:
        needle = str(contains or "").strip()
        if len(needle) < 3:
            return {"error": "give a few words from the note to forget"}
        try:
            rows = (self.client.table("league_ai_memory").select("id, note").eq("league_id", self.league_id).eq("created_by", self.user_id)
                    .eq("active", True).ilike("note", f"%{needle}%").limit(10).execute().data or [])
            for r in rows:
                self.client.table("league_ai_memory").update({"active": False}).eq("id", r["id"]).execute()
        except Exception as exc:
            return {"error": f"could not forget: {type(exc).__name__}"}
        return {"forgot": [r["note"] for r in rows]}

    # ---- conversations ---------------------------------------------------------
    def latest_conversation(self) -> dict[str, Any] | None:
        try:
            rows = (self.client.table("league_ai_conversations").select("id, title, messages, updated_at").eq("league_id", self.league_id)
                    .eq("user_id", self.user_id).order("updated_at", desc=True).limit(1).execute().data or [])
        except Exception:
            return None
        return rows[0] if rows else None

    def list_conversations(self, limit: int = 20) -> list[dict[str, Any]]:
        """Titles only (cheap); messages are loaded when one is chosen."""
        try:
            return (self.client.table("league_ai_conversations").select("id, title, updated_at").eq("league_id", self.league_id)
                    .eq("user_id", self.user_id).order("updated_at", desc=True).limit(limit).execute().data or [])
        except Exception:
            return []

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        try:
            rows = self.client.table("league_ai_conversations").select("id, title, messages").eq("id", conversation_id).eq("user_id", self.user_id).limit(1).execute().data or []
        except Exception:
            return None
        return rows[0] if rows else None

    def save_conversation(self, conversation_id: str | None, messages: list[dict[str, str]]) -> str | None:
        clean = [{"role": m.get("role", "user"), "content": str(m.get("content") or "")[:6000]} for m in messages][-MAX_MESSAGES_SAVED:]
        title = next((m["content"][:80] for m in clean if m["role"] == "user"), "Conversation")
        try:
            if conversation_id:
                self.client.table("league_ai_conversations").update({"messages": clean, "title": title, "updated_at": _now()}).eq("id", conversation_id).execute()
                return conversation_id
            res = self.client.table("league_ai_conversations").insert({"league_id": self.league_id, "user_id": self.user_id, "league_team_id": self.league_team_id,
                                                                       "title": title, "messages": clean}).execute()
            data = res.data or []
            return str(data[0]["id"]) if data else None
        except Exception:
            return None

    # ---- tools -------------------------------------------------------------------
    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "remember",
                "Save a durable note. scope 'owner' = private to this owner (their goals, timeline, players they won't trade, decisions reached, preferences). scope 'league' = the shared league notebook (owner tendencies, trade prices, patterns any member could observe). One fact per call, in plain words. Do not save small talk or facts already in the league state.",
                {"type": "object", "properties": {"scope": {"type": "string", "enum": ["owner", "league"]}, "note": {"type": "string"}}, "required": ["scope", "note"]},
                lambda scope, note: self.remember(scope, note),
            ),
            ToolSpec(
                "forget",
                "Deactivate notes this owner previously saved that contain the given words (when the owner says something no longer applies).",
                {"type": "object", "properties": {"contains": {"type": "string"}}, "required": ["contains"]},
                lambda contains: self.forget(contains),
            ),
        ]
