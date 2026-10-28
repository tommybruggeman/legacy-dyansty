import unittest

from league_ai.trade_backtest import _pick_value, _value_at, backtest, fit_weight, lean_label, side_share


def _a(now, market):
    return {"now": now, "market": market}


def _trade(buyer, seller, vet, youth):
    """buyer (contender) gets a producing veteran, seller (rebuilder) gets a younger, pricier-on-market asset."""
    return {"sides": {buyer: {"gets": [vet], "gives": [youth]}, seller: {"gets": [youth], "gives": [vet]}}}


class BacktestTests(unittest.TestCase):
    def setUp(self):
        vet, youth = _a(6000, 1500), _a(1000, 4000)
        self.trades = [_trade("Dylan", "Grady", vet, youth), _trade("Dylan", "Nando", _a(5000, 1800), _a(1200, 3500)),
                       _trade("Mekel", "Grady", _a(5500, 1400), _a(900, 3800)), _trade("Dylan", "Grady", _a(5200, 1200), _a(800, 3300)),
                       _trade("Mekel", "Nando", _a(4800, 1300), _a(1100, 3600))]

    def test_market_only_misses_trades_both_sides_wanted(self):
        out = backtest(self.trades)
        self.assertEqual(out["hit_rate"]["market_only"], 0.0)  # on the market alone the buyer always overpays
        self.assertGreater(out["hit_rate"]["learned"], out["hit_rate"]["market_only"])

    def test_learns_each_owner_lean(self):
        self.assertEqual(lean_label(fit_weight(self.trades, "Dylan")), "contending")
        self.assertEqual(lean_label(fit_weight(self.trades, "Grady")), "rebuilding")
        self.assertIsNone(fit_weight(self.trades[:1], "Dylan"))  # one trade isn't enough to call it

    def test_value_lookups(self):
        hist = [("2025-09-01", 100.0), ("2025-10-01", 200.0)]
        self.assertEqual(_value_at(hist, "2025-09-15"), 100.0)
        self.assertEqual(_value_at(hist, "2025-08-01"), 100.0)
        self.assertEqual(_value_at(hist, "2026-01-01"), 200.0)
        self.assertEqual(_pick_value([(2027, 1, 3000.0), (2027, 2, 1000.0)], "2027 R1 pick (orig. Grady)", 2026), 3000.0)
        self.assertEqual(side_share({"gets": [_a(1, 1)], "gives": []}, 0.5), 9.99)


if __name__ == "__main__":
    unittest.main()


class LeaningTests(unittest.TestCase):
    def test_trading_history_tips_a_middle_team(self):
        from league_ai.trade_value import team_windows
        teams = [{"league_team_id": t, "owner_name": t} for t in ("A", "B", "C", "D", "E", "F")]
        ranks = {t: i + 1 for i, t in enumerate("ABCDEF")}
        base = team_windows([], teams, ranks, ranks, records={"A": (6, 0)})
        self.assertEqual(base["C"], "middle")
        self.assertEqual(team_windows([], teams, ranks, ranks, leanings={"C": "contending"})["C"], "middle")  # history alone isn't enough
        self.assertEqual(team_windows([], teams, ranks, ranks, records={"B": (4, 2)}, leanings={"B": "contending"})["B"], "contending")


def test_compare_variants_uses_pick_book_and_injury_tilt():
    from league_ai.trade_backtest import compare_variants

    trade = {"sides": {
        "A": {"gets": [{"pick": 1.0, "market": 1000.0, "book": 3000.0}], "gives": [{"market": 3000.0, "now": 3000.0, "inj": 1.0}]},
        "B": {"gets": [{"market": 3000.0, "now": 3000.0, "inj": 1.0}], "gives": [{"pick": 1.0, "market": 1000.0, "book": 3000.0}]},
    }}
    hurt = {"sides": {
        "A": {"gets": [{"market": 4000.0, "now": 1000.0, "inj": 0.5}], "gives": [{"market": 2500.0, "now": 2500.0, "inj": 1.0}]},
        "B": {"gets": [{"market": 2500.0, "now": 2500.0, "inj": 1.0}], "gives": [{"market": 4000.0, "now": 1000.0, "inj": 0.5}]},
    }}
    out = compare_variants([trade, hurt])
    assert out["picks"]["imbalance"] < out["current"]["imbalance"]
    assert out["trades_with_picks"] == 1 and out["trades_with_injured"] == 1
    assert out["both"]["graded"] == 2
