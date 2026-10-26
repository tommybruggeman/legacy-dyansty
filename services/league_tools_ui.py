"""League Tools home (tool cards + chip bar) and the one League Rules page.

League Rules writes both rule stores in one save: ``league_rules`` (cap,
contracts, auction, rookie deals) drives cap space and contract terms
across the app, and ``league_ai_settings`` (roster, IR/taxi, FAAB,
standings) is what the League AI reads alongside it. Editing them in one
place keeps the app and the AI on the same rules.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Mapping

import streamlit as st

from league_ai.settings_store import load_settings, save_settings
from services.offseason_transactions import resolve_rookie_contract_terms

# (key, icon, title, description)
TOOLS: tuple[tuple[str, str, str, str], ...] = (
    ("rules", "📘", "League rules", "Cap, roster, IR & taxi, contracts, auction, standings"),
    ("contracts", "✍️", "Contracts", "Look up any player's current deal"),
    ("add_drop", "➕", "Add / Drop", "Manual add, or release with dead cap"),
    ("trades", "🔁", "Trades", "Record or fix a trade"),
    ("sync", "🔄", "Sleeper sync", "Pause, resume and check the 15-minute sync"),
    ("rollover", "📅", "Season rollover", "Move the league to next season"),
    ("ai", "🧠", "League AI", "Scoring sync, house rules and weekly report"),
)
TITLES = {key: title for key, _, title, _ in TOOLS}
ALL = "All tools"

# The database charges IR and taxi players at half their salary today
# (persist_injured_reserve_assignment_authenticated and the taxi
# assignment functions). The Rules page shows that value instead of an
# editable one so the AI is never told a different number.
DATABASE_IR_TAXI_FRACTION = 0.5

_CSS = """
<style>
/* Tool cards: an HTML card with an invisible full-size button on top. */
[class*="st-key-lmcard_"] { position: relative; }
[class*="st-key-lmcard_"] .lm-card {
    background: #0C1917;
    border: 1px solid rgba(245,235,215,.08);
    border-radius: 16px;
    padding: 14px 16px;
    min-height: 104px;
    transition: border-color .15s ease;
}
[class*="st-key-lmcard_"]:hover .lm-card { border-color: rgba(226,188,91,.55); }
.lm-card .i { font-size: 1.25rem; line-height: 1; }
.lm-card .t { font-weight: 600; font-size: .98rem; margin-top: .45rem; color: #F5EBD7; }
.lm-card .d { color: #9DA89C; font-size: .8rem; margin-top: .15rem; line-height: 1.35; }
[class*="st-key-lmcard_"] [data-testid="stElementContainer"]:has(button) {
    position: absolute !important;
    inset: 0;
    margin: 0 !important;
    z-index: 2;
}
[class*="st-key-lmcard_"] [data-testid="stElementContainer"]:has(button) > div,
[class*="st-key-lmcard_"] button {
    width: 100% !important;
    height: 100% !important;
    opacity: 0 !important;
}
[class*="st-key-lmcard_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }

/* Two per row on phones too: keep these column rows from stacking. */
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-lmcard_"]),
[class*="st-key-rules_group_"] [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: .6rem !important;
}
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-lmcard_"]) > [data-testid="stColumn"],
[class*="st-key-rules_group_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    min-width: 0 !important;
    width: auto !important;
    flex: 1 1 0 !important;
}
[class*="st-key-rules_group_"] [data-testid="stNumberInputStepDown"],
[class*="st-key-rules_group_"] [data-testid="stNumberInputStepUp"] { display: none !important; }
[class*="st-key-rules_group_"] [data-testid="stNumberInputContainer"],
[class*="st-key-rules_group_"] [data-baseweb="input"] {
    border-radius: 12px !important;
    background: #0f201b !important;
    border-color: rgba(245,235,215,.1) !important;
}
[class*="st-key-rules_group_"] input { background: transparent !important; font-weight: 600; }
[class*="st-key-rules_group_"] label p { font-size: .78rem !important; color: #9DA89C !important; }
@media (max-width: 640px) {
    .lm-card { min-height: 118px !important; padding: 12px !important; }
    .lm-card .d { font-size: .74rem !important; }
}

/* Chip bar between tools. */
.st-key-lm_tool_chip [role="radiogroup"] {
    border: 0 !important;
    background: transparent !important;
    padding: 0 !important;
    flex-wrap: nowrap !important;
    overflow-x: auto;
    scrollbar-width: none;
    gap: 6px !important;
}
.st-key-lm_tool_chip [role="radiogroup"]::-webkit-scrollbar { display: none; }
.st-key-lm_tool_chip button {
    border-radius: 999px !important;
    border: 1px solid rgba(200,155,74,.35) !important;
    background: transparent !important;
    flex: none !important;
    min-height: 34px !important;
}
.st-key-lm_tool_chip button p { white-space: nowrap; font-size: .85rem !important; }
.st-key-lm_tool_chip button[aria-checked="true"] {
    background: #E2BC5B !important;
    border-color: #E2BC5B !important;
}
.st-key-lm_tool_chip button[aria-checked="true"] p { color: #1a1405 !important; font-weight: 600 !important; }

/* Rules page groups. */
[class*="st-key-rules_group_"] {
    background: #0C1917;
    border: 1px solid rgba(245,235,215,.08);
    border-radius: 18px;
    padding: 14px 16px 6px 16px;
}
.rules-h { font-weight: 600; font-size: 1rem; color: #F5EBD7; margin-bottom: .1rem; }
.rules-sub { color: #9DA89C; font-size: .8rem; margin-bottom: .4rem; }
.rules-note {
    background: rgba(226,188,91,.08);
    border: 1px solid rgba(200,155,74,.28);
    border-radius: 12px;
    padding: 9px 12px;
    font-size: .85rem;
    color: #e9dcc0;
    margin-bottom: .6rem;
}
.rookie-scale { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: .2rem 0 .6rem 0; }
.rookie-scale div { background: #0f201b; border: 1px solid rgba(245,235,215,.08); border-radius: 12px; padding: 8px 10px; }
.rookie-scale b { display: block; color: #E2BC5B; font-size: .75rem; letter-spacing: .08em; }
.rookie-scale span { color: #F5EBD7; font-size: .85rem; }
</style>
"""


def _select(key: str | None) -> None:
    st.session_state["lm_tool"] = key
    st.session_state["lm_tool_chip"] = TITLES[key] if key else ALL


def _chip_changed() -> None:
    choice = st.session_state.get("lm_tool_chip")
    match = next((k for k, title in TITLES.items() if title == choice), None)
    st.session_state["lm_tool"] = match


def render_tool_picker() -> str | None:
    """Tool cards when no tool is open, a chip bar when one is.

    Returns the open tool's key, or None while the cards are showing.
    """
    st.markdown(_CSS, unsafe_allow_html=True)
    tool = st.session_state.get("lm_tool")
    if tool not in TITLES:
        tool = None

    if tool is None:
        for row_start in range(0, len(TOOLS), 2):
            cols = st.columns(2)
            for col, (key, icon, title, desc) in zip(cols, TOOLS[row_start:row_start + 2]):
                with col:
                    with st.container(key=f"lmcard_{key}"):
                        st.markdown(
                            f'<div class="lm-card"><div class="i">{icon}</div>'
                            f'<div class="t">{html.escape(title)}</div>'
                            f'<div class="d">{html.escape(desc)}</div></div>',
                            unsafe_allow_html=True,
                        )
                        st.button(title, key=f"lmcard_btn_{key}", on_click=_select, args=(key,))
        return None

    if st.session_state.get("lm_tool_chip") != TITLES[tool]:
        st.session_state["lm_tool_chip"] = TITLES[tool]
    st.segmented_control(
        "Tools",
        [ALL, *TITLES.values()],
        key="lm_tool_chip",
        on_change=_chip_changed,
        label_visibility="collapsed",
    )
    return tool


# ------------------------------------------------------------ League rules

def _group(key: str, title: str, subtitle: str = ""):
    box = st.container(key=f"rules_group_{key}")
    with box:
        st.markdown(
            f'<div class="rules-h">{title}</div>'
            + (f'<div class="rules-sub">{subtitle}</div>' if subtitle else ""),
            unsafe_allow_html=True,
        )
    return box


def _f(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _rookie_scale_html(rules: Mapping[str, Any]) -> str:
    cells = []
    for rnd in (1, 2, 3):
        try:
            first = resolve_rookie_contract_terms(rules, rnd, 1)
            last = resolve_rookie_contract_terms(rules, rnd, 10)
        except ValueError:
            return '<div class="rules-sub">Rookie scale is off, so draft picks have no set salary.</div>'
        price = f"${first.salary:g}" if first.salary == last.salary else f"${first.salary:g}–${last.salary:g}"
        cells.append(
            f"<div><b>ROUND {rnd}</b><span>{price} · {first.years} yr{'s' if first.years > 1 else ''}"
            f" · ${first.option_salary:g} option</span></div>"
        )
    return '<div class="rookie-scale">' + "".join(cells) + "</div>"


def render_league_rules(client: Any, league_id: str, rules: Mapping[str, Any]) -> None:
    ai = load_settings(client, league_id)
    if st.session_state.pop("league_rules_saved", False):
        st.success("League rules saved. Cap space and the League AI use them now.")
    st.markdown(
        '<div class="rules-note">One source of truth: these rules drive cap space and contracts '
        "across the app, and the League AI reads them too.</div>",
        unsafe_allow_html=True,
    )

    with st.form("league_rules_form", border=False):
        with _group("cap", "Cap &amp; contracts"):
            c1, c2 = st.columns(2)
            salary_cap = c1.number_input("Salary cap ($)", min_value=0, value=int(_f(rules.get("salary_cap"), 225)))
            max_years = c2.number_input("Max contract years", min_value=1, max_value=10,
                                        value=int(_f(rules.get("max_contract_years"), 4)))
            dead_pct = c1.number_input("Dead cap on drops (%)", min_value=0.0, max_value=100.0, step=5.0,
                                       value=_f(rules.get("default_dead_cap_pct"), 50))
            fa_years = c2.number_input("Default FA years", min_value=1, max_value=5,
                                       value=int(_f(rules.get("default_fa_years"), 1)))
            fa_salary = c1.number_input("Default FA salary ($)", min_value=0.0, step=1.0,
                                        value=_f(rules.get("default_fa_salary"), 1))
            waiver_salary = c2.number_input("Default waiver salary ($)", min_value=0.0, step=1.0,
                                            value=_f(rules.get("default_waiver_salary"), 1))
            one_dollar = st.toggle("$1 deals carry no dead cap", value=bool(ai.one_dollar_deals_no_dead_cap))

        with _group("roster", "Roster, IR &amp; taxi",
                    "IR and taxi players count at half their salary. That rate is set in the database today."):
            c1, c2 = st.columns(2)
            roster_max = c1.number_input("Roster max (incl. IR/taxi)", min_value=1, max_value=60, value=int(ai.roster_max))
            ir_limit = c2.number_input("IR spots", min_value=0, max_value=10, value=int(ai.ir_limit))
            taxi_limit = c1.number_input("Taxi spots", min_value=0, max_value=10, value=int(ai.taxi_limit))
            c2.number_input("IR counts against cap (%)", value=int(DATABASE_IR_TAXI_FRACTION * 100), disabled=True,
                            key="rules_ir_pct")
            c1.number_input("Taxi counts against cap (%)", value=int(DATABASE_IR_TAXI_FRACTION * 100), disabled=True,
                            key="rules_taxi_pct")
            taxi_rookies = st.toggle("Taxi is rookies only", value=bool(ai.taxi_rookies_only))
            taxi_locked = st.toggle("Taxi locks for the season", value=bool(ai.taxi_locked_full_season))
            taxi_skips = st.toggle("Taxi season skips a contract year", value=bool(ai.taxi_skips_contract_year))

        with _group("fa", "Free agency &amp; auction",
                    "The FA Auction tab uses these minimums and the per-year discount."):
            c1, c2 = st.columns(2)
            min2 = c1.number_input("2-year minimum bid ($)", min_value=0.0, step=1.0, value=_f(rules.get("min_2_year_bid"), 4))
            min3 = c2.number_input("3-year minimum bid ($)", min_value=0.0, step=1.0, value=_f(rules.get("min_3_year_bid"), 12))
            min4 = c1.number_input("4-year minimum bid ($)", min_value=0.0, step=1.0, value=_f(rules.get("min_4_year_bid"), 20))
            discount = c2.number_input("Per-year discount (%)", min_value=0.0, max_value=100.0, step=1.0,
                                       value=_f(rules.get("year_discount_pct"), 10))
            faab_per = c1.number_input("Salary per FAAB $", min_value=0.0, max_value=10.0, step=0.25,
                                       value=float(ai.faab_dollar_per_salary))
            faab_zero = c2.number_input("$0 FAAB win counts as ($)", min_value=0.0, max_value=10.0, step=1.0,
                                        value=float(ai.faab_zero_counts_as))
            faab_years = c1.number_input("FAAB pickup years", min_value=1, max_value=5, value=int(ai.faab_pickup_years))
            reset_ok = st.toggle("Allow auction reset", value=bool(rules.get("auction_reset_allowed", True)))
            traded_faab = st.toggle("Traded FAAB moves cap", value=bool(ai.traded_faab_moves_cap))

        with _group("rookie", "Rookie draft", "Each pick's salary comes from the slot scale below."):
            st.markdown(_rookie_scale_html(rules), unsafe_allow_html=True)
            scale_on = st.toggle("Use rookie salary scale", value=bool(rules.get("rookie_scale_enabled", True)))
            scale_cap = st.toggle("Scale rookie salaries with the cap",
                                  value=bool(rules.get("scale_rookie_salaries_with_cap", False)))
            c1, c2 = st.columns(2)
            rookie_years = c1.number_input("Rookie contract years", min_value=1, max_value=5,
                                           value=int(_f(rules.get("rookie_contract_years"), 3)))
            option_years = c2.number_input("Rookie option years", min_value=0, max_value=3,
                                           value=int(_f(rules.get("rookie_option_years"), 1)))

        with _group("standings", "Standings &amp; trades"):
            c1, c2 = st.columns(2)
            win_points = c1.number_input("Points per win", min_value=0, max_value=10, value=int(ai.win_points))
            bonus_points = c2.number_input("Top-scorer bonus points", min_value=0, max_value=10,
                                           value=int(ai.top_scorer_bonus_points))
            bonus_count = c1.number_input("Bonus goes to the top", min_value=0, max_value=12,
                                          value=int(ai.top_scorer_bonus_count))
            playoff_teams = c2.number_input("Playoff teams", min_value=0, max_value=12, value=int(ai.playoff_teams))
            tiebreaker = c1.text_input("Standings tiebreaker", value=ai.standings_tiebreaker)
            deadline = c2.text_input("Trade deadline (e.g. week 12)", value=ai.trade_deadline)
            max_trade_teams = c1.number_input("Max teams per trade", min_value=2, max_value=12, value=int(ai.max_trade_teams))

        saved = st.form_submit_button("Save rules", use_container_width=True, type="primary")

    if not saved:
        return

    try:
        client.table("league_rules").upsert(
            {
                "league_id": league_id,
                "salary_cap": salary_cap,
                "max_contract_years": max_years,
                "default_dead_cap_pct": dead_pct,
                "default_fa_years": fa_years,
                "default_fa_salary": fa_salary,
                "default_waiver_salary": waiver_salary,
                "rookie_contract_years": rookie_years,
                "rookie_option_years": option_years,
                "rookie_scale_enabled": scale_on,
                "scale_rookie_salaries_with_cap": scale_cap,
                "min_2_year_bid": min2,
                "min_3_year_bid": min3,
                "min_4_year_bid": min4,
                "year_discount_pct": discount,
                "auction_reset_allowed": reset_ok,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="league_id",
        ).execute()
        ai.one_dollar_deals_no_dead_cap = one_dollar
        ai.roster_max = int(roster_max)
        ai.ir_limit = int(ir_limit)
        ai.taxi_limit = int(taxi_limit)
        ai.ir_cap_fraction = DATABASE_IR_TAXI_FRACTION
        ai.taxi_cap_fraction = DATABASE_IR_TAXI_FRACTION
        ai.taxi_rookies_only = taxi_rookies
        ai.taxi_locked_full_season = taxi_locked
        ai.taxi_skips_contract_year = taxi_skips
        ai.faab_dollar_per_salary = float(faab_per)
        ai.faab_zero_counts_as = float(faab_zero)
        ai.faab_pickup_years = int(faab_years)
        ai.traded_faab_moves_cap = traded_faab
        ai.win_points = int(win_points)
        ai.top_scorer_bonus_points = int(bonus_points)
        ai.top_scorer_bonus_count = int(bonus_count)
        ai.playoff_teams = int(playoff_teams)
        ai.standings_tiebreaker = tiebreaker
        ai.trade_deadline = deadline
        ai.max_trade_teams = int(max_trade_teams)
        save_settings(client, ai)
    except Exception as exc:
        st.error(f"Rules not saved: {exc}")
        return
    st.cache_data.clear()
    st.session_state["league_rules_saved"] = True
    st.rerun()


# ------------------------------------------------------------ Contracts

def render_contract_lookup(client: Any, league_id: str, season: int) -> None:
    """Read-only lookup of any rostered player's current deal.

    The old Contract Editor only wrote to an audit table that was never
    created, so nothing it "logged" ever changed a contract. Editing needs a
    database function that rewrites contract seasons safely; until that
    exists this shows the real numbers instead of a form that does nothing.
    """
    from services.commissioner_draft_ui import _epoch, _team_state
    from services.team_roster_state import state_roster

    try:
        roster = state_roster(_team_state(client, league_id, int(season), _epoch()))
    except Exception as exc:
        st.error(f"Contracts could not be loaded: {exc}")
        return
    rows = sorted(
        (r for r in roster if r.get("player")),
        key=lambda r: str(r.get("player") or "").casefold(),
    )
    labels = {
        f"{r.get('player')} · {r.get('owner') or r.get('owner_name') or '?'}": r for r in rows
    }
    choice = st.selectbox(
        "Player", list(labels), index=None, placeholder="Search any rostered player",
        label_visibility="collapsed", key="contract_lookup_player",
    )
    st.caption(
        "Editing salary or years isn't connected to the database yet, so this shows each "
        "player's current deal. Use Add / Drop to release and re-sign a player."
    )
    if not choice:
        return
    r = labels[choice]

    def money(value: Any) -> str:
        try:
            return f"${float(value):g}"
        except (TypeError, ValueError):
            return "—"

    designation = {"ir": "IR", "taxi": "Taxi"}.get(str(r.get("roster_designation") or ""), "Active")
    cells = [
        ("Owner", html.escape(str(r.get("owner") or r.get("owner_name") or "—"))),
        ("Salary", money(r.get("contract_cap_hit") or r.get("salary"))),
        ("Counts against cap", money(r.get("cap_hit") or r.get("salary"))),
        ("Years left", html.escape(str(r.get("years") if r.get("years") is not None else "—"))),
        ("Status", designation),
        ("Position", html.escape(str(r.get("player_position") or "—"))),
    ]
    st.markdown(
        '<div class="rookie-scale">'
        + "".join(f"<div><b>{k.upper()}</b><span>{v}</span></div>" for k, v in cells)
        + "</div>",
        unsafe_allow_html=True,
    )
