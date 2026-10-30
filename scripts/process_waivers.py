"""Run due FAAB bids for every league whose app transactions are on (Wednesday and Sunday 3 AM Eastern).

The app also runs them the first time anyone opens it after 3 AM; whichever comes first does the work and the other
finds nothing due. Prints counts only (the repo and its Actions logs are public).
"""
from __future__ import annotations

import os

from supabase import create_client


def main() -> None:
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    leagues = client.table("league_app_transactions").select("league_id").eq("enabled", True).execute().data or []
    for n, row in enumerate(leagues, start=1):
        out = client.rpc("process_due_waivers", {"p_league_id": row["league_id"]}).execute().data or {}
        print(f"league {n}: processed {out.get('processed', 0)}, won {out.get('won', 0)}, lost {out.get('lost', 0)}, failed {out.get('failed', 0)}", flush=True)
    print(f"{len(leagues)} league(s) checked", flush=True)


if __name__ == "__main__":
    main()
