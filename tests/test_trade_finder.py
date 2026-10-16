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
        self.assertTrue(all(s.startswith("RB") for s in top["you_send"]))
        self.assertGreater(top["your_lineup_gain"], 0)
        self.assertGreater(top["their_lineup_gain"], 0)
        self.assertIn("RB group ranks", top["why_they_say_yes"])
        self.assertNotIn("Deepboth", {i["partner"] for i in out["ideas"]})

    def test_every_idea_is_fair_and_cap_legal(self):
        out = find_trades(league=_league(), asker_team_id="me", cap_space={"me": 20.0, "deepwr": 20.0, "deepboth": 20.0, "avg": 20.0}, limit=8)
        for idea in out["ideas"]:
            pct = int(idea["value_check"].split("send ")[1].split("%")[0])
            self.assertTrue(90 <= pct <= 130, idea)
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
