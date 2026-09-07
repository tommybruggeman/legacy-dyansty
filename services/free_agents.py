from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from services.rookie_prospects import (
    is_current_rookie_eligible,
    resolve_rookie_stage,
)


SUPPORTED_POSITIONS = ("QB", "RB", "WR", "TE")

RELEASED_STATUSES = {
    "released",
    "release",
    "cut",
    "dropped",
    "waived",
    "inactive_released",
    "expired",
    "void",
}


@dataclass(frozen=True)
class FreeAgentRow:
    sleeper_player_id: str
    player: str
    position: str
    nfl_team: str
    last_season_ppg: float | None
    current_season_ppg: float | None
    ranking_ppg: float | None
    lifetime_points: float | None
    active_status_priority: int
    on_active_nfl_roster: bool


@dataclass(frozen=True)
class FutureFreeAgentRow(FreeAgentRow):
    contracted_team: str
    salary: float | None
    free_agent_season: int


@dataclass(frozen=True)
class RookieRow:
    sleeper_player_id: str
    player: str
    position: str
    nfl_team: str
    draft_round: int | None
    draft_pick: int | None
    draft_pick_in_round: int | None
    drafted: str
    college: str
    rookie_status: str


@dataclass(frozen=True)
class CommissionerAuctionPlayer:
    sleeper_player_id: str
    player: str
    position: str
    nfl_team: str


@dataclass(frozen=True)
class LifetimePointsCalculation:
    totals: Mapping[str, float]
    season_summaries_used: int
    weekly_records_used: int
    duplicate_records_ignored: int
    invalid_records_ignored: int


@dataclass(frozen=True)
class PpgRankingState:
    active_season: int
    last_season: int
    active_season_started: bool
    current_ppg: Mapping[str, float]
    last_ppg: Mapping[str, float]
    ranking_ppg: Mapping[str, float]


@dataclass(frozen=True)
class LeagueFreeAgentState:
    contracts: tuple[Mapping[str, Any], ...]
    roster_rows: tuple[Mapping[str, Any], ...]
    league_teams: tuple[Mapping[str, Any], ...]
    visible_free_agent_ids: frozenset[str] | None = None
    visible_expiring_contract_ids: frozenset[str] | None = None
    warnings: tuple[str, ...] = ()
    contract_seasons: tuple[Mapping[str, Any], ...] = ()
    sleeper_players: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class FreeAgentResults:
    current: tuple[FreeAgentRow, ...]
    future: tuple[FutureFreeAgentRow, ...]
    rookies: tuple[RookieRow, ...]
    positions: tuple[str, ...]
    nfl_teams: tuple[str, ...]
    warnings: tuple[str, ...]


def resolve_active_league_season(
    context: Any,
) -> int:
    season = _safe_season(
        getattr(
            context,
            "current_season",
            None,
        )
    )

    if season is None:
        raise ValueError(
            "Active league season is unavailable."
        )

    return season


def resolve_free_agent_season(
    active_season: int,
    contract_years_left: Any,
) -> int | None:
    """
    Return the legacy expiration season:
    active season + remaining seasons.
    """

    season = _safe_season(
        active_season
    )

    years_left = _safe_nonnegative_int(
        contract_years_left
    )

    if (
        season is None
        or years_left is None
        or years_left <= 0
    ):
        return None

    return season + years_left


def future_season_options(
    active_season: int,
) -> tuple[int, ...]:

    season = _safe_season(
        active_season
    )

    if season is None:
        return ()

    return tuple(
        season + offset
        for offset in range(1, 5)
    )


