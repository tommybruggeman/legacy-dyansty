"""The run summary must separate new work from replays.

Every scheduled run re-reads a look-back window, so most transactions it sees
have already been applied. Counting those as signings made the summary useless
for answering "did this week's moves land?" -- the question the summary exists
to answer.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from services.sleeper_pipeline import SleeperSyncRunner, SyncException


def waiver(tx_id, *, created, adds=None, drops=None, status="complete", bid=3):
    return {
        "transaction_id": tx_id,
        "type": "waiver",
        "status": status,
        "created": created,
        "status_updated": created + 1000,
        "adds": adds,
        "drops": drops,
        "settings": {"waiver_bid": bid},
    }


class StubRunner(SleeperSyncRunner):
    """A runner with every database read stubbed out."""

    def __init__(self, transactions, applied_keys=()):
        super().__init__(object(), object(), "league-1",
                         fetcher=lambda _league, _week: transactions)
        self.applied_keys = set(applied_keys)
        self.releases: list[str] = []
        self.acquires: list[str] = []
        self.recorded: list[SyncException] = []
        self.recorded_run = False

    def sync_config(self):
        return {"sync_enabled": True, "watermark_created_ms": 0}

    def sleeper_league_id(self):
        return "123"

    def roster_map(self):
        return {1: "team-1", 2: "team-2"}

    def active_season(self):
        return 2026

    def dead_cap_pct(self):
        return Decimal("50")

    def already_applied(self, idempotency_key):
        return str(idempotency_key) in self.applied_keys

    def apply_release(self, intent, roster_map, active_season, dead_cap_pct):
        self.releases.append(intent.idempotency_key)
        return None

    def apply_acquire(self, intent, roster_map, active_season):
        self.acquires.append(intent.idempotency_key)
        return None

    def record_exception(self, exception):
        self.recorded.append(exception)

    def _record_run(self, report):
        self.report = report
        self.recorded_run = True


class RunAccounting(unittest.TestCase):
    def test_new_transaction_counts_as_new_work(self):
        runner = StubRunner([waiver("t1", created=1000, adds={"100": 1}, drops={"200": 1})])
        report = runner.run(weeks=[1])

        self.assertEqual(report.acquired, 1)
        self.assertEqual(report.released, 1)
        self.assertEqual(report.replayed, 0)
        self.assertEqual(runner.acquires, ["sleeper:t1:acquire:100"])
        self.assertEqual(runner.releases, ["sleeper:t1:release:200"])

    def test_already_applied_work_is_not_counted_again(self):
        runner = StubRunner(
            [waiver("t1", created=1000, adds={"100": 1}, drops={"200": 1})],
            applied_keys=["sleeper:t1:acquire:100", "sleeper:t1:release:200"],
        )
        report = runner.run(weeks=[1])

        self.assertEqual(report.replayed, 2)
        self.assertEqual(report.acquired, 0)
        self.assertEqual(report.released, 0)
        self.assertEqual(report.exceptions, [])
        self.assertEqual(runner.acquires, [])
        self.assertEqual(runner.releases, [])

    def test_replayed_drop_is_never_flagged_as_missing_contract(self):
        """The old path re-ran the release, found no live contract, and flagged."""
        runner = StubRunner(
            [waiver("t1", created=1000, drops={"200": 1})],
            applied_keys=["sleeper:t1:release:200"],
        )
        report = runner.run(weeks=[1])

        self.assertEqual(runner.recorded, [])
        self.assertEqual(len(report.exceptions), 0)

    def test_partly_applied_transaction_finishes_the_missing_half(self):
        runner = StubRunner(
            [waiver("t1", created=1000, adds={"100": 1}, drops={"200": 1})],
            applied_keys=["sleeper:t1:release:200"],
        )
        report = runner.run(weeks=[1])

        self.assertEqual(report.replayed, 1)
        self.assertEqual(report.acquired, 1)
        self.assertEqual(runner.acquires, ["sleeper:t1:acquire:100"])

    def test_failed_claim_is_skipped_not_replayed(self):
        runner = StubRunner([waiver("t1", created=1000, adds={"100": 1}, status="failed")])
        report = runner.run(weeks=[1])

        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.replayed, 0)
        self.assertEqual(report.acquired, 0)

    def test_summary_reports_replays_separately(self):
        runner = StubRunner(
            [waiver("t1", created=1000, adds={"100": 1})],
            applied_keys=["sleeper:t1:acquire:100"],
        )
        summary = runner.run(weeks=[1]).summary()

        self.assertIn("0 signed", summary)
        self.assertIn("1 already applied", summary)

    def test_watermark_follows_effect_time_not_submission_time(self):
        """A claim submitted before the watermark still counts once it runs."""
        runner = StubRunner([waiver("t1", created=1000, adds={"100": 1})])
        report = runner.run(weeks=[1])

        self.assertEqual(report.watermark_created_ms, 2000)
        self.assertEqual(report.watermark_transaction_id, "t1")


if __name__ == "__main__":
    unittest.main()


class BackfillNamedTransactions(unittest.TestCase):
    """Repairing a gap must not move the watermark or touch anything else."""

    def transactions(self):
        return [
            waiver("gap", created=1000, adds={"100": 1}),
            waiver("later", created=9000, adds={"200": 2}),
        ]

    def test_applies_only_the_named_transaction(self):
        runner = StubRunner(self.transactions())
        report = runner.apply_specific(["gap"], weeks=[1])

        self.assertEqual(runner.acquires, ["sleeper:gap:acquire:100"])
        self.assertEqual(report.acquired, 1)
        self.assertEqual(report.processed, 1)

    def test_never_records_a_run_or_a_watermark(self):
        runner = StubRunner(self.transactions())
        report = runner.apply_specific(["gap"], weeks=[1])

        self.assertFalse(runner.recorded_run)
        self.assertEqual(report.watermark_created_ms, 0)
        self.assertIsNone(report.watermark_transaction_id)

    def test_work_already_applied_is_left_alone(self):
        runner = StubRunner(self.transactions(), applied_keys=["sleeper:gap:acquire:100"])
        report = runner.apply_specific(["gap"], weeks=[1])

        self.assertEqual(runner.acquires, [])
        self.assertEqual(report.replayed, 1)

    def test_an_unknown_id_is_reported_rather_than_ignored(self):
        runner = StubRunner(self.transactions())
        report = runner.apply_specific(["gap", "nonsense"], weeks=[1])

        self.assertEqual(report.acquired, 1)
        self.assertEqual([e.transaction_id for e in report.exceptions], ["nonsense"])

    def test_naming_nothing_does_nothing(self):
        runner = StubRunner(self.transactions())
        report = runner.apply_specific([], weeks=[1])

        self.assertEqual(runner.acquires, [])
        self.assertEqual(report.processed, 0)


class TradeActivityWording(unittest.TestCase):
    """A trade entry has to say what moved, both ways.

    The feed read "Trade - 2 player(s)", which tells an owner nothing about a
    deal he may not have made himself.
    """

    def runner(self):
        r = StubRunner([])
        r.names = {"100": "Bhayshul Tuten", "200": "Jordan Love"}
        r.player_name = lambda pid: r.names.get(str(pid), str(pid))
        return r

    def intent(self):
        from services.sleeper_transaction_adapter import map_transaction
        return map_transaction({
            "transaction_id": "t9", "type": "trade", "status": "complete",
            "roster_ids": [1, 2],
            "adds": {"100": 1, "200": 2}, "drops": {"100": 2, "200": 1},
            "draft_picks": [{"season": 2027, "round": 3, "roster_id": 2,
                             "previous_owner_id": 2, "owner_id": 1}],
            "waiver_budget": [{"sender": 1, "receiver": 2, "amount": 4}],
        })[0]

    def test_each_side_lists_players_picks_and_cash(self):
        runner = self.runner()
        roster_map = {1: "team-1", 2: "team-2"}

        got, gave = runner._trade_sides(self.intent(), roster_map, "team-1")
        self.assertEqual(got, ["Bhayshul Tuten", "2027 round 3 pick"])
        self.assertEqual(gave, ["Jordan Love", "$4 FAAB"])

        got, gave = runner._trade_sides(self.intent(), roster_map, "team-2")
        self.assertEqual(got, ["Jordan Love", "$4 FAAB"])
        self.assertEqual(gave, ["Bhayshul Tuten", "2027 round 3 pick"])

    def test_a_team_in_no_leg_of_the_trade_gets_nothing(self):
        runner = self.runner()
        got, gave = runner._trade_sides(
            self.intent(), {1: "team-1", 2: "team-2"}, "team-3",
        )
        self.assertEqual((got, gave), ([], []))


class TradedFaabPosting(unittest.TestCase):
    """Traded FAAB has to reach the cap panel without anyone typing it in.

    The sync used to post the players and picks, then log "enter the cap
    adjustment by hand" for the cash. Dylan's $3 to Nando on Sep 29 sat in
    that exception and never reached either team's panel.
    """

    TEAMS = {"team-7": "Dylan Burruel", "team-8": "Nando Munoz"}
    ROSTERS = {7: "team-7", 8: "team-8"}

    def tx(self, **extra):
        from services.sleeper_transaction_adapter import map_transaction
        return map_transaction({
            "transaction_id": "1410698246916984832", "type": "trade",
            "status": "complete", "roster_ids": [7, 8],
            "adds": {"7569": 8, "13287": 7}, "drops": {"7569": 7, "13287": 8},
            "draft_picks": [{"season": 2027, "round": 2, "roster_id": 7,
                             "previous_owner_id": 7, "owner_id": 8}],
            "waiver_budget": [{"amount": 3, "sender": 7, "receiver": 8}],
            **extra,
        })[0]

    def runner(self, existing=()):
        runner = StubRunner([])
        runner._resolve_team_names = lambda: dict(self.TEAMS)
        runner.existing_faab_rows = lambda _tx: list(existing)
        runner.inserted = []

        class Table:
            def insert(_self, rows):
                runner.inserted.extend(rows)
                return _self

            def execute(_self):
                return None

        class Client:
            def table(_self, name):
                assert name == "cap_adjustments"
                return Table()

        runner.write_client = Client()
        return runner

    def test_sender_is_charged_and_receiver_credited(self):
        from services.sleeper_pipeline import faab_cap_adjustment_rows

        rows, problems = faab_cap_adjustment_rows(
            self.tx().faab_moves, league_id="league-1", season=2026,
            transaction_id="1410698246916984832",
            team_names=self.TEAMS, roster_map=self.ROSTERS,
        )
        self.assertEqual(problems, [])
        self.assertEqual(
            [(r["owner_name"], r["amount"], r["counterparty_owner"], r["season"],
              r["adjustment_type"]) for r in rows],
            [("Dylan Burruel", 3, "Nando Munoz", 2026, "trade_carryover"),
             ("Nando Munoz", -3, "Dylan Burruel", 2026, "trade_carryover")],
        )
        self.assertTrue(all("1410698246916984832" in r["note"] for r in rows))

    def test_a_trade_with_picks_and_faab_posts_the_faab(self):
        runner = self.runner()
        self.assertIsNone(runner.post_faab_moves(self.tx(), self.ROSTERS))
        self.assertEqual(
            [(r["owner_name"], r["amount"]) for r in runner.inserted],
            [("Dylan Burruel", 3), ("Nando Munoz", -3)],
        )

    def test_a_replay_never_charges_twice(self):
        existing = [
            {"owner_name": "Dylan Burruel", "counterparty_owner": "Nando Munoz", "amount": 3},
            {"owner_name": "Nando Munoz", "counterparty_owner": "Dylan Burruel", "amount": "-3"},
        ]
        runner = self.runner(existing)
        self.assertIsNone(runner.post_faab_moves(self.tx(), self.ROSTERS))
        self.assertEqual(runner.inserted, [])

    def test_half_posted_leg_finishes_the_missing_half(self):
        existing = [{"owner_name": "Dylan Burruel",
                     "counterparty_owner": "Nando Munoz", "amount": 3}]
        runner = self.runner(existing)
        runner.post_faab_moves(self.tx(), self.ROSTERS)
        self.assertEqual([(r["owner_name"], r["amount"]) for r in runner.inserted],
                         [("Nando Munoz", -3)])

    def test_unmapped_roster_is_flagged_not_guessed(self):
        runner = self.runner()
        problem = runner.post_faab_moves(self.tx(), {7: "team-7"})
        self.assertIsNotNone(problem)
        self.assertIn("by hand", problem.detail)
        self.assertEqual(runner.inserted, [])

    def test_a_trade_with_no_faab_writes_nothing(self):
        runner = self.runner()
        self.assertIsNone(runner.post_faab_moves(self.tx(waiver_budget=[]), self.ROSTERS))
        self.assertEqual(runner.inserted, [])

    def test_cash_only_trade_is_applied_not_rejected(self):
        runner = self.runner()
        intent = self.tx(adds=None, drops=None, draft_picks=[])
        self.assertIsNone(runner.apply_trade(intent, self.ROSTERS))
        self.assertEqual([(r["owner_name"], r["amount"]) for r in runner.inserted],
                         [("Dylan Burruel", 3), ("Nando Munoz", -3)])
        self.assertEqual(runner.recorded, [])

    def test_trade_posts_faab_after_the_canonical_trade(self):
        from unittest import mock

        runner = self.runner()
        runner.resolve_contract_id = lambda pid, team: f"c-{pid}" if team in {
            "team-7", "team-8"} else None
        runner.resolve_draft_pick = lambda **_kw: ("pick-1", None)
        runner.log_activity = lambda *a, **k: None
        with mock.patch("services.canonical_trades.execute_canonical_trade") as trade:
            self.assertIsNone(runner.apply_trade(self.tx(), self.ROSTERS))
        trade.assert_called_once()
        self.assertEqual([(r["owner_name"], r["amount"]) for r in runner.inserted],
                         [("Dylan Burruel", 3), ("Nando Munoz", -3)])
        self.assertEqual(runner.recorded, [])
