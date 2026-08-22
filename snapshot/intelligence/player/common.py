from __future__ import annotations

from auth import service_client


def sb():
    return service_client()


def first(rows: list[dict]) -> dict:
    return rows[0] if rows else {}


def num(value, default=None):
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def pick(*values, default=None):
    for v in values:
        if v is not None:
            return v
    return default


def find_one(table: str, player_name: str, owner_team_name: str | None = None) -> dict:
    client = sb()

    try:
        q = client.table(table).select("*").ilike("player_name", f"%{player_name}%").limit(10)
        rows = q.execute().data or []

        if owner_team_name:
            exact_owner = [
                r for r in rows
                if str(r.get("owner_team_name") or r.get("current_owner") or "").lower()
                == owner_team_name.lower()
            ]
            if exact_owner:
                return exact_owner[0]

        exact_name = [
            r for r in rows
            if str(r.get("player_name", "")).lower() == player_name.lower()
        ]

        return first(exact_name or rows)

    except Exception as e:
        return {"_error": str(e), "_table": table}


def find_many(table: str, player_name: str | None = None, owner_team_name: str | None = None, limit: int = 50) -> list[dict]:
    client = sb()

    try:
        q = client.table(table).select("*").limit(limit)

        if player_name:
            q = q.ilike("player_name", f"%{player_name}%")

        if owner_team_name:
            try:
                q = q.eq("owner_team_name", owner_team_name)
            except Exception:
                pass

        return q.execute().data or []

    except Exception as e:
        return [{"_error": str(e), "_table": table}]
