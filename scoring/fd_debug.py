"""First downs from ESPN's play by play (the live feed) vs Sleeper's, for one finished week (default: last week). For every player whose rush/rec
first downs differ, prints the plays of his that we read and how each was judged. NFL play text only (public)."""
from __future__ import annotations

import sys

from scoring import espn_feed
from scoring.shadow import SLEEPER, _fetch, stat_lines


def main() -> None:
    from pipeline.nfl_data.common import fetch_text
    from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk
    state = _fetch(f"{SLEEPER}/v1/state/nfl") or {}
    season = int(sys.argv[1]) if len(sys.argv) > 2 else int(state.get("season") or 0)
    week = int(sys.argv[2]) if len(sys.argv) > 2 else max(1, int(state.get("week") or 2) - 1)
    players = _fetch(f"{SLEEPER}/v1/players/nfl") or {}
    idmap = espn_feed.IdMap(players, Crosswalk.from_csv(fetch_text(CROSSWALK_URL)))
    board = _fetch(f"{espn_feed.ESPN}/scoreboard?seasontype=2&week={week}&dates={season}") or {}
    theirs = stat_lines(season, week, _fetch)
    for g in espn_feed.games(board):
        if g["state"] != "post":
            continue
        s = _fetch(f"{espn_feed.ESPN}/summary?event={g['id']}") or {}
        drives = s.get("drives") or {}
        nplays = sum(len(d.get("plays") or []) for d in drives.get("previous") or [])
        lines, _ = espn_feed.week_lines([s], idmap)
        bad = []
        for pid, line in lines.items():
            t = (theirs.get(pid) or {}).get("stats") or {}
            for k in ("rush_fd", "rec_fd"):
                a, b = float(line["stats"].get(k) or 0), float(t.get(k) or 0)
                if a != b:
                    bad.append((pid, k, a, b))
        print(f"game {'-'.join(g['teams'])}: {nplays} plays in {len(drives.get('previous') or [])} drives; {len(bad)} first-down gaps", flush=True)
        names = espn_feed.Names()
        for team_box in (s.get("boxscore") or {}).get("players") or []:
            team = espn_feed.team_code(team_box.get("team"))
            for cat in team_box.get("statistics") or []:
                for row in cat.get("athletes") or []:
                    a = row.get("athlete") or {}
                    names.add(team, str(a.get("id")), str(a.get("displayName") or ""))
        for pid, k, a, b in bad[:6]:
            nm = (players.get(pid) or {})
            last = str(nm.get("last_name") or "")
            print(f"  {nm.get('first_name')} {last} {k}: ours {a:g}, Sleeper {b:g}", flush=True)
            for team, play in espn_feed._plays(s):
                text = str(play.get("text") or "")
                if last and last in text:
                    st = play.get("start") or {}
                    print(f"     [{(play.get('type') or {}).get('text')}] dist={st.get('distance')} yds={play.get('statYardage')} :: {text[:150]}", flush=True)


if __name__ == "__main__":
    main()
