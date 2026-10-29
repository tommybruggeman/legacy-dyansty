"""Weekly matchups: turn one Sleeper matchups week into games, and
render the matchup cards for the Weekly Matchups page.

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


def _grid_columns(count: int) -> int:
    """Columns for the desktop grid that leave no half-empty row
    when that can be helped: 4 games -> 2x2, 6 -> 3x2."""

    if count % 3 == 0:
        return 3

    if count % 2 == 0:
        return 2

    return 3


def _my_team_note(me: dict, summary: dict | None) -> str:
    """Where the signed-in owner stands against the Top 5 line."""

    if not summary or me["score"] <= 0:
        return ""

    cut = summary["cut"]

    if me["top5"]:
        return "You're in the Top 5 this week"

    gap = cut["score"] - me["score"]

    return f"{gap:.2f} behind the Top 5 line ({cut['score']:.2f})"


def _hero_html(game: dict, my_roster_id, summary, status, badge) -> str:
    """Desktop: your matchup as a wide head-to-head card."""

    leader, trailer = game["teams"]
    decided = leader["score"] > trailer["score"]
    total = leader["score"] + trailer["score"]
    me = next(
        t for t in game["teams"] if str(t["roster_id"]) == str(my_roster_id)
    )

    # Keep your side on the left.
    left, right = (me, trailer if me is leader else leader)
    left_share = 50.0 if total <= 0 else round(100 * left["score"] / total, 1)

    def side(team, align):
        classes = f"mm-hero-side {align}"

        if decided and team is leader:
            classes += " is-winner"

        rank_class = "mm-rank top5" if team["top5"] else "mm-rank"
        you = '<span class="mm-you">You</span>' if team is me else ""

        return (
            f'<div class="{classes}">'
            '<div class="mm-hero-who">'
            f'<div class="{rank_class}">{team["rank"]}</div>'
            f'<span class="mm-name-text">{html.escape(team["name"])}</span>{you}'
            "</div>"
            f'<div class="mm-hero-score">{team["score"]:.2f}</div>'
            "</div>"
        )

    note = _my_team_note(me, summary)
    note_html = f'<span class="mm-hero-note">{html.escape(note)}</span>' if note else ""

    return (
        '<div class="mm-hero">'
        '<div class="mm-hero-top">'
        '<span class="mm-hero-label">Your matchup</span>'
        f'<span class="mm-badge {badge.lower()}">{badge}</span>'
        "</div>"
        '<div class="mm-hero-body">'
        f'{side(left, "left")}'
        '<div class="mm-hero-vs">vs</div>'
        f'{side(right, "right")}'
        "</div>"
        '<div class="mm-hero-bar">'
        f'<div class="mm-hero-bar-fill" style="width:{left_share}%"></div>'
        "</div>"
        '<div class="mm-hero-foot">'
        f"<span>{status}</span>{note_html}"
        "</div>"
        "</div>"
    )


def _badge(game: dict, is_final: bool) -> str:
    leader, trailer = game["teams"]

    if is_final:
        return "Final"

    if leader["score"] == 0 and trailer["score"] == 0:
        return "Upcoming"

    return "Live"


def matchup_cards_html(
    games: list[dict],
    my_roster_id=None,
    is_final: bool = False,
) -> str:
    """The week's summary boxes, then one card per game.
    The signed-in owner's game comes first: a normal card on phones,
    a wide head-to-head card above the grid on desktop."""

    def is_mine(game):
        return my_roster_id is not None and any(
            str(t["roster_id"]) == str(my_roster_id) for t in game["teams"]
        )

    ordered = sorted(games, key=lambda g: not is_mine(g))
    summary = week_summary(games)
    parts = ['<div class="mm-wrapper">']

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

    mine_game = next((g for g in ordered if is_mine(g)), None)

    if mine_game:
        parts.append(
            _hero_html(
                mine_game,
                my_roster_id,
                summary,
                _status_line(mine_game, is_final),
                _badge(mine_game, is_final),
            )
        )

    desktop_count = len(ordered) - (1 if mine_game else 0)
    parts.append(f'<div class="mm-games cols-{_grid_columns(desktop_count)}">')

    for game in ordered:
        mine = is_mine(game)
        leader, trailer = game["teams"]
        decided = leader["score"] > trailer["score"]
        card_class = "mm-card is-mine phone-only" if mine else "mm-card"

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
        badge = _badge(game, is_final)

        parts.append(
            '<div class="mm-status">'
            f"<span>{status}</span>"
            f'<span class="mm-badge {badge.lower()}">{badge}</span>'
            "</div>"
        )
        parts.append("</div>")

    parts.append("</div>")

    if summary:
        parts.append(
            '<div class="mm-hint">Gold rank = a Top 5 score this week</div>'
        )

    parts.append("</div>")

    return "".join(parts)


def box_score_html(game: dict) -> str:
    """Both lineups of one game (api.box_score.game): points, projection and weekly position rank per starter,
    plus each side's projected total. Same numbers as the app's game screen."""
    def side_html(side: dict) -> str:
        rows = []
        for p in side.get("players") or []:
            proj = p.get("proj")
            proj_txt = f"{proj:.1f}" if isinstance(proj, (int, float)) else "–"
            rank = html.escape(str(p.get("rank") or ""))
            status = p.get("status") or ""
            pts = f"{_as_number(p.get('points')):.1f}" if status in ("playing", "final") or p.get("points") else "–"
            rows.append(
                '<div class="bx-row">'
                f'<span class="bx-slot">{html.escape(str(p.get("slot") or ""))}</span>'
                f'<span class="bx-name">{html.escape(str(p.get("name") or ""))}'
                f'<span class="bx-sub">{html.escape(str(p.get("pos") or ""))}{" · " + html.escape(str(p["team"])) if p.get("team") else ""}{" · " + rank if rank else ""}</span></span>'
                f'<span class="bx-num">{pts}<span class="bx-sub">proj {proj_txt}</span></span>'
                '</div>'
            )
        projected = side.get("projected")
        head = (f'<div class="bx-head"><b>{html.escape(str(side.get("name") or ""))}</b>'
                f'<span>{_as_number(side.get("score")):.1f}'
                + (f'<span class="bx-sub">proj {projected:.1f}</span>' if isinstance(projected, (int, float)) else "")
                + '</span></div>')
        return f'<div class="bx-side">{head}{"".join(rows)}</div>'

    sides = "".join(side_html(s) for s in (game.get("teams") or [])[:2])
    return (
        '<style>'
        '.bx-wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:6px 0 18px}'
        '@media (max-width:700px){.bx-wrap{grid-template-columns:1fr}}'
        '.bx-side{border:1px solid rgba(226,188,91,.22);border-radius:14px;padding:10px 12px;background:rgba(255,255,255,.025)}'
        '.bx-head{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:6px;margin-bottom:4px;border-bottom:1px solid rgba(226,188,91,.18);color:#F5EBD7}'
        '.bx-head span{font-weight:800;text-align:right}'
        '.bx-row{display:grid;grid-template-columns:38px 1fr auto;gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.05);color:#F5EBD7}'
        '.bx-row:last-child{border-bottom:0}'
        '.bx-slot{font-size:.7rem;font-weight:800;color:#E2BC5B;letter-spacing:.06em}'
        '.bx-name{font-size:.9rem;line-height:1.2}'
        '.bx-num{font-weight:700;text-align:right;line-height:1.2}'
        '.bx-sub{display:block;font-size:.72rem;font-weight:400;opacity:.55}'
        '</style>'
        f'<div class="bx-wrap">{sides}</div>'
    )
