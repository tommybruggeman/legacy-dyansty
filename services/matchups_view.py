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


def _game_html(p: dict) -> str:
    """'Q3 8:42 · 24-13 vs MIA · ball' while live, 'Final W 34-20 vs MIA' after (same as the app)."""
    g = p.get("game") or {}
    if not g or g.get("state") == "pre":
        return ""
    vs = f"{'vs' if g.get('home') else '@'} {html.escape(str(g.get('opp') or ''))}"
    score = f"{g.get('score', 0)}-{g.get('opp_score', 0)}"
    if g.get("state") == "post":
        res = "W" if g.get("score", 0) > g.get("opp_score", 0) else "L" if g.get("score", 0) < g.get("opp_score", 0) else "T"
        return f'<span class="bx-game">Final {res} {score} {vs}</span>'
    ball = ""
    if g.get("possession"):
        down = html.escape(str(g.get("down") or ""))
        ball = f' · <span class="bx-rz">RED ZONE {down}</span>' if g.get("red_zone") else f" · has the ball{(' ' + down) if down else ''}"
    return f'<span class="bx-game"><b>Q{g.get("quarter")} {html.escape(str(g.get("clock") or ""))}</b> {score} {vs}{ball}</span>'


def _line_html(p: dict) -> str:
    parts = p.get("line") or []
    if not parts:
        return ""
    tone = {"good": "bx-good", "bad": "bx-bad"}
    return '<span class="bx-line">' + ", ".join(
        f'<span class="{tone.get(x.get("tone"), "")}">{html.escape(str(x.get("text") or ""))}</span>' for x in parts) + "</span>"


def box_score_html(game: dict) -> str:
    """Both lineups of one game (api.box_score.game): points, projection and weekly position rank per starter,
    plus each side's projected total. Same numbers as the app's game screen."""
    def side_html(side: dict) -> str:
        rows = []
        for p in side.get("players") or []:
            proj = p.get("live_proj") if p.get("status") == "playing" and p.get("live_proj") is not None else p.get("proj")
            proj_txt = f"{proj:.1f}" if isinstance(proj, (int, float)) else "–"
            rank = html.escape(str(p.get("rank") or ""))
            status = p.get("status") or ""
            pts = f"{_as_number(p.get('points')):.1f}" if status in ("playing", "final") or p.get("points") else "–"
            live_cls = " live" if status == "playing" else ""
            rows.append(
                f'<div class="bx-row{live_cls}">'
                f'<span class="bx-slot">{html.escape(str(p.get("slot") or ""))}</span>'
                f'<span class="bx-name">{html.escape(str(p.get("name") or ""))}'
                f'<span class="bx-sub">{html.escape(str(p.get("pos") or ""))}{" · " + html.escape(str(p["team"])) if p.get("team") else ""}{" · " + rank if rank else ""}</span>'
                f'{_game_html(p)}{_line_html(p)}</span>'
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
        '.bx-row.live{background:linear-gradient(90deg,rgba(200,155,74,.16),transparent 75%);box-shadow:inset 3px 0 0 #C89B4A;padding-left:6px;border-radius:6px}'
        '.bx-game{display:block;font-size:.72rem;opacity:.75}.bx-game b{color:#4CC38A}.bx-game .bx-rz{color:#ff7d7d;font-weight:800}'
        '.bx-line{display:block;font-size:.74rem;font-weight:600;opacity:.9}.bx-good{color:#4CC38A;font-weight:800}.bx-bad{color:#ef7a7a;font-weight:800}'
        '</style>'
        f'<div class="bx-wrap">{sides}</div>'
    )


# ------------------------------------------------------------
# Playoff weeks (same view as the app, from api.playoffs):
# a mini bracket on top, this round's games (your game and the
# title game in gold), then the loser bracket as compact rows.
# ------------------------------------------------------------

