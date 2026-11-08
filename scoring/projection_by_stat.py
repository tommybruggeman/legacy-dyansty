"""Where does the projection run high? Sleeper's projected stat lines vs real ones, stat by stat, in this league's
scoring, for every QB/RB/WR/TE projected 3+ points in 2026 (finished weeks). Split by players who played and
players who didn't. Numbers only (the repo and its Actions logs are public).

    python -m scoring.projection_by_stat
"""
from __future__ import annotations

from collections import defaultdict


def main() -> None:
    from api.projections import POSITIONS, _rows
    from league_ai.start_sit_grade import _sleeper
    from pipeline.league_history.run import league_chain
    from scoring.engine import player_points

    sc = {k: float(v) for k, v in (league_chain("1361083066197491712")[0].get("scoring_settings") or {}).items() if v}
    print("scoring keys used:", sorted(sc))
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)
    by = defaultdict(lambda: [0.0, 0.0])          # (pos, stat) -> [projected pts, actual pts], players who played
    tot = defaultdict(lambda: [0, 0.0, 0.0])      # (pos, played) -> [n, projected, actual]
    for week in range(1, 9):
        proj = _rows(_sleeper(f"projections/nfl/2026/{week}?{query}"))
        real = _rows(_sleeper(f"stats/nfl/2026/{week}?{query}"))
        for pid, line in proj.items():
            pos = line.get("pos")
            if pos not in ("QB", "RB", "WR", "TE"):
                continue
            ps = line.get("stats") or {}
            p = player_points(ps, sc, pos)
            if p < 3:
                continue
            rs = (real.get(pid) or {}).get("stats") or {}
            a = player_points(rs, sc, pos) if rs else 0.0
            played = bool(rs.get("gp") or rs.get("off_snp"))
            t = tot[(pos, played)]
            t[0] += 1
            t[1] += p
            t[2] += a
            if played:
                for k, w in sc.items():
                    by[(pos, k)][0] += float(ps.get(k) or 0) * w
                    by[(pos, k)][1] += float(rs.get(k) or 0) * w
    for (pos, played), (n, p, a) in sorted(tot.items()):
        print(f"{pos} {'played' if played else 'did not play'}: n={n} projected {p / n:.2f} actual {a / n:.2f} ratio {a / p:.3f}")
    n_played = {pos: tot[(pos, True)][0] for pos in ("QB", "RB", "WR", "TE")}
    print("per player-game, players who played: stat  projected pts  actual pts  diff (biggest gaps first)")
    rows = [(pos, k, v[0] / max(1, n_played[pos]), v[1] / max(1, n_played[pos])) for (pos, k), v in by.items() if abs(v[0]) + abs(v[1]) > 0]
    for pos in ("QB", "RB", "WR", "TE"):
        for _, k, p, a in sorted([r for r in rows if r[0] == pos], key=lambda r: -abs(r[2] - r[3]))[:10]:
            print(f"  {pos} {k:<16} {p:7.2f} {a:7.2f} {a - p:+7.2f}")


if __name__ == "__main__":
    main()
