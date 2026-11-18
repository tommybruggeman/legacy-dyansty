import random

from league_ai.trade_fit import best_lineup, decoys, directions_at, evaluate, side_features, utility, DEFAULT_WEIGHTS

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]


def _p(pid, pos, ppg, market):
    return {"pid": pid, "pos": pos, "ppg": ppg, "market": market, "now": market, "cap": 5, "years": 1}


def _roster(prefix, strong, weak, rng):
    """A team deep at `strong` and thin at `weak`."""
    out = []
    for i, pos in enumerate(["QB", "QB", "RB", "RB", "RB", "WR", "WR", "WR", "WR", "TE", "TE"] + [strong] * 2):
        ppg = (16 if pos == strong else 6 if pos == weak else 11) + rng.random() * 3
        out.append(_p(f"{prefix}{i}", pos, ppg, ppg * 200 + rng.random() * 1500))
    return out


def _need_swap(n, rng):
    """Both teams send from a deep room into the other's hole: what this league's trades look like."""
    a, b = _roster(f"a{n}-", "RB", "QB", rng), _roster(f"b{n}-", "QB", "RB", rng)
    give_a = max((p for p in a if p["pos"] == "RB"), key=lambda p: p["ppg"])
    give_b = max((p for p in b if p["pos"] == "QB"), key=lambda p: p["ppg"])
    return {"tid": str(n), "season": 2025, "week": 5, "sides": {"A": {"gives": [give_a], "gets": [give_b]}, "B": {"gives": [give_b], "gets": [give_a]}},
            "rosters": {"A": a, "B": b}, "directions": {"A": "middle", "B": "middle"}}


def test_swap_that_fills_both_holes_helps_both_lineups():
    t = _need_swap(1, random.Random(1))
    fa = side_features(t["rosters"]["A"], t["sides"]["A"]["gives"], t["sides"]["A"]["gets"], "middle", SLOTS)
    fb = side_features(t["rosters"]["B"], t["sides"]["B"]["gives"], t["sides"]["B"]["gets"], "middle", SLOTS)
    assert fa["lineup_gain"] > 0 and fb["lineup_gain"] > 0
    assert fa["by_pos"].get("QB", 0) > 0 and fb["by_pos"].get("RB", 0) > 0


def test_rebuilder_selling_for_picks_and_cap_reads_sensible():
    rng = random.Random(2)
    r = _roster("r-", "WR", "TE", rng)
    vet = max(r, key=lambda p: p["ppg"])
    vet["cap"] = 30
    pick = {"pick": 1.0, "market": vet["market"] * 0.9, "book": vet["market"] * 0.9}
    f = side_features(r, [vet], [pick], "rebuilding", SLOTS)
    assert f["lineup_gain"] < 0 and f["cap_freed"] == 30
    f["value"] = 0.0
    w = {**DEFAULT_WEIGHTS, "R": 0.25}
    assert utility(f, w) > 0
    assert utility(f, {**w, "R": 0.0}) < 0  # without selling credit the lost lineup points make it read badly


def test_fit_beats_plain_value_on_need_swaps():
    rng = random.Random(3)
    trades = [_need_swap(n, rng) for n in range(20)]
    out = evaluate(trades, SLOTS, n=12)
    assert out["graded"] == 20
    assert out["auc"]["any_decoy"]["fit_loo"] > 0.6
    assert out["auc"]["same_value_decoy"]["fit_loo"] > out["auc"]["same_value_decoy"]["market"]


def test_decoys_keep_shape_and_differ():
    t = _need_swap(4, random.Random(4))
    ds = decoys(t, 10, "value")
    assert ds and all(len(d["A"]["gives"]) == 1 and d["A"]["gives"] == d["B"]["gets"] for d in ds)


def test_best_lineup_and_directions():
    total, by_pos = best_lineup([_p("1", "QB", 20, 1), _p("2", "QB", 15, 1), _p("3", "RB", 10, 1)], ["QB", "RB", "SUPER_FLEX"])
    assert total == 45 and by_pos["QB"] == 35
    recs = {f"o{i}": [(2025, w, i < 3, 100 - i) for w in range(1, 6)] for i in range(10)}
    d = directions_at(recs, 2025, 6)
    assert d["o0"] == "contending" and d["o9"] == "rebuilding" and d["o5"] == "middle"
