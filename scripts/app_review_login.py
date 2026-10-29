"""Create (or reset) Apple's App Review login and link it to the commissioner's team.

Prints nothing secret: the new password goes to $GITHUB_ENV (masked) and to
mobile/store/metadata/review_information/demo_password.txt on the runner only.
"""
from __future__ import annotations

import os
import secrets
import sys

from supabase import create_client

EMAIL = "applereview@legacydynasty.app"


def main() -> int:
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    password = "Ld-" + secrets.token_urlsafe(12)
    print(f"::add-mask::{password}", flush=True)

    user = next((u for u in client.auth.admin.list_users(per_page=1000) if (u.email or "").lower() == EMAIL), None)
    if user:
        client.auth.admin.update_user_by_id(user.id, {"password": password, "email_confirm": True})
        print("[review] login reset", flush=True)
    else:
        user = client.auth.admin.create_user({"email": EMAIL, "password": password, "email_confirm": True}).user
        print("[review] login created", flush=True)

    boss = (client.table("league_memberships").select("league_id,league_team_id").eq("role", "commissioner")
            .not_.is_("league_team_id", "null").limit(1).execute().data or [])
    if not boss:
        print("[review] no commissioner membership with a team; can't link the demo login", flush=True)
        return 1
    client.table("league_memberships").upsert(
        {"league_id": boss[0]["league_id"], "user_id": user.id, "role": "member", "league_team_id": boss[0]["league_team_id"]},
        on_conflict="league_id,user_id",
    ).execute()
    print("[review] linked to the commissioner's team", flush=True)

    with open(os.environ["GITHUB_ENV"], "a") as env:
        env.write(f"REVIEW_EMAIL={EMAIL}\nREVIEW_PASSWORD={password}\n")
    with open("mobile/store/metadata/review_information/demo_password.txt", "w") as f:
        f.write(password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
