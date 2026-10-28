import unittest

from league_ai.power import PlayerStrength
from league_ai.trade_finder import League, find_trades, trade_finder_tools

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]


def _p(team, sid, pos, now, dyn=None, cap=10.0, rank=20):
    p = PlayerStrength(sleeper_id=sid, name=f"{pos} {sid}", position=pos, team_id=team, owner=team, now=now, dynasty=dyn if dyn is not None else now,
                       available=True, season_ppg=now, pos_rank=rank, years_left=2)
    p.cap_hit = cap
    return p


def _team(team, qb, rbs, wrs, te):
    out = [_p(team, f"{team}-qb", "QB", qb)]
    out += [_p(team, f"{team}-rb{i}", "RB", v) for i, v in enumerate(rbs)]
    out += [_p(team, f"{team}-wr{i}", "WR", v) for i, v in enumerate(wrs)]
    out.append(_p(team, f"{team}-te", "TE", te))
    return out


def _league():
    players = (
        _team("me", 20, [18, 16, 15, 14], [9, 8, 6], 10)        # deep at RB, weak at WR
        + _team("deepwr", 20, [8, 7], [19, 18, 17, 16], 10)     # deep at WR, weak at RB: the natural partner
        + _team("deepboth", 20, [17, 16, 14], [18, 17, 15], 10) # no need at RB, so no reason to buy one
        + _team("avg", 20, [12, 11], [12, 11], 10)
    )
    teams = [{"league_team_id": t, "owner_name": t.title(), "team_name": t} for t in ("me", "deepwr", "deepboth", "avg")]
    return League(players, teams, SLOTS)


class TradeFinderTests(unittest.TestCase):
    def test_finds_the_team_that_needs_what_you_have(self):
        out = find_trades(league=_league(), asker_team_id="me", cap_space={t: 20.0 for t in ("me", "deepwr", "deepboth", "avg")})
        self.assertEqual(out["upgrading"], "WR")  # defaults to the owner's weakest group
        self.assertTrue(out["ideas"])
        top = out["ideas"][0]
        self.assertEqual(top["partner"], "Deepwr")
        self.assertTrue(all(s.startswith("RB") for s in top["open_with"]))
        self.assertGreater(top["your_lineup_gain"], 0)
        self.assertGreater(top["their_lineup_gain"], 0)
        self.assertIn("RB group ranks", top["why_they_say_yes"])
        self.assertNotIn("Deepboth", {i["partner"] for i in out["ideas"]})

    def test_every_idea_works_for_both_sides_and_fits_the_cap(self):
        out = find_trades(league=_league(), asker_team_id="me", cap_space={"me": 20.0, "deepwr": 20.0, "deepboth": 20.0, "avg": 20.0}, limit=8)
        self.assertTrue(out["ideas"])
        for idea in out["ideas"]:
            theirs = int(idea["by_their_goals"].split("back ")[1].split("%")[0])
            mine = int(idea["by_your_goals"].split("pay ")[1].split("%")[0])
            self.assertGreaterEqual(theirs, 100, idea)
            self.assertLessEqual(mine, 110, idea)
            self.assertGreaterEqual(idea["cap_after"]["you"], 0)
            self.assertGreaterEqual(idea["cap_after"]["them"], 0)

    def test_cap_blocks_deals_the_partner_cannot_absorb(self):
        league = _league()
        for p in league.by_team["me"]:
            if p.position == "RB":
                p.cap_hit = 40.0
        out = find_trades(league=league, asker_team_id="me", cap_space={"me": 20.0, "deepwr": 0.0, "deepboth": 0.0, "avg": 0.0})
        self.assertEqual(out["ideas"], [])

    def test_partner_filter_and_tool_wrapper(self):
        league = _league()
        calls = []

        class History:
            def trade_partners(self):
                return {"trades_by_owner": {"Deepwr": 6}}

            def events(self, kind=None, limit=40):
                return [{"season": 2026, "position": "WR", "summary": "Deepwr traded WR x for RB y"}]

        def load():
            calls.append(1)
            return league, {t: 20.0 for t in league.teams}, "me"

        tool = trade_finder_tools(load, History())[0]
        out = tool.fn(position="WR", partner="deepwr")
        tool.fn(position="WR")
        self.assertEqual(len(calls), 1)  # league data loads once per answer
        self.assertEqual({i["partner"] for i in out["ideas"]}, {"Deepwr"})
        self.assertEqual(out["league_trade_habits"]["trades_made_here"], {"Deepwr": 6})
        self.assertEqual(out["league_trade_habits"]["recent_wr_trades_here"], ["2026: Deepwr traded WR x for RB y"])

    def test_no_need_means_no_forced_ideas(self):
        out = find_trades(league=_league(), asker_team_id="deepboth", cap_space={t: 20.0 for t in ("me", "deepwr", "deepboth", "avg")}, want_position="QB")
        self.assertEqual(out["ideas"], [])


if __name__ == "__main__":
    unittest.main()


# ---- the two trades Tommy flagged (2026 week 6 values from the app's own data) ----------------------

def _real(team, sid, name, pos, ppg, market, age, cap, years, rank):
    p = PlayerStrength(sleeper_id=sid, name=name, position=pos, team_id=team, owner=team, now=ppg, dynasty=ppg, available=True,
                       season_ppg=ppg, recent_ppg=ppg, pos_rank=rank, age=age, years_left=years)
    p.cap_hit, p.market_value = cap, market
    return p


