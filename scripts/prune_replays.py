"""Delete session replay pictures older than KEEP_DAYS from the private 'replays' bucket (and their frame rows),
so replays stay inside Supabase's free storage. Runs daily from the League AI learning workflow.

Folders are <owner id>/<session start in ms>/<n>.jpg, so old sessions are found by folder name alone.
Prints counts only (the repo's Actions logs are public).

    python -m scripts.prune_replays
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client

KEEP_DAYS = int(os.getenv("REPLAY_KEEP_DAYS") or 14)


def main() -> int:
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    bucket = client.storage.from_("replays")
    cutoff_ms = (time.time() - KEEP_DAYS * 86400) * 1000
    removed = sessions = 0
    try:
        owners = bucket.list("", {"limit": 1000})
    except Exception as exc:
        print(f"[replays] bucket not ready ({type(exc).__name__}); has owner-activity.sql been run?")
        return 0
    for owner in owners or []:
        for sess in bucket.list(owner["name"], {"limit": 1000}) or []:
            try:
                started = int(sess["name"])
            except (TypeError, ValueError):
                continue
            if started >= cutoff_ms:
                continue
            folder = f"{owner['name']}/{sess['name']}"
            files = [f"{folder}/{f['name']}" for f in bucket.list(folder, {"limit": 1000}) or []]
            for i in range(0, len(files), 100):
                bucket.remove(files[i:i + 100])
            removed += len(files)
            sessions += 1
    since = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    try:
        client.table("app_screen_views").delete().eq("kind", "frame").lt("viewed_at", since).execute()
    except Exception as exc:
        print(f"[replays] frame rows not pruned ({type(exc).__name__})")
    print(f"[replays] removed {removed} pictures from {sessions} sessions older than {KEEP_DAYS} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