PLAYOFF_CSS = """
<style>
.po-wrap{display:flex;flex-direction:column;gap:12px;color:#F5EBD7}
.po-note{color:#C8BEAD;font-size:13px;opacity:.85}
.po-tree{display:flex;align-items:center;gap:8px;background:#0C1F17;border-radius:16px;padding:12px}
.po-col{flex:1;display:flex;flex-direction:column;gap:6px;min-width:0}
.po-head{color:#C8BEAD;font-size:10px;font-weight:800;letter-spacing:1px}
.po-tg{background:#112B20;border-radius:10px;padding:4px 8px;border:1px solid #1D3328}
.po-tg.lit{border-color:#C89B4A}.po-tg.cup{background:#2A2414}
.po-ts{display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:700;color:#C8BEAD;padding:3px 0}
.po-ts b{margin-left:auto;color:#F5EBD7}.po-ts.me{color:#F5EBD7}.po-ph{color:#8f8a7c;font-size:12px;padding:6px 0}
.po-conn{width:12px;height:60px;border:2px solid #2B3A2F;border-left:0;border-radius:0 8px 8px 0}
.po-cup{font-size:24px}
.po-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
.po-lbl{color:#C89B4A;font-size:11px;font-weight:800;letter-spacing:1.3px;margin:0 4px 6px}.po-lbl.big{color:#E2BC5B;font-size:12px}
.po-card{position:relative;border-radius:16px;background:#0E201A;border:1px solid #1B2E24;overflow:hidden}
.po-card.big{background:linear-gradient(135deg,#2C2512,#132D20)}.po-card.mine{border:1.5px solid #C89B4A}
.po-card.live:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:#4CC38A}
.po-row{display:flex;align-items:center;gap:10px;padding:11px 14px 9px 16px}
.po-lost{opacity:.5}
.po-seed{width:24px;height:24px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;flex:none;
  background:#173225;border:1px solid rgba(200,155,74,.5);color:#C89B4A;font-size:11px;font-weight:900}
.po-seed.s{width:20px;height:20px;font-size:10px;color:#C8BEAD;border-color:#2B3A2F}
.po-name{flex:1;min-width:0}.po-name b{font-size:16px;font-weight:800}
.po-you{font-size:9.5px;font-weight:800;letter-spacing:.6px;color:#C89B4A;background:#2A2A1A;border-radius:6px;padding:2px 6px;margin-left:6px}
.po-rec{font-size:11px;color:#C8BEAD;opacity:.6;margin-left:6px}
.po-legs{display:block;font-size:11px;color:#C8BEAD;opacity:.6}
.po-score{font-size:21px;font-weight:900}.po-score.pre{opacity:.4}
.po-losers{background:#0C1F17;border-radius:14px;padding:10px 14px}
.po-lh{display:flex;justify-content:space-between;color:#C8BEAD;font-size:11px;font-weight:800;letter-spacing:1.3px;margin-bottom:4px}
.po-lh span{opacity:.6;letter-spacing:0;font-weight:600}
.po-mini{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid #18291F;font-size:13px}
.po-mini:last-of-type{border-bottom:0}
.po-side{flex:1;display:flex;align-items:center;gap:6px;color:#C8BEAD;font-weight:700}.po-side.r{justify-content:flex-end}
.po-side em{font-style:normal;color:#F5EBD7;font-weight:800;margin:0 4px}
.po-bye{font-size:11.5px;color:#C8BEAD;opacity:.6;margin-top:6px}
</style>
"""


def _po_first(slot: dict) -> str:
    return html.escape(_first_name(slot.get("name") or ""))


def _po_seed(n, small: bool = False) -> str:
    return f'<span class="po-seed{" s" if small else ""}">{n if n else "–"}</span>'


def _po_tree(view: dict) -> str:
    rounds = sorted({g["round"] for g in view["tree"]})
    last = rounds[-1] if rounds else 0
    out = ['<div class="po-tree">']
    for i, r in enumerate(rounds):
        games = [g for g in view["tree"] if g["round"] == r and (r != last or g.get("place") in (1, None))]
        if i:
            out.append('<div class="po-conn"></div>')
        weeks = games[0]["weeks"] if games else []
        wk = f"WK {weeks[0]}-{weeks[-1]}" if len(weeks) > 1 else f"WK {weeks[0]}" if weeks else ""
        head = "FINAL" if r == last else (games[0]["label"].upper() + "S" if games else "")
        out.append(f'<div class="po-col"><div class="po-head">{head} · {wk}</div>')
        for g in games:
            cls = "po-tg" + (" lit" if g["mine"] else "") + (" cup" if g.get("place") == 1 else "")
            out.append(f'<div class="{cls}">')
            for t in g["teams"]:
                if "roster_id" not in t:
                    out.append(f'<div class="po-ph">{html.escape(t.get("placeholder") or "TBD")}</div>')
                    continue
                lost = g["winner"] is not None and g["winner"] != t["roster_id"]
                champ = g.get("place") == 1 and g["state"] == "final" and g["winner"] == t["roster_id"]
                score = "" if g["state"] == "pre" else f'<b>{t["score"]:.0f}</b>'
                out.append(f'<div class="po-ts{" me" if t["is_me"] else ""}{" po-lost" if lost else ""}">'
                           f'{_po_seed(t["seed"], True)}{_po_first(t)}{" 🏆" if champ else ""}{score}</div>')
            out.append("</div>")
        out.append("</div>")
    out.append('<div class="po-cup">🏆</div></div>')
    return "".join(out)


