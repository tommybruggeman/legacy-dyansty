"""League standings rules, shared by the live standings page and the snapshot.

- Wins and losses come only from each week's head-to-head matchup.
  A tied matchup is neither a win nor a loss.
- Top 5 is a separate tally: the five highest scorers each week get one.
- Standing points: 2 per win, 1 per top 5 finish.
- Over the cap at Sunday's first kickoff (Tommy, 2026-11-17): that team's week
  is an automatic loss and it can't earn the top 5 point. Its score still
  counts in the top 5 ranking (nobody moves up), and its opponent is scored
  normally: a win only if they outscored it.
- Only finished weeks count. A week still being played (Sleeper has not
  scored it yet) is skipped, so partial Thursday scores never become a
  loss or a top 5.
"""

from __future__ import annotations

WIN_POINTS = 2
TOP_FIVE_POINTS = 1
TOP_FIVE_COUNT = 5


def last_completed_week(league: dict | None, nfl_state: dict | None) -> int:
    """Return the last regular week Sleeper has finished scoring.

    Prefers the league's own ``last_scored_leg``; falls back to the week
    before the current NFL week when the league does not report it.
    """
    settings = (league or {}).get("settings") or {}
    scored = settings.get("last_scored_leg")

    if scored is not None:
        try:
            return max(0, int(scored))
        except (TypeError, ValueError):
            pass

    try:
        week = int((nfl_state or {}).get("week") or 0)
    except (TypeError, ValueError):
        week = 0

    return max(0, week - 1)


def score_week(results: list[tuple[str, float, float]], auto_loss: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Score one week from ``(team, score, opponent_score)`` rows.

    Returns one dict per team with win, loss, tie, top5 and standing_points.
    Top 5 ties on score are broken by team name so the result is stable.
    ``auto_loss``: teams that started Sunday over the cap (a loss, no top 5).
    """
    ranked = sorted(results, key=lambda r: (-r[1], r[0]))
    top_five = {team for team, _, _ in ranked[:TOP_FIVE_COUNT]}

    out = []

    for team, score, opp in results:
        win = 1 if score > opp else 0
        loss = 1 if score < opp else 0
        top5 = 1 if team in top_five else 0
        if team in auto_loss:
            win, loss, top5 = 0, 1, 0

        out.append(
            {
                "team": team,
                "score": score,
                "opp_score": opp,
                "win": win,
                "loss": loss,
                "tie": 1 - win - loss,
                "top5": top5,
                "standing_points": WIN_POINTS * win + TOP_FIVE_POINTS * top5,
            }
        )

    return out
