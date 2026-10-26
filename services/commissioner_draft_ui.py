"""Draft room and FA auction sections of the League Control page.

Loading is cached per league and bumped by an epoch after every write, so a
pick or award reruns in well under a second. The browser widgets in
services/commissioner_widgets.py do the searching and bid math; this module
loads their data and performs the checked writes.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import streamlit as st

from auth import service_client
from services.commissioner_widgets import draft_room, fa_auction
from services.draft_board import (
    build_slots,
    draft_order,
    on_the_clock,
    short_names,
    slot_payload,
)
from services.fa_auction import auction_rules, is_valid_bid
from services.free_agents import (
    build_commissioner_auction_players,
    build_free_agent_results,
    canonical_live_owners,
    load_commissioner_auction_universe,
    load_league_free_agent_state,
    load_player_universe,
)
from services.offseason_transactions import (
    OffseasonTransactionService,
    resolve_rookie_contract_terms,
)
from services.team_roster_state import (
    calculate_team_financials,
    invalidate_team_state_session_cache,
    load_team_state,
    state_cap_adjustments,
    state_roster,
)

EPOCH_KEY = "commish_data_epoch"


def _epoch() -> int:
    return int(st.session_state.get(EPOCH_KEY, 0))


def _bump() -> None:
    st.session_state[EPOCH_KEY] = _epoch() + 1
    invalidate_team_state_session_cache(st.session_state)


def _flash(kind: str, text: str, slot: str) -> None:
    st.session_state[slot] = {"kind": kind, "text": text, "token": _epoch()}


def _take_flash(slot: str) -> dict | None:
    flash = st.session_state.pop(slot, None)
    return {"kind": flash["kind"], "text": flash["text"]} if flash else None


# ------------------------------------------------------------ cached loaders

@st.cache_data(ttl=900, show_spinner=False)
def _rookies(league_id: str, draft_year: int, epoch: int) -> list[dict]:
    results = build_free_agent_results(
        load_player_universe(service_client()),
        load_league_free_agent_state(service_client(), league_id),
        active_season=draft_year,
    )
    return [
        {
            "id": row.sleeper_player_id,
            "name": row.player,
            "pos": row.position,
            "team": row.nfl_team if row.nfl_team not in ("", "—", "-") else "",
            "college": row.college if row.college not in ("", "—", "-") else "",
            "order": index,
        }
        for index, row in enumerate(results.rookies)
    ]


@st.cache_data(ttl=600, show_spinner=False)
def _standings_rows(sleeper_league_id: str) -> list[dict]:
    from services.app_context import build_standings_from_sleeper

    frame = build_standings_from_sleeper(sleeper_league_id)
    return frame.to_dict("records") if not frame.empty else []


@st.cache_data(ttl=300, show_spinner=False)
def _team_state(_client: Any, league_id: str, season: int, epoch: int) -> dict:
    return load_team_state(_client, league_id, season)


@st.cache_data(ttl=300, show_spinner=False)
def _made_picks(_client: Any, league_id: str, draft_year: int, epoch: int) -> list[dict]:
    return (
        _client.table("rookie_draft_board_assignments")
        .select("player_id,original_league_team_id,draft_year,draft_round,round_pick")
        .eq("league_id", league_id)
        .eq("draft_year", int(draft_year))
        .execute()
        .data
        or []
    )


@st.cache_data(ttl=900, show_spinner=False)
def _auction_universe(epoch: int) -> list[dict]:
    players = build_commissioner_auction_players(
        load_commissioner_auction_universe(service_client())
    )
    # Players on an NFL team first, so "Etienne" finds the starter, not a
    # retired namesake.
    ranked = sorted(players, key=lambda p: (not p.nfl_team or p.nfl_team in ("—", "-", "FA"),))
    return [
        {"id": p.sleeper_player_id, "name": p.player, "pos": p.position,
         "team": p.nfl_team if p.nfl_team not in ("—", "-") else "", "order": i}
        for i, p in enumerate(ranked)
    ]


@st.cache_data(ttl=300, show_spinner=False)
def _contracted_ids(league_id: str, season: int, epoch: int) -> dict[str, str]:
    state = load_league_free_agent_state(service_client(), league_id)
    return dict(canonical_live_owners(state, active_season=season))


# ------------------------------------------------------------ helpers

def _order_source(sb_client: Any, league_id: str, draft_year: int, active_season: int) -> tuple[str | None, str]:
    """Sleeper league whose standings set this draft's order, and a label."""
    from season_engine.resolver import SeasonResolver

    resolver = SeasonResolver(sb_client)
    try:
        if int(draft_year) > int(active_season):
            season = resolver.get_active_season(league_id)
            return season.sleeper_league_id, f"projected from {season.season} standings"
        season = resolver.get_completed_season(league_id)
        return season.sleeper_league_id, f"from final {season.season} standings"
    except Exception:
        return None, "standings unavailable"


