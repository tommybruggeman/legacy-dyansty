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