def _filler(team, pos, ladder):
    """(ppg, market, cap) per player, so each position has a realistic spread from stars to bench."""
    return [_real(team, f"{team}-{pos}{i}", f"{team} {pos}{i}", pos, ppg, market, 26, cap, 2, 10 * i + 5) for i, (ppg, market, cap) in enumerate(ladder)]


def _real_league(windows=None):
    players = [
        _real("mekel", "henry", "Derrick Henry", "RB", 23.5, 1401, 32.8, 18.0, 1, 24),
        _real("tommy", "btj", "Brian Thomas", "WR", 5.0, 1276, 24.0, 21.0, 2, 30),
        _real("tommy", "javonte", "Javonte Williams", "RB", 11.0, 1802, 26.0, 6.0, 1, 22),
        _real("dylan", "love", "Jordan Love", "QB", 12.0, 6532, 27.9, 30.0, 2, 8),
        _real("grady", "kyren", "Kyren Williams", "RB", 22.0, 2263, 26.2, 25.0, 1, 18),
    ]
    for t in ("mekel", "tommy", "dylan", "grady", "avg1", "avg2"):
        players += (_filler(t, "QB", [(22, 7000, 35), (15, 2500, 12)]) + _filler(t, "RB", [(16, 5000, 22), (12, 2500, 10), (8, 900, 3)])
                    + _filler(t, "WR", [(16, 5500, 24), (13, 3000, 12), (10, 1500, 6), (7, 700, 2)]) + _filler(t, "TE", [(9, 1200, 5)]))
    teams = [{"league_team_id": t, "owner_name": t.title(), "team_name": t} for t in ("mekel", "tommy", "dylan", "grady", "avg1", "avg2")]
    league = League(players, teams, SLOTS + ["SUPER_FLEX"], windows=windows, pick_values=[("2027 Mid 1st", 3500.0), ("2027 Mid 2nd", 1300.0)])
    return league


class RealTradeTests(unittest.TestCase):
    def test_henry_is_worth_far_more_than_btj_to_a_contender(self):
        from league_ai.trade_finder import evaluate_trade
        league = _real_league(windows={"Mekel": "contending", "Tommy": "middle"})
        out = evaluate_trade(league, "tommy", "Mekel", you_get=["Derrick Henry"], you_send=["Brian Thomas"])
        theirs = int(out["for_them"].split("back ")[1].split("%")[0])
        self.assertLess(theirs, 40, out)  # BTJ for Henry is nowhere near fair to a contender, whatever the market says
        self.assertIn("likely no", out["for_them"])
        self.assertIn("on the dynasty market alone you'd send 91%", out["market_view"])  # the market alone calls it even

    def test_kyren_for_love_straight_up_works_for_both(self):
        from league_ai.trade_finder import evaluate_trade
        league = _real_league(windows={"Dylan": "contending", "Grady": "rebuilding"})
        v = league.valuer
        love, kyren = league.find_player("Jordan Love"), league.find_player("Kyren Williams")
        self.assertGreater(v.value_to("dylan", kyren), v.value_to("dylan", love))  # all-in Dylan would rather have Kyren
        self.assertGreater(v.value_to("grady", love), v.value_to("grady", kyren))  # rebuilding Grady would rather have Love
        out = evaluate_trade(league, "grady", "Dylan", you_get=["Jordan Love"], you_send=["Kyren Williams"])
        self.assertIn("likely yes", out["for_them"])
        self.assertIn("good for you", out["for_you"])
        self.assertIn("market fair would add about 2027 Mid 1st", out["market_view"])  # the old answer, kept as context only

    def test_adding_a_first_is_flagged_as_a_cheaper_opening(self):
        from league_ai.trade_finder import evaluate_trade
        league = _real_league(windows={"Dylan": "contending", "Grady": "rebuilding"})
        out = evaluate_trade(league, "grady", "Dylan", you_get=["Jordan Love"], you_send=["Kyren Williams"], picks_you_send=["2027 1st"])
        self.assertIn("open without 2027 1st", out.get("cheaper_opening", ""), out)

    def test_windows_come_from_standings_when_nobody_said(self):
        league = _real_league()
        self.assertEqual(set(league.windows.values()) <= {"contending", "middle", "rebuilding"}, True)
        league.set_windows({"Grady": "rebuilding"})
        self.assertEqual(league.window("grady"), "rebuilding")


class InjuryTiltTest(unittest.TestCase):
    def test_recent_major_injury_is_explained_and_tilts_only_when_on(self):
        from league_ai import trade_value
        from league_ai.injuries import recent_injury
        from league_ai.trade_value import Valuer

        healthy, hurt = _p("A", "h", "RB", 15.0), _p("A", "x", "RB", 15.0)
        healthy.market_value = hurt.market_value = 5000.0
        hurt.injury_mult, hurt.injury_note = recent_injury([{"last_game_season": 2026, "injury": "Knee", "games_missed": 9}], 2026)
        v = Valuer([healthy, hurt], {"A": "rebuilding"})
        self.assertEqual(v.base(hurt, "rebuilding"), v.base(healthy, "rebuilding"))
        self.assertIn("major knee", v.explain("A", hurt))
        trade_value.INJURY_TILT = True
        try:
            self.assertLess(v.base(hurt, "rebuilding"), v.base(healthy, "rebuilding"))
        finally:
            trade_value.INJURY_TILT = False