def _terms(league_rules: Mapping[str, Any]):
    def terms(rnd: int, pick: int) -> tuple[float, int, float]:
        resolved = resolve_rookie_contract_terms(league_rules, rnd, pick)
        return float(resolved.salary), int(resolved.years), float(resolved.option_salary)
    return terms


def _slots(sb_client, league_id, league_teams, league_rules, draft_year, active_season, rookies, epoch):
    sleeper_id, order_note = _order_source(sb_client, league_id, draft_year, active_season)
    standings = _standings_rows(sleeper_id) if sleeper_id else []
    order = draft_order(standings, league_teams)
    try:
        pick_assets = _team_state(sb_client, league_id, active_season, epoch).get("draft_picks", [])
    except Exception:
        pick_assets = None
    players = {r["id"]: {"player": r["name"], "position": r["pos"]} for r in rookies}
    made = _made_picks(sb_client, league_id, draft_year, epoch)
    missing = [m for m in made if str(m.get("player_id")) not in players]
    if missing:
        # A drafted player may have left the "available rookies" list.
        universe = {p["id"]: p for p in _auction_universe(epoch)}
        for m in missing:
            p = universe.get(str(m.get("player_id")))
            if p:
                players[p["id"]] = {"player": p["name"], "position": p["pos"]}
    slots = build_slots(
        draft_year=draft_year,
        order=order,
        teams=league_teams,
        pick_assets=pick_assets or [],
        made_picks=made,
        players=players,
        terms=_terms(league_rules),
    )
    if pick_assets is None:
        order_note += ". Traded picks could not be loaded, so every pick shows its original owner"
    return slots, order_note, bool(standings)


# ------------------------------------------------------------ draft

def render_draft(
    *,
    sb_client: Any,
    league_id: str,
    league_teams: Sequence[Mapping[str, Any]],
    league_rules: Mapping[str, Any],
    active_season: int,
    draft_years: Sequence[int],
    lifecycle_controls,
) -> None:
    years = list(draft_years)
    default_year = st.session_state.get("draft_room_year")
    if default_year not in years:
        # The next draft that is not finished yet: this offseason's, else next year's.
        default_year = years[1] if len(years) > 1 else years[0]
    draft_year = st.segmented_control(
        "Draft year", years, default=default_year, key="draft_room_year_pick",
        label_visibility="collapsed",
    ) or default_year
    st.session_state["draft_room_year"] = draft_year

    epoch = _epoch()
    rookies = _rookies(league_id, int(draft_year), 0)
    slots, order_note, has_standings = _slots(
        sb_client, league_id, league_teams, league_rules,
        int(draft_year), int(active_season), rookies, epoch,
    )
    clock = on_the_clock(slots)
    taken = {s.player_id for s in slots if s.player_id}

    if "could not be loaded" in order_note:
        st.warning("Traded picks could not be loaded. Check pick owners before drafting.")
    if not has_standings:
        st.caption("Standings aren't available yet, so the order below is alphabetical by owner.")

    data = {
        "year": int(draft_year),
        "token": st.session_state.get("draft_flash", {}).get("token", epoch),
        "flash": _take_flash("draft_flash"),
        "clock": slot_payload(clock) if clock else None,
        "board": [slot_payload(s) for s in slots],
        "rookies": rookies,
        "taken": sorted(taken),
        "left": len([r for r in rookies if r["id"] not in taken]),
    }
    hit = draft_room(data, key=f"draft_room_{draft_year}")
    st.caption(f"Pick order is worst record first each round, {order_note}. Traded picks follow their new owner.")

    if hit:
        _save_pick(sb_client, league_id, league_teams, league_rules, int(draft_year),
                   int(active_season), rookies, hit)
        st.rerun()

    with st.expander("Draft settings"):
        lifecycle_controls(int(draft_year))


def _save_pick(sb_client, league_id, league_teams, league_rules, draft_year, active_season, rookies, hit) -> None:
    player_id = str(hit.get("player_id") or "")
    try:
        rnd, pick = int(hit.get("round")), int(hit.get("pick"))
    except (TypeError, ValueError):
        _flash("err", "That pick could not be read. Try again.", "draft_flash")
        return
    rookie = next((r for r in rookies if r["id"] == player_id), None)

    # Re-read the board from the database, not the cache, before writing.
    _bump()
    slots, _, _ = _slots(sb_client, league_id, league_teams, league_rules,
                         draft_year, active_season, rookies, _epoch())
    slot = next((s for s in slots if s.round == rnd and s.pick == pick), None)
    clock = on_the_clock(slots)
    if rookie is None:
        _flash("err", "That player isn't in this year's rookie class.", "draft_flash")
    elif any(s.player_id == player_id for s in slots):
        _flash("err", f"{rookie['name']} was already drafted.", "draft_flash")
    elif slot is None or slot.made or clock is None or clock.label != slot.label:
        _flash("err", "The board changed. Check who is on the clock and try again.", "draft_flash")
    elif str(hit.get("owner_id") or "") != slot.owner_team_id:
        _flash("err", "This pick's owner changed. Check the board and try again.", "draft_flash")
    else:
        try:
            sb_client.rpc("persist_rookie_draft_board_authenticated", {"p_request": {
                "league_id": league_id,
                "draft_year": draft_year,
                # Same key format as the old full-board submit, so a pick that
                # was already saved is recognised instead of duplicated.
                "idempotency_key": f"rookie-board:{league_id}:{draft_year}:v1",
                "picks": [{
                    "draft_round": slot.round,
                    "round_pick": slot.pick,
                    "player_id": player_id,
                    "league_team_id": slot.owner_team_id,
                    "original_salary": slot.salary,
                    "original_contract_term": slot.years,
                    "one_time_option_salary": slot.option_salary,
                }],
            }}).execute()
        except Exception as exc:
            _flash("err", f"Pick not saved: {exc}", "draft_flash")
        else:
            _bump()
            _flash(
                "ok",
                f"{slot.label}: {slot.owner_name} drafts {rookie['name']} "
                f"(${slot.salary:g} × {slot.years} yr{'s' if slot.years > 1 else ''}).",
                "draft_flash",
            )


