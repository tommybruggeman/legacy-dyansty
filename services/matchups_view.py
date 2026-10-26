"""Weekly matchups: turn one Sleeper matchups week into games, and
render the phone layout for the Weekly Matchups page.

Kept free of Streamlit and network calls so it can be tested directly.

- Teams are keyed by Sleeper roster id, never by display name.
- Weekly rank and Top 5 follow services.standings_rules: highest score
  first, ties broken by name, and the top five scorers get a Top 5.
"""

from __future__ import annotations

import html

from services.standings_rules import TOP_FIVE_COUNT


def _as_number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _team_points(row: dict) -> float:
    points = _as_number(row.get("points"))
    starters = row.get("starters_points")

    # Sleeper sometimes leaves "points" near zero while starters are
    # already scored; fall back to the starters total in that case.
    if isinstance(starters, (list, tuple)):
        starters = sum(_as_number(p) for p in starters)
    else:
        starters = _as_number(starters)

    if points < 10.0 <= starters:
        points = starters

    return round(points, 2)


def build_games(rows: list[dict], names: dict) -> list[dict]:
    """Group a Sleeper matchups week into head-to-head games.

    Returns one dict per game with ``teams``: two dicts holding
    ``roster_id``, ``name``, ``score``, ``rank`` and ``top5``.
    Games are ordered by their best score.
    """

    by_matchup: dict = {}

    for row in rows or []:
        matchup_id = row.get("matchup_id")

        if matchup_id is None:
            continue

        by_matchup.setdefault(matchup_id, []).append(row)

    games = []

    for matchup_id, pair in by_matchup.items():
        if len(pair) < 2:
            continue

        teams = []

        for row in pair[:2]:
            roster_id = row.get("roster_id")
            teams.append(
                {
                    "roster_id": roster_id,
                    "name": str(names.get(roster_id) or f"Roster {roster_id}"),
                    "score": _team_points(row),
                }
            )

        games.append({"matchup_id": matchup_id, "teams": teams})

    everyone = [team for game in games for team in game["teams"]]
    ordered = sorted(everyone, key=lambda t: (-t["score"], t["name"]))
    scored = any(team["score"] > 0 for team in everyone)

    for rank, team in enumerate(ordered, start=1):
        team["rank"] = rank
        team["top5"] = scored and rank <= TOP_FIVE_COUNT

    for game in games:
        game["teams"].sort(key=lambda t: t["rank"])

    games.sort(key=lambda g: g["teams"][0]["rank"])

    return games


def week_summary(games: list[dict]) -> dict | None:
    """High score, the Top 5 cut line and the closest game, or None
    when nobody has scored yet."""

    everyone = [team for game in games for team in game["teams"]]

    if not any(team["score"] > 0 for team in everyone):
        return None

    ordered = sorted(everyone, key=lambda t: t["rank"])
    cut = ordered[min(TOP_FIVE_COUNT, len(ordered)) - 1]
    closest = min(
        games,
        key=lambda g: abs(g["teams"][0]["score"] - g["teams"][1]["score"]),
    )

    return {
        "high": ordered[0],
        "cut": cut,
        "closest": closest,
        "closest_margin": abs(
            closest["teams"][0]["score"] - closest["teams"][1]["score"]
        ),
    }


def _first_name(name: str) -> str:
    return name.split()[0] if name.split() else name


def _status_line(game: dict, is_final: bool) -> str:
    leader, trailer = game["teams"]

    if leader["score"] == 0 and trailer["score"] == 0:
        return "Not started"

    margin = leader["score"] - trailer["score"]
    who = html.escape(_first_name(leader["name"]))

    if margin == 0:
        return "Tied" if is_final else "All square"

    verb = "won by" if is_final else "leads by"

    return f"{who} {verb} {margin:.2f}"


def _team_row(team: dict, is_winner: bool, is_me: bool) -> str:
    classes = ["mm-team"]

    if is_winner:
        classes.append("is-winner")

    if is_me:
        classes.append("is-me")

    rank_class = "mm-rank top5" if team["top5"] else "mm-rank"
    you = '<span class="mm-you">You</span>' if is_me else ""
    name = html.escape(team["name"])

    return (
        f'<div class="{" ".join(classes)}">'
        f'<div class="{rank_class}">{team["rank"]}</div>'
        f'<div class="mm-name"><span class="mm-name-text">{name}</span>{you}</div>'
        f'<div class="mm-score">{team["score"]:.2f}</div>'
        "</div>"
    )


def matchup_cards_html(
    games: list[dict],
    my_roster_id=None,
    is_final: bool = False,
) -> str:
    """Phone layout: the week's summary boxes, then one card per game.
    The signed-in owner's game comes first."""

    def is_mine(game):
        return my_roster_id is not None and any(
            str(t["roster_id"]) == str(my_roster_id) for t in game["teams"]
        )

    ordered = sorted(games, key=lambda g: not is_mine(g))
    summary = week_summary(games)
    parts = ['<div class="mobile-matchups-wrapper">']

    if summary:
        high = summary["high"]
        cut = summary["cut"]
        closest = summary["closest"]
        names = " v ".join(
            html.escape(_first_name(t["name"])) for t in closest["teams"]
        )
        boxes = [
            ("High score", f'{high["score"]:.2f}', html.escape(high["name"])),
            ("Top 5 line", f'{cut["score"]:.2f}', html.escape(cut["name"])),
            ("Closest", f'{summary["closest_margin"]:.2f}', names),
        ]
        parts.append('<div class="mm-summary">')

        for label, value, sub in boxes:
            parts.append(
                '<div class="mm-box">'
                f"<span>{label}</span><b>{value}</b><em>{sub}</em>"
                "</div>"
            )

        parts.append("</div>")

    for game in ordered:
        mine = is_mine(game)
        leader, trailer = game["teams"]
        decided = leader["score"] > trailer["score"]
        card_class = "mm-card is-mine" if mine else "mm-card"

        parts.append(f'<div class="{card_class}">')

        if mine:
            parts.append('<div class="mm-label">Your matchup</div>')

        for team in game["teams"]:
            parts.append(
                _team_row(
                    team,
                    is_winner=decided and team is leader,
                    is_me=mine and str(team["roster_id"]) == str(my_roster_id),
                )
            )

        status = _status_line(game, is_final)

        if is_final:
            badge = "Final"
        elif leader["score"] == 0 and trailer["score"] == 0:
            badge = "Upcoming"
        else:
            badge = "Live"

        parts.append(
            '<div class="mm-status">'
            f"<span>{status}</span>"
            f'<span class="mm-badge {badge.lower()}">{badge}</span>'
            "</div>"
        )
        parts.append("</div>")

    if summary:
        parts.append(
            '<div class="mm-hint">Gold rank = a Top 5 score this week</div>'
        )

    parts.append("</div>")

    return "".join(parts)
