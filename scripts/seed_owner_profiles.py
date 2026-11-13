"""One-time: copy each team's current name and Sleeper picture into our own owner_profiles + storage.

What the league sees today becomes each owner's default profile, kept in our database and our own picture storage,
so it keeps working after the league leaves Sleeper. Once an owner changes their name, team name or picture in
Settings, theirs replaces it. Teams that already have a profile are left alone, so running this twice is safe.

Run from GitHub Actions (league-ai-checks, check=seed_owner_profiles). Prints counts only, never names.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import requests

BUCKET = "owner-pictures"
SLEEPER_AVATAR = "https://sleepercdn.com/avatars/{}"


def _text(value) -> str:
    return " ".join(str(value or "").split())[:40]


def picture_bytes(user: dict) -> bytes | None:
    meta = user.get("metadata") or {}
    url = meta.get("avatar") or (SLEEPER_AVATAR.format(user["avatar"]) if user.get("avatar") else None)
    if not url or not str(url).startswith("https://"):
        return None
    try:
        res = requests.get(url, timeout=20)
    except requests.RequestException:
        return None
    kind = res.headers.get("content-type", "")
    if res.status_code != 200 or not kind.startswith("image/") or len(res.content) > 1_000_000:
        return None
    return res.content


def main() -> int:
    from api import sleeper
    from pipeline.nfl_data.common import service_client

    client = service_client()
    base = os.environ["SUPABASE_URL"].rstrip("/")
    seasons = (client.table("league_seasons").select("league_id,sleeper_league_id").eq("is_active", True).execute().data or [])
    for n, s in enumerate(seasons, 1):
        sid, lid = s.get("sleeper_league_id"), s["league_id"]
        if not sid:
            continue
        have = {str(r["league_team_id"]) for r in
                (client.table("owner_profiles").select("league_team_id").eq("league_id", lid).execute().data or [])}
        teams = client.table("league_teams").select("id,owner_name,team_name,sleeper_roster_id").eq("league_id", lid).execute().data or []
        users = {u.get("user_id"): u for u in (sleeper.raw(f"league/{sid}/users") or [])}
        owners = {r.get("roster_id"): r.get("owner_id") for r in (sleeper.raw(f"league/{sid}/rosters") or [])}
        added = pictures = 0
        for t in teams:
            tid = str(t["id"])
            if tid in have:
                continue
            try:
                user = users.get(owners.get(int(t.get("sleeper_roster_id")))) or {}
            except (TypeError, ValueError):
                user = {}
            meta = user.get("metadata") or {}
            row = {
                "league_team_id": tid, "league_id": lid,
                "display_name": _text(t.get("owner_name")) or _text(user.get("display_name")) or None,
                "team_name": _text(meta.get("team_name")) or _text(t.get("team_name")) or None,
                "picture_url": None, "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            image = picture_bytes(user) if user else None
            if image:
                kind = "image/png" if image[:4] == b"\x89PNG" else "image/jpeg"
                path = f"{tid}/sleeper.{'png' if kind == 'image/png' else 'jpg'}"
                try:
                    client.storage.from_(BUCKET).upload(path, image, {"content-type": kind, "upsert": "true"})
                    row["picture_url"] = f"{base}/storage/v1/object/public/{BUCKET}/{path}"
                    pictures += 1
                except Exception as exc:
                    print(f"  picture upload failed: {type(exc).__name__}", flush=True)
            client.table("owner_profiles").insert(row).execute()
            added += 1
        print(f"league {n}: {added} profiles added ({pictures} pictures copied), {len(have)} already had one", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
