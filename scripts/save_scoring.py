"""Save each league's points per stat into our own Settings (league_ai_settings.scoring_settings), copied once from
the league's current Sleeper rules, so scoring, the GM and the commissioner's Rules screen all read rules we own.
Leagues that already have saved rules are left alone. Prints the rules, no owner names."""
from __future__ import annotations

from datetime import datetime, timezone

from league_ai.settings_store import fetch_sleeper_scoring, load_settings, save_settings
from scoring.rules import rows_from_rules
from pipeline.nfl_data.common import service_client


def main() -> None:
    client = service_client()
    seasons = client.table("league_seasons").select("league_id,sleeper_league_id").eq("is_active", True).execute().data or []
    for n, lg in enumerate(seasons, start=1):
        sid = str(lg.get("sleeper_league_id") or "")
        ai = load_settings(client, str(lg["league_id"]))
        if ai.scoring_settings:
            print(f"league {n}: already has {len(ai.scoring_settings)} saved rules, left alone")
        elif not sid:
            print(f"league {n}: no Sleeper league to copy from")
            continue
        else:
            payload = fetch_sleeper_scoring(sid)
            ai.scoring_settings = {k: v for k, v in (payload.get("scoring_settings") or {}).items() if v}
            ai.scoring_source = "sleeper"
            ai.scoring_synced_at = datetime.now(timezone.utc).isoformat()
            if not ai.roster_positions:
                ai.roster_positions = list(payload.get("roster_positions") or [])
            deadline = (payload.get("league_settings") or {}).get("trade_deadline")
            if not ai.trade_deadline and deadline not in (None, "", 0, 99):
                ai.trade_deadline = f"week {deadline}"
            save_settings(client, ai)
            print(f"league {n}: saved {len(ai.scoring_settings)} rules, trade deadline {ai.trade_deadline or 'none'}")
        for row in rows_from_rules(ai.scoring_settings):
            if row["points"]:
                print(f"  {row['group']}: {row['rule']} = {row['points']:g}")


if __name__ == "__main__":
    main()
