"""Apply Sleeper transactions to the canonical contract model.

The pure decision functions at the top of this module take plain rows and
return plain values, so the league's money rules can be tested without a
database. ``SleeperSyncRunner`` below is the only part that talks to Supabase
or the Sleeper API.

Ordering guarantees, which the money math depends on:
  * transactions replay oldest first
  * within a transaction, releases execute before acquisitions
  * the watermark only advances past a transaction that fully succeeded
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Iterable, Mapping, Sequence

from services.sleeper_transaction_adapter import (
    AcquireIntent,
    ReleaseIntent,
    SkippedTransaction,
    TradeIntent,
    map_transaction,
)

SLEEPER_BASE = "https://api.sleeper.app/v1"

# A contract at or below this salary carries no dead cap when dropped.
NO_DEAD_CAP_SALARY_CEILING = Decimal("1")

LIVE_AGREEMENT_STATUSES = ("active", "scheduled")
LIVE_OBLIGATION_STATUSES = ("active", "scheduled")


@dataclass(frozen=True)
class DeadCapCharge:
    season: int
    amount: Decimal


@dataclass(frozen=True)
class SyncException:
    kind: str
    transaction_id: str
    detail: str
    player_id: str | None = None
    roster_id: int | None = None


@dataclass
class SyncReport:
    league_id: str
    enabled: bool
    processed: int = 0
    released: int = 0
    acquired: int = 0
    traded: int = 0
    skipped: int = 0
    exceptions: list[SyncException] = field(default_factory=list)
    watermark_created_ms: int = 0
    watermark_transaction_id: str | None = None

    @property
    def status(self) -> str:
        if not self.enabled:
            return "skipped_disabled"
        return "ok"

    def summary(self) -> str:
        if not self.enabled:
            return "Sync is paused; no transactions were applied."
        return (
            f"{self.processed} transaction(s): {self.acquired} signed, "
            f"{self.released} released, {self.traded} traded, "
            f"{self.skipped} skipped, {len(self.exceptions)} flagged."
        )


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dead_cap_schedule(
    contract_seasons: Sequence[Mapping[str, Any]],
    *,
    from_season: int,
    dead_cap_pct: Decimal | float | str,
) -> tuple[DeadCapCharge, ...]:
    """Dead cap owed per remaining season when a contract is dropped.

    The penalty applies to every season still owed, not just the current one,
    so a multi-year deal keeps charging after rollover. A season worth $1 or
    less carries no penalty at all -- dropping a minimum contract is free.
    """
    pct = Decimal(str(dead_cap_pct))
    if pct < 0 or pct > 100:
        raise ValueError("dead cap percentage is out of range")

    charges: list[DeadCapCharge] = []
    for row in contract_seasons:
        try:
            season = int(row.get("season") or 0)
        except (TypeError, ValueError):
            continue
        if season < from_season:
            continue
        if str(row.get("obligation_status") or "") not in LIVE_OBLIGATION_STATUSES:
            continue
        basis = row.get("cap_hit")
        if basis is None:
            basis = row.get("salary")
        basis = _money(basis)
        if basis <= NO_DEAD_CAP_SALARY_CEILING:
            charges.append(DeadCapCharge(season, Decimal("0.00")))
            continue
        charges.append(DeadCapCharge(
            season, (basis * pct / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ),
        ))
    return tuple(sorted(charges, key=lambda charge: charge.season))


def total_dead_cap(charges: Iterable[DeadCapCharge]) -> Decimal:
    return sum((charge.amount for charge in charges), Decimal("0.00"))


def build_roster_map(league_team_rows: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """Sleeper roster id -> canonical league_team id.

    Keyed on ``sleeper_roster_id``, which Sleeper never reassigns within a
    league, so an owner renaming himself cannot move his contracts.
    """
    mapping: dict[int, str] = {}
    for row in league_team_rows:
        raw = row.get("sleeper_roster_id")
        team_id = str(row.get("id") or "").strip()
        if raw is None or not team_id:
            continue
        try:
            mapping[int(raw)] = team_id
        except (TypeError, ValueError):
            continue
    return mapping


def describe_faab_moves(
    faab_moves: Sequence[Any], team_names: Mapping[str, str],
    roster_map: Mapping[int, str],
) -> str:
    """A readable summary of traded FAAB for manual cap entry.

    Sleeper moves budget, but nothing canonical tracks FAAB balances, so the
    pipeline reports the transfer instead of writing cap adjustments from a
    figure it cannot validate.
    """
    parts: list[str] = []
    for move in faab_moves:
        sender = team_names.get(roster_map.get(move.from_roster_id, ""), "unknown team")
        receiver = team_names.get(roster_map.get(move.to_roster_id, ""), "unknown team")
        parts.append(f"${move.amount} from {sender} to {receiver}")
    return "; ".join(parts)


def transactions_after_watermark(
    transactions: Sequence[Mapping[str, Any]], watermark_created_ms: int,
) -> tuple[Mapping[str, Any], ...]:
    """Only transactions newer than the watermark, oldest first.

    Resuming from a pause moves the watermark forward to the present, so a
    paused window is skipped permanently rather than replayed late.
    """
    fresh = [
        tx for tx in transactions
        if isinstance(tx, Mapping)
        and int(tx.get("created") or 0) > int(watermark_created_ms or 0)
    ]
    return tuple(sorted(fresh, key=lambda tx: (
        int(tx.get("created") or 0), str(tx.get("transaction_id") or ""),
    )))


def is_duplicate_acquisition(
    live_agreements: Sequence[Mapping[str, Any]], league_team_id: str,
) -> bool:
    """True when this team already holds a live contract for the player.

    Guards the case where owners move already-contracted players onto their
    Sleeper rosters -- after a draft, most commonly -- which would otherwise
    write a second contract on top of the real one.
    """
    return any(
        str(row.get("league_team_id") or "") == str(league_team_id)
        for row in live_agreements
    )


class SleeperSyncRunner:
    """Fetch, map, and apply one league's Sleeper transactions."""

    def __init__(
        self,
        write_client: Any,
        read_client: Any,
        league_id: str,
        *,
        fetcher: Callable[[str, int], Sequence[Mapping[str, Any]]] | None = None,
    ):
        self.write_client = write_client
        self.read_client = read_client
        self.league_id = str(league_id)
        self._fetch = fetcher or _fetch_week

    # ---- canonical reads -------------------------------------------------

    def sync_config(self) -> Mapping[str, Any] | None:
        rows = (self.read_client.table("league_sleeper_sync").select("*")
                .eq("league_id", self.league_id).execute().data or [])
        return rows[0] if rows else None

    def sleeper_league_id(self) -> str:
        rows = (self.read_client.table("leagues").select("sleeper_league_id")
                .eq("id", self.league_id).execute().data or [])
        if len(rows) != 1:
            raise ValueError("canonical league is missing or ambiguous")
        value = "".join(ch for ch in str(rows[0].get("sleeper_league_id") or "") if ch.isdigit())
        if not value:
            raise ValueError("league has no Sleeper league id configured")
        return value

    def roster_map(self) -> dict[int, str]:
        rows = (self.read_client.table("league_teams").select("id,sleeper_roster_id")
                .eq("league_id", self.league_id).execute().data or [])
        return build_roster_map(rows)

    def live_agreements(self, player_id: str) -> list[Mapping[str, Any]]:
        return list(self.read_client.table("contract_agreements")
                    .select("id,league_team_id,player_id,status,superseded_by_contract_id")
                    .eq("league_id", self.league_id).eq("player_id", str(player_id))
                    .in_("status", list(LIVE_AGREEMENT_STATUSES))
                    .is_("superseded_by_contract_id", "null").execute().data or [])

    def contract_seasons(self, agreement_id: str) -> list[Mapping[str, Any]]:
        return list(self.read_client.table("contract_seasons")
                    .select("id,season,salary,cap_hit,obligation_status")
                    .eq("contract_id", str(agreement_id)).execute().data or [])

    def dead_cap_pct(self) -> Decimal:
        rows = (self.read_client.table("league_rules").select("default_dead_cap_pct")
                .eq("league_id", self.league_id).execute().data or [])
        if len(rows) != 1 or rows[0].get("default_dead_cap_pct") is None:
            raise ValueError("canonical league dead-cap rule is missing or ambiguous")
        return Decimal(str(rows[0]["default_dead_cap_pct"]))

    # ---- exception logging ----------------------------------------------

    def record_exception(self, exception: SyncException) -> None:
        try:
            self.write_client.table("sleeper_sync_exceptions").upsert({
                "league_id": self.league_id,
                "sleeper_transaction_id": exception.transaction_id,
                "player_id": exception.player_id,
                "sleeper_roster_id": exception.roster_id,
                "exception_kind": exception.kind,
                "detail": exception.detail,
                "status": "open",
            }, on_conflict="league_id,sleeper_transaction_id,exception_kind,player_id").execute()
        except Exception:
            # An exception log that fails must never abort the sync itself.
            pass


    # ---- activity feed ---------------------------------------------------

    def log_activity(
        self, league_team_id: str, action: str, player_name: str, note: str = "",
    ) -> None:
        """Automated moves land in the same feed owners already read."""
        try:
            teams = (self.read_client.table("league_teams")
                     .select("owner_name,team_name").eq("id", league_team_id)
                     .execute().data or [])
            team = teams[0] if teams else {}
            self.write_client.table("team_activity").insert({
                "league_id": self.league_id,
                "owner_name": team.get("owner_name"),
                "team_name": team.get("team_name"),
                "player_name": player_name,
                "action": action,
                "note": note or "Applied automatically from Sleeper",
            }).execute()
        except Exception:
            # A missing feed entry must never roll back a contract write.
            pass

    def player_name(self, player_id: str) -> str:
        try:
            rows = (self.read_client.table("player_universe")
                    .select("player_name").eq("sleeper_id", str(player_id))
                    .limit(1).execute().data or [])
            if rows and str(rows[0].get("player_name") or "").strip():
                return str(rows[0]["player_name"]).strip()
        except Exception:
            pass
        return str(player_id)

    # ---- intent execution ------------------------------------------------

    def apply_release(
        self, intent: ReleaseIntent, roster_map: Mapping[int, str],
        active_season: int, dead_cap_pct: Decimal,
    ) -> SyncException | None:
        team_id = roster_map.get(intent.roster_id)
        if not team_id:
            return SyncException(
                "unmapped_roster", intent.transaction_id,
                f"Sleeper roster {intent.roster_id} has no canonical team",
                intent.player_id, intent.roster_id,
            )

        agreements = self.live_agreements(intent.player_id)
        if not agreements:
            return SyncException(
                "no_live_contract", intent.transaction_id,
                "Player was dropped in Sleeper but holds no live contract. "
                "Check whether the contract was already removed by hand.",
                intent.player_id, intent.roster_id,
            )
        if len(agreements) > 1:
            return SyncException(
                "ambiguous_contract", intent.transaction_id,
                f"Player holds {len(agreements)} live agreements; expected 1",
                intent.player_id, intent.roster_id,
            )

        agreement = agreements[0]
        owner_team = str(agreement.get("league_team_id") or "")
        if owner_team != team_id:
            return SyncException(
                "ambiguous_contract", intent.transaction_id,
                "Sleeper dropped this player from a different team than the "
                "one holding his contract.",
                intent.player_id, intent.roster_id,
            )

        charges = dead_cap_schedule(
            self.contract_seasons(str(agreement["id"])),
            from_season=active_season, dead_cap_pct=dead_cap_pct,
        )
        current = next(
            (c.amount for c in charges if c.season == active_season), Decimal("0.00"),
        )

        self.write_client.rpc("release_offseason_player_authenticated", {"p_request": {
            "league_id": self.league_id, "player_id": intent.player_id,
            "league_team_id": team_id, "season": active_season,
            "dead_cap": float(current), "idempotency_key": intent.idempotency_key,
            "notes": f"Sleeper transaction {intent.transaction_id}",
        }}).execute()

        future = [
            {"season": c.season, "amount": float(c.amount)}
            for c in charges if c.season > active_season and c.amount > 0
        ]
        if future:
            self.write_client.rpc(
                "append_release_dead_cap_schedule_authenticated", {"p_request": {
                    "league_id": self.league_id, "league_team_id": team_id,
                    "player_id": intent.player_id,
                    "release_idempotency_key": intent.idempotency_key,
                    "schedule": future,
                }},
            ).execute()

        name = self.player_name(intent.player_id)
        total = total_dead_cap(charges)
        self.log_activity(
            team_id, "drop", name,
            f"Dropped in Sleeper. Dead cap ${total} across "
            f"{len([c for c in charges if c.amount > 0])} season(s).",
        )
        return None

    def apply_acquire(
        self, intent: AcquireIntent, roster_map: Mapping[int, str], active_season: int,
    ) -> SyncException | None:
        team_id = roster_map.get(intent.roster_id)
        if not team_id:
            return SyncException(
                "unmapped_roster", intent.transaction_id,
                f"Sleeper roster {intent.roster_id} has no canonical team",
                intent.player_id, intent.roster_id,
            )

        agreements = self.live_agreements(intent.player_id)
        if is_duplicate_acquisition(agreements, team_id):
            return SyncException(
                "duplicate_live_contract", intent.transaction_id,
                "This team already holds a live contract for the player, so no "
                "new contract was written. Usually a roster move rather than a "
                "signing -- adding drafted players in Sleeper, for example.",
                intent.player_id, intent.roster_id,
            )
        if agreements:
            return SyncException(
                "duplicate_live_contract", intent.transaction_id,
                "Player was added in Sleeper while still under contract to "
                "another team. Resolve that contract before this signing.",
                intent.player_id, intent.roster_id,
            )

        self.write_client.rpc("acquire_offseason_player_authenticated", {"p_request": {
            "league_id": self.league_id, "player_id": intent.player_id,
            "league_team_id": team_id, "season": active_season,
            "salary": float(intent.salary), "years": intent.years,
            "acquisition_type": intent.acquisition_type,
            "idempotency_key": intent.idempotency_key,
            "notes": f"Sleeper transaction {intent.transaction_id}",
        }}).execute()

        name = self.player_name(intent.player_id)
        self.log_activity(
            team_id, "sign", name,
            f"Acquired in Sleeper for ${intent.salary} FAAB "
            f"on a {intent.years}-year deal.",
        )
        return None


    # ---- trades -----------------------------------------------------------

    def resolve_contract_id(self, player_id: str, league_team_id: str) -> str | None:
        """The live agreement id a trade moves, verified against the from-team."""
        for row in self.live_agreements(player_id):
            if str(row.get("league_team_id") or "") == str(league_team_id):
                return str(row.get("id"))
        return None

    def resolve_draft_pick(
        self, *, draft_year: int, round_number: int,
        original_team_id: str, expected_owner_team_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Canonical stable_pick_id for a Sleeper pick, or a reason it failed.

        A pick's identity is league + draft year + round + ORIGINAL team, which
        never changes when it is traded. Sleeper's roster_id carries that
        original owner; previous_owner_id is only the seller.
        """
        try:
            rows = (self.read_client.table("draft_pick_assets")
                    .select("stable_pick_id,current_owner_league_team_id,asset_status")
                    .eq("league_id", self.league_id)
                    .eq("draft_year", int(draft_year))
                    .eq("round_number", int(round_number))
                    .eq("original_league_team_id", str(original_team_id))
                    .execute().data or [])
        except Exception as exc:
            return None, f"draft pick lookup failed: {exc}"

        if len(rows) != 1:
            return None, (
                f"{len(rows)} canonical assets for {draft_year} round "
                f"{round_number}; expected exactly 1. The {draft_year} draft "
                "inventory may not be initialized yet."
            )
        asset = rows[0]
        if str(asset.get("asset_status") or "") != "tradable":
            return None, f"pick is {asset.get('asset_status')}, not tradable"
        if expected_owner_team_id and str(
            asset.get("current_owner_league_team_id") or ""
        ) != str(expected_owner_team_id):
            return None, (
                "canonical pick owner does not match the team Sleeper traded it from"
            )
        return str(asset.get("stable_pick_id")), None

    def apply_trade(
        self, intent: TradeIntent, roster_map: Mapping[int, str],
    ) -> SyncException | None:
        from services.canonical_trades import (
            DraftPickMovement, PlayerMovement, execute_canonical_trade,
        )

        teams = [roster_map.get(rid) for rid in intent.roster_ids]
        if not all(teams):
            missing = [r for r in intent.roster_ids if not roster_map.get(r)]
            return SyncException(
                "unmapped_roster", intent.transaction_id,
                f"Sleeper rosters {missing} have no canonical team",
            )

        player_movements = []
        for move in intent.player_moves:
            from_team = roster_map.get(move.from_roster_id) if move.from_roster_id else None
            to_team = roster_map.get(move.to_roster_id) if move.to_roster_id else None
            if not from_team or not to_team:
                return SyncException(
                    "unmapped_roster", intent.transaction_id,
                    f"Trade leg for player {move.player_id} has an unmapped roster",
                    move.player_id,
                )
            contract_id = self.resolve_contract_id(move.player_id, from_team)
            if not contract_id:
                return SyncException(
                    "no_live_contract", intent.transaction_id,
                    "Traded player holds no live contract on the team Sleeper "
                    "traded him from.",
                    move.player_id,
                )
            player_movements.append(PlayerMovement(contract_id, from_team, to_team))

        pick_movements = []
        for pick in intent.pick_moves:
            original_team = roster_map.get(pick.original_roster_id)
            from_team = roster_map.get(pick.from_roster_id)
            to_team = roster_map.get(pick.to_roster_id)
            if not original_team or not from_team or not to_team:
                return SyncException(
                    "unmapped_roster", intent.transaction_id,
                    f"{pick.season} round {pick.round_number} pick has an unmapped roster",
                )
            stable_pick_id, failure = self.resolve_draft_pick(
                draft_year=pick.season, round_number=pick.round_number,
                original_team_id=original_team, expected_owner_team_id=from_team,
            )
            if not stable_pick_id:
                return SyncException(
                    "ambiguous_contract", intent.transaction_id,
                    f"{pick.season} round {pick.round_number} pick: {failure}",
                )
            pick_movements.append(
                DraftPickMovement(stable_pick_id, from_team, to_team)
            )

        try:
            execute_canonical_trade(
                self.write_client, league_id=self.league_id,
                participant_team_ids=teams,
                idempotency_key=intent.idempotency_key,
                player_movements=player_movements,
                draft_pick_movements=pick_movements,
                notes=f"Sleeper transaction {intent.transaction_id}",
            )
        except Exception as exc:
            return SyncException(
                "unsupported_transaction", intent.transaction_id,
                f"Canonical trade execution failed: {exc}",
            )

        for team_id in teams:
            self.log_activity(
                team_id, "trade", f"{len(player_movements)} player(s)",
                f"Trade completed in Sleeper between {len(teams)} teams.",
            )

        if intent.faab_moves:
            # The trade itself succeeded; this is a note, not a failure.
            self.record_exception(SyncException(
                "unsupported_transaction", intent.transaction_id,
                "Trade completed, but it also moved FAAB, which is not posted "
                "automatically. Enter the cap adjustment by hand: "
                + describe_faab_moves(
                    intent.faab_moves, self._resolve_team_names(), roster_map,
                ),
            ))
        return None

    def active_season(self) -> int:
        rows = (self.read_client.table("league_seasons").select("season")
                .eq("league_id", self.league_id).eq("is_active", True)
                .execute().data or [])
        if len(rows) != 1:
            raise ValueError("canonical active season is missing or ambiguous")
        return int(rows[0]["season"])

    def _record_run(self, report: SyncReport) -> None:
        payload: dict[str, Any] = {
            "last_run_at": "now()",
            "last_run_status": report.status,
            "last_run_detail": report.summary(),
            "updated_at": "now()",
        }
        if report.enabled and report.watermark_transaction_id:
            payload["watermark_created_ms"] = report.watermark_created_ms
            payload["watermark_transaction_id"] = report.watermark_transaction_id
        try:
            self.write_client.table("league_sleeper_sync").update(payload).eq(
                "league_id", self.league_id,
            ).execute()
        except Exception:
            pass

    def run(self, *, weeks: Sequence[int] | None = None) -> SyncReport:
        """Apply every Sleeper transaction newer than the watermark.

        Stops at the first hard failure without advancing past it, so the next
        run retries from the same point. Transactions that merely flag an
        exception still advance -- they are recorded for review, and the
        idempotency keys make a later replay harmless either way.
        """
        config = self.sync_config()
        if config is None:
            report = SyncReport(self.league_id, enabled=False)
            report.exceptions.append(SyncException(
                "unsupported_transaction", "",
                "No league_sleeper_sync row; run the sync control migration.",
            ))
            return report

        if not config.get("sync_enabled"):
            report = SyncReport(self.league_id, enabled=False)
            self._record_run(report)
            return report

        watermark = int(config.get("watermark_created_ms") or 0)
        report = SyncReport(
            self.league_id, enabled=True, watermark_created_ms=watermark,
        )

        sleeper_league_id = self.sleeper_league_id()
        roster_map = self.roster_map()
        season = self.active_season()
        pct = self.dead_cap_pct()

        if weeks is None:
            current = current_nfl_week()
            # A small look-back absorbs a missed run without replaying the year.
            weeks = list(range(max(1, current - 1), current + 1))

        collected: list[Mapping[str, Any]] = []
        for week in weeks:
            collected.extend(self._fetch(sleeper_league_id, week))

        for transaction in transactions_after_watermark(collected, watermark):
            created = int(transaction.get("created") or 0)
            tx_id = str(transaction.get("transaction_id") or "")
            try:
                for intent in map_transaction(transaction):
                    if isinstance(intent, SkippedTransaction):
                        report.skipped += 1
                        continue
                    if isinstance(intent, ReleaseIntent):
                        failure = self.apply_release(intent, roster_map, season, pct)
                        if failure is None:
                            report.released += 1
                    elif isinstance(intent, AcquireIntent):
                        failure = self.apply_acquire(intent, roster_map, season)
                        if failure is None:
                            report.acquired += 1
                    elif isinstance(intent, TradeIntent):
                        failure = self.apply_trade(intent, roster_map)
                        if failure is None:
                            report.traded += 1
                    else:
                        failure = None
                    if failure is not None:
                        report.exceptions.append(failure)
                        self.record_exception(failure)
            except Exception as exc:
                # Hard failure: leave the watermark where it is and retry later.
                report.exceptions.append(SyncException(
                    "unsupported_transaction", tx_id,
                    f"Sync halted on this transaction: {exc}",
                ))
                self.record_exception(report.exceptions[-1])
                break

            report.processed += 1
            report.watermark_created_ms = created
            report.watermark_transaction_id = tx_id

        self._record_run(report)
        return report


def _fetch_week(sleeper_league_id: str, week: int) -> Sequence[Mapping[str, Any]]:
    import requests

    response = requests.get(
        f"{SLEEPER_BASE}/league/{sleeper_league_id}/transactions/{week}", timeout=30,
    )
    response.raise_for_status()
    return response.json() or []


def current_nfl_week() -> int:
    import requests

    response = requests.get(f"{SLEEPER_BASE}/state/nfl", timeout=20)
    response.raise_for_status()
    return int(response.json().get("week", 1) or 1)
