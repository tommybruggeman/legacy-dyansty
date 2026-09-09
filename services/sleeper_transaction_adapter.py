"""Translate Sleeper transaction payloads into canonical contract intents.

Pure mapping only: no network, no database, no clock. Every function here takes
a Sleeper transaction dict exactly as the API returns it and produces intents
that ``services/sleeper_pipeline`` resolves against the canonical model.

Keeping this layer pure means the league's money rules are testable offline
against recorded fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# A pickup always costs at least this much, including a $0 waiver win and any
# free-agent add, which carries no bid field at all.
MINIMUM_ACQUISITION_SALARY = 1

# Every Sleeper-sourced acquisition is a one-year deal regardless of price.
ACQUISITION_YEARS = 1

_COMPLETE = "complete"


@dataclass(frozen=True)
class ReleaseIntent:
    transaction_id: str
    player_id: str
    roster_id: int
    idempotency_key: str


@dataclass(frozen=True)
class AcquireIntent:
    transaction_id: str
    player_id: str
    roster_id: int
    salary: int
    years: int
    acquisition_type: str
    idempotency_key: str


@dataclass(frozen=True)
class PlayerMove:
    player_id: str
    from_roster_id: int | None
    to_roster_id: int | None


@dataclass(frozen=True)
class PickMove:
    season: int
    round_number: int
    original_roster_id: int | None
    from_roster_id: int | None
    to_roster_id: int | None


@dataclass(frozen=True)
class FaabMove:
    from_roster_id: int
    to_roster_id: int
    amount: int


@dataclass(frozen=True)
class TradeIntent:
    transaction_id: str
    roster_ids: tuple[int, ...]
    player_moves: tuple[PlayerMove, ...]
    pick_moves: tuple[PickMove, ...]
    faab_moves: tuple[FaabMove, ...]
    idempotency_key: str


@dataclass(frozen=True)
class SkippedTransaction:
    transaction_id: str
    reason: str


Intent = ReleaseIntent | AcquireIntent | TradeIntent | SkippedTransaction


def _mapping(value: Any) -> dict[str, int]:
    """Sleeper sends ``null`` rather than ``{}`` for an empty adds/drops map."""
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for player_id, roster_id in value.items():
        key = str(player_id).strip()
        if not key:
            continue
        try:
            out[key] = int(roster_id)
        except (TypeError, ValueError):
            continue
    return out


def _transaction_id(transaction: Mapping[str, Any]) -> str:
    return str(transaction.get("transaction_id") or "").strip()


def waiver_bid(transaction: Mapping[str, Any]) -> int | None:
    """The FAAB bid, read from where Sleeper actually puts it.

    ``settings`` is null on free-agent adds and trades, and the bid legitimately
    comes back as 0 on an unopposed claim.
    """
    settings = transaction.get("settings")
    if not isinstance(settings, Mapping):
        return None
    raw = settings.get("waiver_bid")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def acquisition_salary(transaction: Mapping[str, Any]) -> int:
    """FAAB spent becomes salary, floored at the league minimum."""
    bid = waiver_bid(transaction)
    if bid is None or bid < MINIMUM_ACQUISITION_SALARY:
        return MINIMUM_ACQUISITION_SALARY
    return bid


def _acquisition_type(transaction: Mapping[str, Any]) -> str:
    kind = str(transaction.get("type") or "").strip().lower()
    if kind == "waiver":
        return "sleeper_waiver"
    if kind == "free_agent":
        return "sleeper_free_agent"
    return f"sleeper_{kind or 'unknown'}"


def _pick_moves(transaction: Mapping[str, Any]) -> tuple[PickMove, ...]:
    picks = transaction.get("draft_picks")
    if not isinstance(picks, Sequence):
        return ()

    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    moves: list[PickMove] = []
    for pick in picks:
        if not isinstance(pick, Mapping):
            continue
        season = _int_or_none(pick.get("season"))
        round_number = _int_or_none(pick.get("round"))
        if season is None or round_number is None:
            continue
        # Sleeper names these owner_id/previous_owner_id, but inside a
        # transaction they carry roster ids, not user ids.
        moves.append(PickMove(
            season, round_number,
            _int_or_none(pick.get("roster_id")),
            _int_or_none(pick.get("previous_owner_id")),
            _int_or_none(pick.get("owner_id")),
        ))
    return tuple(moves)


def _faab_moves(transaction: Mapping[str, Any]) -> tuple[FaabMove, ...]:
    budget = transaction.get("waiver_budget")
    if not isinstance(budget, Sequence):
        return ()
    moves: list[FaabMove] = []
    for entry in budget:
        if not isinstance(entry, Mapping):
            continue
        try:
            moves.append(FaabMove(
                int(entry["sender"]), int(entry["receiver"]), int(entry["amount"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(moves)


def _trade_intent(transaction: Mapping[str, Any], tx_id: str) -> TradeIntent:
    adds = _mapping(transaction.get("adds"))
    drops = _mapping(transaction.get("drops"))
    moves = tuple(
        PlayerMove(player_id, drops.get(player_id), adds.get(player_id))
        for player_id in sorted(set(adds) | set(drops))
    )
    roster_ids = transaction.get("roster_ids")
    rosters: tuple[int, ...] = ()
    if isinstance(roster_ids, Sequence):
        collected = []
        for roster_id in roster_ids:
            try:
                collected.append(int(roster_id))
            except (TypeError, ValueError):
                continue
        rosters = tuple(collected)
    return TradeIntent(
        tx_id, rosters, moves,
        _pick_moves(transaction), _faab_moves(transaction),
        f"sleeper:{tx_id}:trade",
    )


def map_transaction(transaction: Mapping[str, Any]) -> tuple[Intent, ...]:
    """Convert one Sleeper transaction into ordered canonical intents.

    Releases are emitted before acquisitions so a roster never momentarily
    exceeds its limit when a transaction both adds and drops.
    """
    tx_id = _transaction_id(transaction)
    if not tx_id:
        return (SkippedTransaction("", "transaction is missing an id"),)

    status = str(transaction.get("status") or "").strip().lower()
    if status != _COMPLETE:
        return (SkippedTransaction(tx_id, f"status is {status or 'unknown'}, not complete"),)

    kind = str(transaction.get("type") or "").strip().lower()
    if kind == "trade":
        return (_trade_intent(transaction, tx_id),)

    adds = _mapping(transaction.get("adds"))
    drops = _mapping(transaction.get("drops"))
    if not adds and not drops:
        return (SkippedTransaction(tx_id, "no player movement in transaction"),)

    salary = acquisition_salary(transaction)
    acquisition_type = _acquisition_type(transaction)

    intents: list[Intent] = [
        ReleaseIntent(tx_id, player_id, roster_id, f"sleeper:{tx_id}:release:{player_id}")
        for player_id, roster_id in sorted(drops.items())
    ]
    intents.extend(
        AcquireIntent(
            tx_id, player_id, roster_id, salary, ACQUISITION_YEARS,
            acquisition_type, f"sleeper:{tx_id}:acquire:{player_id}",
        )
        for player_id, roster_id in sorted(adds.items())
    )
    return tuple(intents)


def map_transactions(transactions: Iterable[Mapping[str, Any]]) -> tuple[Intent, ...]:
    """Map a week of transactions, oldest first so replays stay deterministic."""
    ordered = sorted(
        (tx for tx in transactions if isinstance(tx, Mapping)),
        key=lambda tx: (int(tx.get("created") or 0), _transaction_id(tx)),
    )
    intents: list[Intent] = []
    for transaction in ordered:
        intents.extend(map_transaction(transaction))
    return tuple(intents)