def _po_card(g: dict, leg_text: str) -> str:
    # Gold for the title game and for your game on the road to it (a 3rd-place game just gets the gold border).
    big = g.get("place") == 1 or (g["mine"] and g.get("place") is None)
    label = g["label"].upper()
    title = f"🏆 {label}" if g.get("place") == 1 else f"YOUR {label}" if g["mine"] else label
    cls = "po-card" + (" big" if big else "") + (" mine" if g["mine"] else "") + (" live" if g["state"] == "live" else "")
    rows = []
    for t in g["teams"]:
        if "roster_id" not in t:
            rows.append(f'<div class="po-row">{_po_seed(None)}<span class="po-ph">{html.escape(t.get("placeholder") or "To be decided")}</span></div>')
            continue
        lost = g["winner"] is not None and g["winner"] != t["roster_id"]
        legs = ""
        if len(g["weeks"]) > 1 and g["state"] != "pre":
            legs = " · ".join(f'Wk {w} {"–" if t["legs"][i] is None else format(t["legs"][i], ".1f")}' for i, w in enumerate(g["weeks"]))
            legs = f'<span class="po-legs">{legs}</span>'
        you = '<span class="po-you">YOU</span>' if t["is_me"] else ""
        rec = f'<span class="po-rec">{html.escape(t["record"])}</span>' if t.get("record") else ""
        arrow = " ◀" if g["winner"] == t["roster_id"] else ""
        rows.append(f'<div class="po-row{" po-lost" if lost else ""}">{_po_seed(t["seed"])}'
                    f'<div class="po-name"><b>{_po_first(t)}</b>{you}{rec}{legs}</div>'
                    f'<span class="po-score{" pre" if g["state"] == "pre" else ""}">{t["score"]:.2f}{arrow}</span></div>')
    return (f'<div><div class="po-lbl{" big" if big else ""}">{title}{leg_text}</div>'
            f'<div class="{cls}">{"".join(rows)}</div></div>')


def _po_losers(view: dict) -> str:
    out = [f'<div class="po-losers"><div class="po-lh">LOSER BRACKET<span>Round {view["round"]}</span></div>']
    for g in view["losers"]:
        sides = []
        for k, t in enumerate(g["teams"]):
            right = k == 1
            if "roster_id" not in t:
                sides.append(f'<div class="po-side{" r" if right else ""}"><span class="po-ph">{html.escape(t.get("placeholder") or "TBD")}</span></div>')
                continue
            lost = g["winner"] is not None and g["winner"] != t["roster_id"]
            score = "" if g["state"] == "pre" else f'<em>{t["score"]:.1f}</em>'
            bits = [_po_seed(t["seed"], True), _po_first(t), score]
            if right:
                bits.reverse()
            sides.append(f'<div class="po-side{" r" if right else ""}{" po-lost" if lost else ""}">{"".join(bits)}</div>')
        out.append(f'<div class="po-mini">{sides[0]}<span style="opacity:.5">–</span>{sides[1]}</div>')
    if view["byes"]:
        out.append(f'<div class="po-bye">Bye: {", ".join(_po_first(t) for t in view["byes"])}</div>')
    out.append("</div>")
    return "".join(out)


def playoff_html(view: dict) -> str:
    """The playoff week view (api.playoffs.build) as HTML for the website."""
    leg_text = f' · WEEK {view["leg"]} OF {view["legs"]}' if view["legs"] > 1 else ""
    parts = [PLAYOFF_CSS, '<div class="po-wrap">']
    if view["projected"]:
        parts.append(f'<div class="po-note">If the season ended today. Seeds lock after week {view["start"] - 1}.</div>')
    parts.append(_po_tree(view))
    parts.append('<div class="po-grid">' + "".join(_po_card(g, leg_text) for g in view["games"]) + "</div>")
    if view["losers"]:
        parts.append(_po_losers(view))
    parts.append("</div>")
    return "".join(parts)