def load_player_universe(
    sb: Any,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(_load_all_rows(sb, "player_universe"))


def load_commissioner_auction_universe(
    sb: Any,
) -> tuple[Mapping[str, Any], ...]:
    """Load the complete identity population used only by commissioner auctions."""
    universe = _load_all_rows(sb, "player_universe")
    sleeper = _load_sleeper_players(sb, None)
    return tuple((*universe, *sleeper))


def build_commissioner_auction_players(
    identity_rows: Sequence[Mapping[str, Any]],
) -> tuple[CommissionerAuctionPlayer, ...]:
    """Return every known Sleeper QB/RB/WR/TE, independent of market eligibility."""
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in identity_rows:
        player_id = _player_id(row)
        if not player_id:
            continue
        existing = by_id.get(player_id, {})
        # Later rows (the Sleeper fallback) fill or refresh identity metadata.
        merged = dict(existing)
        for key, value in row.items():
            if value is not None and str(value).strip():
                merged[key] = value
        by_id[player_id] = merged

    players = []
    for player_id, row in by_id.items():
        position = _clean(row.get("position") or row.get("pos") or row.get("player_position"))
        name = _clean(row.get("full_name") or row.get("player_name") or row.get("name"))
        if not name or not position or position.upper() not in SUPPORTED_POSITIONS:
            continue
        players.append(CommissionerAuctionPlayer(
            sleeper_player_id=player_id,
            player=name,
            position=position.upper(),
            nfl_team=_display(_nfl_team(row)),
        ))
    return tuple(sorted(players, key=lambda row: (row.player.casefold(), row.sleeper_player_id)))


def canonical_live_owners(
    state: LeagueFreeAgentState,
    *,
    active_season: int,
) -> Mapping[str, str]:
    """Resolve current ownership from a live agreement plus a live obligation."""
    season = _safe_season(active_season)
    if season is None:
        raise ValueError("Active league season is unavailable.")
    teams = _team_names_by_id(state.league_teams)
    live_contract_ids = {
        _clean(row.get("contract_id"))
        for row in state.contract_seasons
        if _clean(row.get("contract_id"))
        and _safe_season(row.get("season")) is not None
        and int(row.get("season")) >= season
        and str(row.get("obligation_status") or "").strip().lower() in {"active", "scheduled"}
    }
    owners: dict[str, str] = {}
    for agreement in state.contracts:
        player_id = _player_id(agreement)
        if not player_id or _is_released(agreement):
            continue
        contract_id = _clean(agreement.get("id") or agreement.get("contract_id"))
        canonical = "status" in agreement or "superseded_by_contract_id" in agreement
        if canonical and (
            str(agreement.get("status") or "").lower() not in {"active", "scheduled"}
            or agreement.get("superseded_by_contract_id") is not None
            or contract_id not in live_contract_ids
        ):
            continue
        team_id = _clean(agreement.get("league_team_id"))
        owners[player_id] = teams.get(team_id, _clean(agreement.get("owner_name")) or "another team")
    return owners


def load_lifetime_points(
    sb: Any,
) -> Mapping[str, float]:
    """
    Aggregate canonical scoring-history records
    without double-counting seasons.
    """

    try:
        rows = (
            sb.table("player_scoring_history")
            .select(
                "sleeper_id,"
                "season,"
                "games_played,"
                "total_points,"
                "source"
            )
            .execute()
            .data
            or []
        )

    except Exception:
        return {}

    return calculate_lifetime_points(
        rows
    ).totals


def load_ranking_ppg(
    sb: Any,
    *,
    active_season: int,
) -> PpgRankingState:
    """
    Load last-season and current-season PPG for the
    free-agent market.

    Before the active season has any completed-game scoring,
    the market ranks by the previous season.

    Once active-season scoring exists, the market ranks by
    the active season.

    Both seasons remain available for display.
    """

    season = _safe_season(
        active_season
    )

    if season is None:
        raise ValueError(
            "Active league season is unavailable."
        )

    last_season = season - 1

    try:
        rows = (
            sb.table(
                "player_season_stats"
            )
            .select(
                "sleeper_id,"
                "season,"
                "games,"
                "fantasy_ppg_ppr"
            )
            .in_(
                "season",
                [
                    last_season,
                    season,
                ],
            )
            .execute()
            .data
            or []
        )

    except Exception:
        return PpgRankingState(
            active_season=season,
            last_season=last_season,
            active_season_started=False,
            current_ppg={},
            last_ppg={},
            ranking_ppg={},
        )

    current_ppg: dict[
        str,
        float,
    ] = {}

    last_ppg: dict[
        str,
        float,
    ] = {}

    for row in rows:
        row_season = _safe_season(
            row.get(
                "season"
            )
        )

        player_id = _player_id(
            row
        )

        games = _safe_nonnegative_int(
            row.get(
                "games"
            )
        )

        ppg = _safe_float(
            row.get(
                "fantasy_ppg_ppr"
            )
        )

        if (
            not player_id
            or games is None
            or games <= 0
            or ppg is None
        ):
            continue

        if row_season == season:
            current_ppg[
                player_id
            ] = ppg

        elif row_season == last_season:
            last_ppg[
                player_id
            ] = ppg

    active_season_started = bool(
        current_ppg
    )

    ranking_ppg = (
        current_ppg
        if active_season_started
        else last_ppg
    )

    return PpgRankingState(
        active_season=season,
        last_season=last_season,
        active_season_started=active_season_started,
        current_ppg=current_ppg,
        last_ppg=last_ppg,
        ranking_ppg=ranking_ppg,
    )


def calculate_lifetime_points(
    rows: Sequence[Mapping[str, Any]],
) -> LifetimePointsCalculation:
    """
    Use one season summary per player/season,
    or unique weekly rows when no summary exists.
    """

    summaries: dict[
        tuple[str, int],
        tuple[
            tuple[int, int, float],
            float,
        ],
    ] = {}

    weekly: dict[
        tuple[str, int, int],
        float,
    ] = {}

    duplicates = 0
    invalid = 0


    for row in rows:

        player_id = _player_id(
            row
        )

        season = _safe_season(
            row.get("season")
        )


        if (
            not player_id
            or season is None
        ):
            invalid += 1
            continue


        week = _safe_positive_int(
            row.get("week")
        )


        if week is not None:

            points = _safe_float(
                row.get(
                    "fantasy_points_custom"
                )
                if row.get(
                    "fantasy_points_custom"
                )
                is not None
                else row.get(
                    "total_points"
                )
            )


            if points is None:
                invalid += 1
                continue


            key = (
                player_id,
                season,
                week,
            )


            if key in weekly:
                duplicates += 1
                continue


            weekly[key] = points
            continue


        points = _safe_float(
            row.get("total_points")
        )

        games = _safe_nonnegative_int(
            row.get("games_played")
        )


        if (
            points is None
            or games is None
            or games <= 0
        ):
            invalid += 1
            continue


        source_priority = (
            1
            if _clean(
                row.get("source")
            )
            == "nflverse_player_stats"
            else 0
        )


        quality = (
            source_priority,
            games,
            points,
        )

        key = (
            player_id,
            season,
        )


        if key in summaries:

            duplicates += 1

            if (
                quality
                <= summaries[key][0]
            ):
                continue


        summaries[key] = (
            quality,
            points,
        )


    totals: dict[
        str,
        float,
    ] = {}


    weekly_used = 0


    for (
        player_id,
        _season,
    ), (
        _quality,
        points,
    ) in summaries.items():

        totals[player_id] = (
            totals.get(
                player_id,
                0.0,
            )
            + points
        )


    summary_seasons = set(
        summaries
    )


    for (
        player_id,
        season,
        _week,
    ), points in weekly.items():

        if (
            player_id,
            season,
        ) in summary_seasons:

            duplicates += 1
            continue


        totals[player_id] = (
            totals.get(
                player_id,
                0.0,
            )
            + points
        )

        weekly_used += 1


    clean_totals = {
        player_id: round(
            points,
            3,
        )
        for player_id, points
        in totals.items()
    }


    return LifetimePointsCalculation(
        clean_totals,
        len(summaries),
        weekly_used,
        duplicates,
        invalid,
    )


def load_league_free_agent_state(
    sb: Any,
    league_id: str,
    relevant_player_ids: Iterable[Any] | None = None,
) -> LeagueFreeAgentState:

    clean_league_id = _clean(
        league_id
    )


    if not clean_league_id:
        raise ValueError(
            "Selected league is required."
        )


    contracts = _required_rows(
        sb,
        "contract_agreements",
        clean_league_id,
    )


    if not contracts:

        contracts = _required_rows(
            sb,
            "contracts",
            clean_league_id,
        )

        warnings = [
            (
                "Canonical contract agreements are unavailable; "
                "legacy contracts are being used as the ownership fallback."
            )
        ]

    else:
        warnings = []


    try:
        contract_seasons = _required_rows(
            sb,
            "contract_seasons",
            clean_league_id,
        )

    except Exception:
        contract_seasons = []

        warnings.append(
            (
                "Canonical contract-season data is unavailable; "
                "legacy remaining-years data is being used as the expiration fallback."
            )
        )


    teams = _required_rows(
        sb,
        "league_teams",
        clean_league_id,
    )


    try:
        roster_rows = _required_rows(
            sb,
            "team_roster_state",
            clean_league_id,
        )

    except Exception:
        roster_rows = []

        warnings.append(
            (
                "Canonical roster-state data is unavailable; "
                "league-scoped contracts are being used as the ownership fallback."
            )
        )


    try:
        sleeper_player_ids = {
            player_id
            for row in (
                *contracts,
                *roster_rows,
            )
            if (
                player_id := _player_id(
                    row
                )
            )
        }

        sleeper_player_ids = sleeper_player_ids | {
            player_id
            for value in (
                relevant_player_ids
                or ()
            )
            if (
                player_id := _clean(
                    value
                )
            )
        }

        sleeper_players = _load_sleeper_players(
            sb,
            sleeper_player_ids
            if sleeper_player_ids
            else None,
        )

    except Exception:
        sleeper_players = []

        warnings.append(
            (
                "Current Sleeper player metadata is unavailable; "
                "player-universe metadata is being used as the fallback."
            )
        )


    return LeagueFreeAgentState(
        contracts=tuple(
            contracts
        ),

        roster_rows=tuple(
            roster_rows
        ),

        league_teams=tuple(
            teams
        ),

        # The live league market is derived from
        # current league authority.
        #
        # Open Market:
        #   eligible player universe
        #   - current league ownership
        #
        # Expiring Contracts:
        #   current league contracts
        #   + remaining contract years
        #
        # Rollover publication artifacts are not
        # required to render the ordinary live market
        # for an active league.

        visible_free_agent_ids=None,

        visible_expiring_contract_ids=None,

        warnings=tuple(
            warnings
        ),

        contract_seasons=tuple(
            dict(row)
            for row in contract_seasons
        ),

        sleeper_players=tuple(
            dict(row)
            for row in sleeper_players
        ),
    )


def build_free_agent_results(
    player_universe: Sequence[Mapping[str, Any]],
    state: LeagueFreeAgentState,
    *,
    active_season: int,
    lifetime_points_by_player: Mapping[str, float] | None = None,
    last_season_ppg_by_player: Mapping[str, float] | None = None,
    current_season_ppg_by_player: Mapping[str, float] | None = None,
    ranking_ppg_by_player: Mapping[str, float] | None = None,
) -> FreeAgentResults:

    season = _safe_season(
        active_season
    )


    if season is None:
        raise ValueError(
            "Active league season is unavailable."
        )


    warnings = list(
        state.warnings
    )


    raw_universe_by_id: dict[
        str,
        Mapping[str, Any],
    ] = {}


    universe_by_id: dict[
        str,
        Mapping[str, Any],
    ] = {}


    sleeper_by_id = {
        player_id: row
        for row in state.sleeper_players
        if (
            player_id := _player_id(
                row
            )
        )
    }


    for row in player_universe:

        player_id = _player_id(
            row
        )


        if not player_id:
            continue


        raw_universe_by_id.setdefault(
            player_id,
            row,
        )


        current_row = _overlay_sleeper_metadata(
            row,
            sleeper_by_id.get(
                player_id
            ),
        )


        if not _eligible_player(
            current_row
        ):
            continue


        universe_by_id.setdefault(
            player_id,
            current_row,
        )


    owned_ids = _owned_player_ids(
        (
            *state.roster_rows,
            *state.contracts,
        ),
        warnings,
    )


    lifetime_points_by_player = (
        lifetime_points_by_player
        or {}
    )


    last_season_ppg_by_player = (
        last_season_ppg_by_player
        or {}
    )


    current_season_ppg_by_player = (
        current_season_ppg_by_player
        or {}
    )


    ranking_ppg_by_player = (
        ranking_ppg_by_player
        or {}
    )


    current = tuple(
        sorted(
            (
                _current_row(
                    player_id,
                    row,
                    season,
                    lifetime_points_by_player.get(
                        player_id
                    ),
                    last_season_ppg_by_player.get(
                        player_id
                    ),
                    current_season_ppg_by_player.get(
                        player_id
                    ),
                    ranking_ppg_by_player.get(
                        player_id
                    ),
                )
                for player_id, row
                in universe_by_id.items()
                if (
                    player_id
                    not in owned_ids
                )
                and (
                    state.visible_free_agent_ids
                    is None
                    or player_id
                    in state.visible_free_agent_ids
                )
            ),
            key=_current_rank_key,
        )
    )


    team_names = _team_names_by_id(
        state.league_teams
    )


    future_rows: list[
        FutureFreeAgentRow
    ] = []


    expiration_by_contract_id = (
        _canonical_expirations_by_contract_id(
            state.contract_seasons
        )
    )


    salary_by_contract_id = (
        _canonical_salary_by_contract_id(
            state.contract_seasons,
            season,
        )
    )


    for contract in state.contracts:

        if _is_released(
            contract
        ):
            continue


        player_id = _player_id(
            contract
        )


        contract_id = _clean(
            contract.get(
                "id"
            )
            or contract.get(
                "contract_id"
            )
        )


        expiration = expiration_by_contract_id.get(
            contract_id
        )


        if expiration is None:
            expiration = resolve_free_agent_season(
                season,
                contract.get(
                    "contract_years_left"
                )
                or contract.get(
                    "years_remaining"
                ),
            )


        if (
            not player_id
            or expiration is None
            or (
                state.visible_expiring_contract_ids
                is not None
                and player_id
                not in state.visible_expiring_contract_ids
            )
        ):

            if _clean(
                contract.get(
                    "player_name"
                )
            ):
                warnings.append(
                    (
                        "A contract with incomplete player identity "
                        "or remaining-years data was omitted from future free agents."
                    )
                )

            continue


        player = universe_by_id.get(
            player_id,
            {},
        )


        future_rows.append(
            FutureFreeAgentRow(
                sleeper_player_id=player_id,

                player=_display(
                    player.get(
                        "player_name"
                    )
                    or player.get(
                        "full_name"
                    )
                    or player.get(
                        "name"
                    )
                    or contract.get(
                        "player_name"
                    )
                ),

                position=_display(
                    player.get(
                        "pos"
                    )
                    or player.get(
                        "position"
                    )
                    or player.get(
                        "player_position"
                    )
                    or contract.get(
                        "player_position"
                    )
                ),

                nfl_team=_display_nfl_team(
                    player,
                    contract.get(
                        "nfl_team"
                    ),
                ),

                last_season_ppg=_safe_float(
                    last_season_ppg_by_player.get(
                        player_id
                    )
                ),

                current_season_ppg=_safe_float(
                    current_season_ppg_by_player.get(
                        player_id
                    )
                ),

                ranking_ppg=_safe_float(
                    ranking_ppg_by_player.get(
                        player_id
                    )
                ),

                lifetime_points=_safe_float(
                    lifetime_points_by_player.get(
                        player_id
                    )
                ),

                active_status_priority=(
                    _active_status_priority(
                        player
                    )
                ),

                on_active_nfl_roster=(
                    _on_active_nfl_roster(
                        player
                    )
                ),

                contracted_team=(
                    _contracted_team(
                        contract,
                        team_names,
                        warnings,
                    )
                ),

                salary=(
                    salary_by_contract_id[
                        contract_id
                    ]
                    if contract_id
                    in salary_by_contract_id
                    else _safe_float(
                        contract.get(
                            "salary"
                        )
                    )
                ),

                free_agent_season=(
                    expiration
                ),
            )
        )


    future = tuple(
        sorted(
            future_rows,
            key=_future_rank_key,
        )
    )


    rookies = sort_rookie_class(
        _rookie_rows(
            raw_universe_by_id,
            season,
        )
    )


    positions = tuple(
        sorted(
            {
                row.position
                for row in (
                    *current,
                    *future,
                    *rookies,
                )
                if row.position != "—"
            }
        )
    )


    nfl_teams = tuple(
        sorted(
            {
                row.nfl_team
                for row in (
                    *current,
                    *future,
                    *rookies,
                )
                if row.nfl_team != "—"
            }
        )
    )


    return FreeAgentResults(
        current,
        future,
        rookies,
        positions,
        nfl_teams,
        tuple(
            dict.fromkeys(
                warnings
            )
        ),
    )


def current_free_agents_for_filters(
    rows: Sequence[FreeAgentRow],
    *,
    search: str = "",
    position: str = "All",
    nfl_team: str = "All",
    nfl_roster_status: str = "All",
) -> tuple[FreeAgentRow, ...]:

    needle = (
        search
        .strip()
        .casefold()
    )


    return tuple(
        row
        for row in rows
        if (
            not needle
            or needle
            in row.player.casefold()
        )
        and (
            position == "All"
            or row.position
            == position
        )
        and (
            nfl_team == "All"
            or row.nfl_team
            == nfl_team
        )
        and (
            nfl_roster_status
            == "All"
            or (
                nfl_roster_status
                == "Active roster"
            )
            == row.on_active_nfl_roster
        )
    )


def future_free_agents_for_season(
    rows: Sequence[FutureFreeAgentRow],
    selected_season: int,
    *,
    position: str = "All",
) -> tuple[FutureFreeAgentRow, ...]:

    season = _safe_season(
        selected_season
    )


    if season is None:
        return ()


    return tuple(
        row
        for row in rows
        if (
            row.free_agent_season
            == season
        )
        and (
            position == "All"
            or row.position
            == position
        )
    )


def rookie_class_for_position(
    rows: Sequence[RookieRow],
    *,
    position: str = "All",
) -> tuple[RookieRow, ...]:

    return tuple(
        row
        for row in rows
        if (
            position == "All"
            or row.position
            == position
        )
    )


def resolve_rookie_ranking_strategy(
    *,
    meaningful_current_ppg: bool = False,
    ai_rankings_available: bool = False,
) -> tuple[str, ...]:

    if meaningful_current_ppg:
        return (
            "current_season_ppg",
            "ai_rookie_projection",
            "nfl_draft_position",
        )


    if ai_rankings_available:
        return (
            "ai_rookie_projection",
            "nfl_draft_position",
        )


    return (
        "nfl_draft_position",
    )


def sort_rookie_class(
    rows: Sequence[RookieRow],
) -> tuple[RookieRow, ...]:

    return tuple(
        sorted(
            rows,
            key=_rookie_rank_key,
        )
    )


def _required_rows(
    sb: Any,
    table_name: str,
    league_id: str,
) -> list[Mapping[str, Any]]:

    rows = (
        sb.table(
            table_name
        )
        .select("*")
        .eq(
            "league_id",
            league_id,
        )
        .execute()
        .data
        or []
    )


    return [
        dict(row)
        for row in rows
    ]


def _load_all_rows(
    sb: Any,
    table_name: str,
    *,
    page_size: int = 1000,
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    start = 0
    while True:
        batch = (
            sb.table(table_name)
            .select("*")
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(dict(row) for row in batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def _load_sleeper_players(
    sb: Any,
    player_ids: Iterable[str] | None,
    *,
    chunk_size: int = 500,
) -> list[Mapping[str, Any]]:

    if player_ids is not None:
        ids = sorted(
            {
                player_id
                for value in player_ids
                if (
                    player_id := _clean(
                        value
                    )
                )
            }
        )

        rows: list[Mapping[str, Any]] = []

        for start in range(
            0,
            len(ids),
            chunk_size,
        ):
            batch = (
                sb.table("sleeper_players")
                .select("*")
                .in_(
                    "sleeper_player_id",
                    ids[start:start + chunk_size],
                )
                .execute()
                .data
                or []
            )

            rows.extend(
                dict(row)
                for row in batch
            )

        return rows


    rows = []
    page_size = 1000
    start = 0

    while True:
        batch = (
            sb.table("sleeper_players")
            .select("*")
            .range(
                start,
                start + page_size - 1,
            )
            .execute()
            .data
            or []
        )

        rows.extend(
            dict(row)
            for row in batch
        )

        if len(batch) < page_size:
            return rows

        start += page_size


def _owned_player_ids(
    rows: Iterable[Mapping[str, Any]],
    warnings: list[str],
) -> set[str]:

    owned: set[str] = set()

    incomplete = False


    for row in rows:

        if _is_released(
            row
        ):
            continue


        player_id = _player_id(
            row
        )


        if player_id:
            owned.add(
                player_id
            )

        elif _clean(
            row.get(
                "player_name"
            )
        ):
            incomplete = True


    if incomplete:
        warnings.append(
            (
                "Some ownership rows lack a canonical Sleeper player ID "
                "and could not safely exclude players by display name."
            )
        )


    return owned


def _current_row(
    player_id: str,
    row: Mapping[str, Any],
    active_season: int,
    lifetime_points: Any = None,
    last_season_ppg: Any = None,
    current_season_ppg: Any = None,
    ranking_ppg: Any = None,
) -> FreeAgentRow:

    return FreeAgentRow(
        sleeper_player_id=player_id,

        player=_display(
            row.get(
                "player_name"
            )
            or row.get(
                "full_name"
            )
            or row.get(
                "name"
            )
        ),

        position=_display(
            row.get(
                "pos"
            )
            or row.get(
                "position"
            )
            or row.get(
                "player_position"
            )
        ),

        nfl_team=_display_nfl_team(
            row
        ),

        last_season_ppg=_safe_float(
            last_season_ppg
        ),

        current_season_ppg=_safe_float(
            current_season_ppg
        ),

        ranking_ppg=_safe_float(
            ranking_ppg
        ),

        lifetime_points=_safe_float(
            lifetime_points
        ),

        active_status_priority=(
            _active_status_priority(
                row
            )
        ),

        on_active_nfl_roster=(
            _on_active_nfl_roster(
                row
            )
        ),
    )


def _current_rank_key(
    row: FreeAgentRow,
) -> tuple[Any, ...]:
    """
    Open Market ranking authority:

    1. Active-season PPG when current-season scoring exists
    2. Otherwise previous-season PPG
    3. Active NFL roster
    4. Player name
    5. Sleeper player ID
    """

    return (
        0
        if row.ranking_ppg
        is not None
        else 1,

        -(
            row.ranking_ppg
            or 0.0
        ),

        row.active_status_priority,

        row.player.casefold(),

        row.sleeper_player_id,
    )


def _rookie_rows(
    universe_by_id: Mapping[
        str,
        Mapping[str, Any],
    ],
    active_season: int,
) -> tuple[RookieRow, ...]:

    rows: list[
        RookieRow
    ] = []


    for (
        player_id,
        player,
    ) in universe_by_id.items():

        class_year = _safe_season(
            player.get(
                "rookie_class_year"
            )
            or player.get(
                "draft_year"
            )
        )


        if (
            class_year
            != active_season
            or not is_current_rookie_eligible(
                player,
                active_season,
            )
        ):
            continue


        draft_round = _safe_positive_int(
            player.get(
                "draft_round"
            )
        )


        draft_pick = _safe_positive_int(
            player.get(
                "overall_pick"
            )
            or player.get(
                "draft_number"
            )
            or player.get(
                "draft_pick"
            )
        )


        in_round = _safe_positive_int(
            player.get(
                "draft_pick_in_round"
            )
            or player.get(
                "pick_in_round"
            )
            or player.get(
                "draft_slot"
            )
        )


        undrafted = _is_undrafted(
            player
        )


        stage = resolve_rookie_stage(
            player,
            active_season,
        )


        if (
            stage == "UDFA"
            or undrafted
        ):

            drafted = "UDFA"


        elif (
            stage == "DRAFTED"
            and draft_round
            is not None
        ):

            drafted = (
                f"Round {draft_round}, "
                f"Pick {draft_pick}"
            )


        elif stage == "PROSPECT":

            drafted = (
                "Undrafted Prospect"
            )


        else:

            drafted = "—"


        rows.append(
            RookieRow(
                sleeper_player_id=player_id,

                player=_display(
                    player.get(
                        "player_name"
                    )
                    or player.get(
                        "full_name"
                    )
                    or player.get(
                        "name"
                    )
                ),

                position=_display(
                    player.get(
                        "pos"
                    )
                    or player.get(
                        "position"
                    )
                    or player.get(
                        "player_position"
                    )
                ),

                nfl_team=_display(
                    _nfl_team(
                        player
                    )
                ),

                draft_round=(
                    draft_round
                ),

                draft_pick=(
                    draft_pick
                ),

                draft_pick_in_round=(
                    in_round
                ),

                drafted=(
                    drafted
                ),

                college=_display(
                    player.get(
                        "college"
                    )
                ),

                rookie_status={
                    "DRAFTED": "Drafted",
                    "UDFA": "UDFA",
                    "PROSPECT": "Prospect",
                }.get(
                    stage,
                    "Unknown",
                ),
            )
        )


    return tuple(
        rows
    )


def _rookie_rank_key(
    row: RookieRow,
) -> tuple[Any, ...]:

    group = {
        "Drafted": 0,
        "UDFA": 1,
        "Prospect": 2,
    }.get(
        row.rookie_status,
        3,
    )


    return (
        group,

        row.draft_pick
        if row.draft_pick
        is not None
        else 10_000,

        row.player.casefold(),

        row.sleeper_player_id,
    )


def _future_rank_key(
    row: FutureFreeAgentRow,
) -> tuple[Any, ...]:

    return (
        row.free_agent_season,

        0
        if row.salary
        is not None
        else 1,

        -(
            row.salary
            or 0.0
        ),

        0
        if row.ranking_ppg
        is not None
        else 1,

        -(
            row.ranking_ppg
            or 0.0
        ),

        row.player.casefold(),

        row.sleeper_player_id,
    )


def _current_ppg(
    row: Mapping[str, Any],
    active_season: int,
) -> float | None:
    """
    Legacy player-universe PPG fallback.

    Ranking PPG should normally come from
    load_ranking_ppg(). This remains as a safe fallback
    for callers that do not yet supply scoring-history PPG.
    """

    source_season = _safe_season(
        row.get(
            "latest_season"
        )
        or row.get(
            "season"
        )
    )


    if (
        source_season
        != active_season
    ):
        return None


    return _safe_float(
        row.get(
            "season_ppg"
        )
    )


def _team_names_by_id(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:

    return {
        team_id: _display(
            row.get(
                "team_name"
            )
            or row.get(
                "owner_name"
            )
        )
        for row in rows
        if (
            team_id := _clean(
                row.get("id")
            )
        )
    }


def _contracted_team(
    contract: Mapping[str, Any],
    team_names: Mapping[str, str],
    warnings: list[str],
) -> str:

    team_id = _clean(
        contract.get(
            "league_team_id"
        )
    )


    if (
        team_id
        and team_id
        in team_names
    ):
        return team_names[
            team_id
        ]


    legacy_name = _clean(
        contract.get(
            "owner_name"
        )
        or contract.get(
            "team_name"
        )
        or contract.get(
            "owner"
        )
    )


    if legacy_name:

        warnings.append(
            "internal:legacy_contract_team_label_fallback"
        )

        return legacy_name


    return "—"


def _eligible_player(
    row: Mapping[str, Any],
) -> bool:

    position = _clean(
        row.get(
            "pos"
        )
        or row.get(
            "position"
        )
        or row.get(
            "player_position"
        )
    )


    if (
        position
        and position.upper()
        not in SUPPORTED_POSITIONS
    ):
        return False


    if row.get(
        "active"
    ) is False:
        return False


    nfl_status = str(
        row.get(
            "nfl_status"
        )
        or ""
    ).strip().lower()


    return nfl_status not in {
        "retired",
        "inactive",
        "deceased",
    }


def _overlay_sleeper_metadata(
    universe_row: Mapping[str, Any],
    sleeper_row: Mapping[str, Any] | None,
) -> Mapping[str, Any]:

    if not sleeper_row:
        return universe_row


    merged = dict(
        universe_row
    )


    position = _clean(
        sleeper_row.get(
            "position"
        )
    )


    if position:
        merged["pos"] = position


    merged["nfl_team"] = _clean(
        sleeper_row.get(
            "team"
        )
    )

    merged["team"] = None
    merged["nfl_team_abbr"] = None
    merged["pro_team"] = None
    merged["_sleeper_team_authoritative"] = True


    status = _clean(
        sleeper_row.get(
            "status"
        )
    )


    if status:
        merged["nfl_status"] = status


    if isinstance(
        sleeper_row.get(
            "is_active"
        ),
        bool,
    ):
        merged["active"] = sleeper_row[
            "is_active"
        ]


    return merged


def _canonical_expirations_by_contract_id(
    contract_seasons: Sequence[Mapping[str, Any]],
) -> Mapping[str, int]:

    maximum_season_by_contract_id: dict[
        str,
        int,
    ] = {}


    for row in contract_seasons:

        status = str(
            row.get(
                "obligation_status"
            )
            or ""
        ).strip().lower()


        if status not in {
            "active",
            "scheduled",
        }:
            continue


        contract_id = _clean(
            row.get(
                "contract_id"
            )
        )

        obligation_season = _safe_season(
            row.get(
                "season"
            )
        )


        if (
            not contract_id
            or obligation_season is None
        ):
            continue


        maximum_season_by_contract_id[
            contract_id
        ] = max(
            obligation_season,
            maximum_season_by_contract_id.get(
                contract_id,
                obligation_season,
            ),
        )


    return {
        contract_id: maximum_season + 1
        for contract_id, maximum_season
        in maximum_season_by_contract_id.items()
    }


def _canonical_salary_by_contract_id(
    contract_seasons: Sequence[Mapping[str, Any]],
    active_season: int,
) -> Mapping[str, float | None]:

    candidates_by_contract_id: dict[
        str,
        list[tuple[int, int, Mapping[str, Any]]],
    ] = {}


    for row in contract_seasons:

        status = str(
            row.get(
                "obligation_status"
            )
            or ""
        ).strip().lower()

        contract_id = _clean(
            row.get(
                "contract_id"
            )
        )

        obligation_season = _safe_season(
            row.get(
                "season"
            )
        )


        if (
            status not in {
                "active",
                "scheduled",
            }
            or not contract_id
            or obligation_season is None
        ):
            continue


        if obligation_season == active_season:
            priority = 0
        elif (
            obligation_season > active_season
            and status == "scheduled"
        ):
            priority = 1
        else:
            priority = 2


        candidates_by_contract_id.setdefault(
            contract_id,
            [],
        ).append(
            (
                priority,
                obligation_season
                if priority < 2
                else -obligation_season,
                row,
            )
        )


    salaries: dict[
        str,
        float | None,
    ] = {}


    for contract_id, candidates in candidates_by_contract_id.items():
        selected = min(
            candidates,
            key=lambda candidate: (
                candidate[0],
                candidate[1],
            ),
        )[2]

        salaries[contract_id] = _safe_float(
            selected.get(
                "cap_hit"
            )
            if selected.get(
                "cap_hit"
            ) is not None
            else selected.get(
                "salary"
            )
        )


    return salaries


def _active_status_priority(
    row: Mapping[str, Any],
) -> int:

    return (
        0
        if _on_active_nfl_roster(
            row
        )
        else 1
    )


def _on_active_nfl_roster(
    row: Mapping[str, Any],
) -> bool:

    status = str(
        row.get(
            "nfl_status"
        )
        or row.get(
            "status"
        )
        or ""
    ).strip().lower()


    return (
        row.get(
            "active"
        )
        is True
        and _nfl_team(
            row
        )
        is not None
        and status
        not in {
            "retired",
            "deceased",
            "inactive",
        }
    )


def _is_undrafted(
    row: Mapping[str, Any],
) -> bool:

    status = str(
        row.get(
            "draft_status"
        )
        or ""
    ).strip().lower()


    return (
        status
        in {
            "udfa",
            "undrafted",
        }
        or row.get(
            "draft_round"
        )
        == 0
        or row.get(
            "draft_pick"
        )
        == 0
    )


def _nfl_team(
    row: Mapping[str, Any],
) -> str | None:

    return _clean(
        row.get(
            "nfl_team"
        )
        or row.get(
            "team"
        )
        or row.get(
            "nfl_team_abbr"
        )
        or row.get(
            "pro_team"
        )
    )


def _display_nfl_team(
    row: Mapping[str, Any],
    fallback: Any = None,
) -> str:

    team = _nfl_team(
        row
    )


    if team:
        return (
            "FA"
            if team.casefold()
            == "no team"
            else team
        )


    if row.get(
        "_sleeper_team_authoritative"
    ):
        return "FA"


    fallback_team = _clean(
        fallback
    )


    if (
        fallback_team
        and fallback_team.casefold()
        != "no team"
    ):
        return fallback_team


    return (
        "FA"
        if fallback_team
        else "—"
    )


def _is_released(
    row: Mapping[str, Any],
) -> bool:

    status = str(
        row.get(
            "status"
        )
        or row.get(
            "roster_status"
        )
        or row.get(
            "contract_status"
        )
        or ""
    ).strip().lower()


    return status in RELEASED_STATUSES


def _player_id(
    row: Mapping[str, Any],
) -> str | None:

    return _clean(
        row.get(
            "sleeper_player_id"
        )
        or row.get(
            "sleeper_id"
        )
        or row.get(
            "player_id"
        )
    )


def _display(
    value: Any,
) -> str:

    return (
        _clean(
            value
        )
        or "—"
    )


def _clean(
    value: Any,
) -> str | None:

    if value is None:
        return None


    text = str(
        value
    ).strip()


    return (
        text
        or None
    )


def _safe_float(
    value: Any,
) -> float | None:

    if value in (
        None,
        "",
    ):
        return None


    try:
        return round(
            float(
                value
            ),
            3,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def _safe_nonnegative_int(
    value: Any,
) -> int | None:

    try:
        number = float(
            str(
                value
            ).strip()
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


    if (
        number < 0
        or not number.is_integer()
    ):
        return None


    return int(
        number
    )


def _safe_positive_int(
    value: Any,
) -> int | None:

    number = _safe_nonnegative_int(
        value
    )


    return (
        number
        if (
            number is not None
            and number > 0
        )
        else None
    )


def _safe_season(
    value: Any,
) -> int | None:

    try:
        season = int(
            float(
                str(
                    value
                ).strip()
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


    return (
        season
        if 2000
        <= season
        <= 2100
        else None
    )