# ------------------------------------------------------------ FA auction

def render_fa_auction(
    *,
    sb_client: Any,
    league_id: str,
    league_teams: Sequence[Mapping[str, Any]],
    league_rules: Mapping[str, Any],
    active_season: int,
) -> None:
    epoch = _epoch()
    rules = auction_rules(league_rules)
    contracted = _contracted_ids(league_id, int(active_season), epoch)
    players = [p for p in _auction_universe(0) if p["id"] not in contracted]
    names = short_names(league_teams)
    rooms = _cap_rooms(sb_client, league_id, league_teams, league_rules, int(active_season), epoch)
    owners = [
        {"id": str(t["id"]), "name": names.get(str(t["id"]), "?"), "room": rooms.get(str(t["id"]))}
        for t in sorted(league_teams, key=lambda t: names.get(str(t.get("id")), ""))
        if t.get("id")
    ]
    data = {
        "token": st.session_state.get("fa_flash", {}).get("token", epoch),
        "flash": _take_flash("fa_flash"),
        "rules": {
            "max_years": rules["max_years"],
            "discount_pct": rules["discount_pct"],
            "minimums": {str(k): v for k, v in rules["minimums"].items()},
        },
        "players": players,
        "owners": owners,
        "recent": st.session_state.get("fa_recent", [])[:5],
    }
    hit = fa_auction(data, key="fa_auction")
    st.caption(
        f"Minimums and the {rules['discount_pct']:g}% per-year discount come from League Rules. "
        "Players already under contract don't show up in search."
    )
    if hit:
        _award(sb_client, league_id, league_teams, rules, int(active_season), players, owners, hit)
        st.rerun()


def _cap_rooms(sb_client, league_id, league_teams, league_rules, season, epoch) -> dict[str, float | None]:
    try:
        state = _team_state(sb_client, league_id, season, epoch)
    except Exception:
        return {}
    roster = state_roster(state)
    adjustments = state_cap_adjustments(state)
    cap = league_rules.get("salary_cap", 225)
    rooms = {}
    for team in league_teams:
        team_id = str(team.get("id") or "")
        if team_id:
            money = calculate_team_financials(roster, adjustments, salary_cap=cap, league_team_id=team_id)
            rooms[team_id] = round(float(money["cap_space"]), 2)
    return rooms


def _award(sb_client, league_id, league_teams, rules, season, players, owners, hit) -> None:
    player_id = str(hit.get("player_id") or "")
    team_id = str(hit.get("team_id") or "")
    try:
        salary = float(hit.get("salary"))
        years = int(hit.get("years"))
    except (TypeError, ValueError):
        _flash("err", "That bid could not be read. Try again.", "fa_flash")
        return
    player = next((p for p in players if p["id"] == player_id), None)
    owner = next((o for o in owners if o["id"] == team_id), None)
    _bump()
    contracted = _contracted_ids(league_id, season, _epoch())
    if player is None or owner is None:
        _flash("err", "Pick the player and winning owner again.", "fa_flash")
    elif not is_valid_bid(rules, salary, years):
        _flash("err", f"${salary:g} for {years} years is not a valid bid.", "fa_flash")
    elif player_id in contracted:
        _flash("err", f"{player['name']} is already under contract with {contracted[player_id]}.", "fa_flash")
    else:
        try:
            OffseasonTransactionService(sb_client, league_id).acquire(
                player_id=player_id, league_team_id=team_id, season=season,
                salary=salary, years=years, acquisition_type="fa_auction",
                idempotency_key=f"fa-auction:{league_id}:{season}:{player_id}",
            )
        except Exception as exc:
            _flash("err", f"Contract not saved: {exc}", "fa_flash")
            return
        _bump()
        recent = st.session_state.setdefault("fa_recent", [])
        recent.insert(0, {"player": player["name"], "pos": player["pos"], "owner": owner["name"],
                          "salary": salary, "years": years})
        _flash("ok", f"{player['name']} signed with {owner['name']}: ${salary:g} × {years} yr{'s' if years > 1 else ''}.",
               "fa_flash")
