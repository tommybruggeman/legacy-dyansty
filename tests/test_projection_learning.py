import random

from league_ai import projection_learning as pl


def _ctx(status=None, implied=None):
    return {"implied": implied or {"A": 24.0, "B": 20.0}, "lg_implied": 22.0, "spread": {"A": 4.0, "B": -4.0}, "wind": {},
            "status": status or {}, "snaps": {}, "freed_share": {}, "qb_out": set()}


def _post(pct=None):
    return {"pct": pct or {}, "points": {"A": 24.0, "B": 20.0}, "margin": {"A": 4.0, "B": -4.0}, "wind": {}, "qb_changed": set()}


def _weeks(n, questionable_hit=0.6, seed=1):
    rnd = random.Random(seed)
    weeks = []
    for w in range(1, n + 1):
        rows, status, pct = [], {}, {}
        for i in range(40):
            pid = f"p{i}"
            proj = 6 + (i % 10)
            q = rnd.random() < 0.2
            if q:
                status[pid] = "questionable"
            actual = max(0.0, proj * (questionable_hit if q else 1.0) + rnd.gauss(0, 2))
            pct[pid] = 0.7
            rows.append({"id": pid, "pos": "WR", "team": "A" if i % 2 else "B", "raw": proj, "shown": proj, "cal": float(proj),
                         "actual": actual, "td_proj": 0.0, "td_act": 0.0})
        weeks.append({"season": 2026, "week": w, "rows": rows, "lineups": [[f"p{i}" for i in range(0, 8)], [f"p{i}" for i in range(8, 16)]],
                      "ctx": _ctx(status), "post": _post(pct)})
    return weeks


def test_a_signal_that_really_helps_switches_on():
    out = pl.learn(_weeks(16))
    q = next(s for s in out["signals"] if s["signal"] == "questionable")
    assert q["k"] < -0.2 and q["on"] and "questionable" in out["active"]


def test_a_signal_with_nothing_behind_it_stays_off():
    out = pl.learn(_weeks(16, questionable_hit=1.0))
    assert "questionable" not in out["active"]


def test_misses_get_causes_and_uncovered_ones_are_gaps():
    ctx, post = _ctx(), _post({"x": 0.0})
    r = {"id": "x", "pos": "RB", "team": "A", "actual": 0.0, "td_proj": 4.0, "td_act": 0.0}
    assert pl.causes(r, ctx, post, 14.0) == ["late_scratch"]
    assert pl.causes(r, _ctx({"x": "questionable"}), post, 14.0) == ["listed_injured"]
    r2 = {**r, "actual": 30.0, "td_act": 18.0}
    assert "td_swing" in pl.causes(r2, ctx, _post({"x": 0.7}), 12.0)
    assert pl.is_miss(r2, 12.0) and not pl.is_miss({**r, "actual": 13.0}, 12.0)
    weeks = _weeks(6, questionable_hit=1.0)
    weeks[-1]["rows"][0].update(actual=0.0, cal=15.0, raw=15.0)
    weeks[-1]["post"]["pct"]["p0"] = 0.0
    out = pl.learn(weeks)
    assert any(c["cause"] == "late_scratch" for c in out["gaps"])
    assert out["last_misses"][0]["id"] == "p0"


def test_adjustments_only_from_signals_that_are_on():
    rows = [{"id": "a", "pos": "WR", "team": "A"}, {"id": "b", "pos": "WR", "team": "A"}]
    ctx = _ctx({"a": "questionable"})
    assert pl.adjustments(rows, ctx, {"active": ["questionable"], "k": {"questionable": -0.3}}) == {"a": 0.7}
    assert pl.adjustments(rows, ctx, {"active": [], "k": {"questionable": -0.3}}) == {}


def test_red_zone_signals_compare_role_with_projected_touchdowns():
    ctx = {**_ctx(), "rz_xtd": {"a": 0.8}, "rz_avg": {"RB": 0.4}}
    r = {"id": "a", "pos": "RB", "team": "A", "cal": 12.0, "td_proj": 2.4}
    xs = pl.signal_x(r, ctx)
    assert abs(xs["rz_vs_proj"] - (4.8 - 2.4) / 12.0) < 1e-9 and abs(xs["rz_role"] - 1.0) < 1e-9
    assert "rz_role" not in pl.signal_x({**r, "id": "b"}, ctx)


def test_usage_signal_compares_our_usage_projection_with_the_calibrated_one():
    ctx = {**_ctx(), "opps": {"a": [10.0, 12.0], "b": [9.0]}, "pos_of": {"a": "RB", "b": "RB"}}
    own = pl.own_projections(ctx, {"RB": [600.0, 1000.0]})
    assert own == {"a": 6.6}   # 11 opportunities a game x 0.6 points each; b has only one game
    xs = pl.signal_x({"id": "a", "pos": "RB", "team": "A", "cal": 11.0}, {**ctx, "own": own})
    assert abs(xs["usage"] - (6.6 / 11.0 - 1)) < 1e-9


def test_usage_survives_play_by_play_being_loaded():
    stats = [{"gsis_id": "g1", "sleeper_id": "a", "week": w, "team": "A", "position": "RB", "offense_pct": 0.6, "carries": 15, "targets": 3}
             for w in range(1, 5)]
    plays = [{"week": 1, "yardline_100": 5, "rush": 1, "rusher_player_id": "g1", "rush_touchdown": 1}]
    ctx, _ = pl.context({"stats": stats, "games": [], "injuries": [], "plays": plays}, 2026, 4, {})
    assert ctx["pos_of"] == {"a": "RB"} and pl.own_projections(ctx, {"RB": [600.0, 1000.0]}) == {"a": 10.8}
