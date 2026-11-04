import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from api import alerts, lineups
from scripts import push_alerts as job

ET = ZoneInfo("America/New_York")
MT = ZoneInfo("America/Denver")


def at(y, m, d, h=12, mi=0, tz=ET):
    return datetime(y, m, d, h, mi, tzinfo=tz).astimezone(timezone.utc)


class WordingTests(unittest.TestCase):
    """The exact text Tommy approved on 2026-11-04."""

    def test_game_day(self):
        sub = {"name": "Jauan Jennings", "proj": 11.8}
        a = alerts.lineup_problem(week=10, day="Sunday", slot="WR", problem="on IR", name="Puka Nacua", player_id="1", sub=sub)
        self.assertEqual((a.title, a.body), ("Lineup problem for Sunday",
                                             "Puka Nacua is on IR and still in your WR spot. Look at starting Jauan Jennings (11.8 proj)."))
        a = alerts.lineup_problem(week=10, day="Sunday", slot="TE", problem="bye", name="Sam LaPorta", player_id="2",
                                  sub={"name": "Tucker Kraft", "proj": 9.6})
        self.assertEqual(a.body, "Sam LaPorta is on bye and still in your TE spot. Look at starting Tucker Kraft (9.6 proj).")
        a = alerts.lineup_problem(week=10, day="Sunday", slot="FLEX", problem="empty", name=None, player_id=None, sub=None)
        self.assertEqual(a.body, "Your FLEX spot is empty.")
        a = alerts.ruled_out(week=10, day="Sunday", name="Breece Hall", player_id="3", body_part="hamstring",
                             sub={"name": "Rhamondre Stevenson", "proj": 10.9})
        self.assertEqual((a.title, a.body), ("Breece Hall ruled out", "Hamstring, out for Sunday. Look at starting Rhamondre Stevenson (10.9 proj)."))
        a = alerts.inactive(week=10, name="De'Von Achane", player_id="4", sub={"name": "Kenneth Walker", "proj": 12.3})
        self.assertEqual((a.title, a.body, a.urgent), ("De'Von Achane is inactive",
                                                       "Kickoff in 30 minutes and he's in your lineup. Look at starting Kenneth Walker (12.3 proj).", True))
        self.assertEqual(alerts.inactive(week=10, name="X", player_id="4", sub=None).key, alerts.ruled_out(
            week=10, day="Sunday", name="X", player_id="4", body_part=None, sub=None).key)  # never both

    def test_results(self):
        url = "/game/9/1"
        a = alerts.final_result(season=2026, week=9, opp="Dylan", me=128.4, them=112.0, top5=True, rank=2, cut=110, game_url=url)
        self.assertEqual((a.title, a.body), ("You beat Dylan 128.4 – 112.0", "You got a win and a top 5 performance."))
        a = alerts.final_result(season=2026, week=9, opp="Nando", me=104.2, them=119.6, top5=True, rank=5, cut=104.2, game_url=url)
        self.assertEqual((a.title, a.body), ("Nando beat you 119.6 – 104.2", "You got a top 5 performance."))
        a = alerts.final_result(season=2026, week=9, opp="Grady", me=88.5, them=97.1, top5=False, rank=9, cut=110, game_url=url)
        self.assertEqual(a.title, "Grady beat you 97.1 – 88.5")
        self.assertIn(a.body, [t.format(opp="Grady", opp_score="97") for t in alerts.TRASH_TALK])
        a = alerts.final_result(season=2026, week=9, opp="Dylan", me=100.0, them=90.0, top5=False, rank=6, cut=101.2, game_url=url)
        self.assertEqual(a.body, "You got a win. You missed the top 5 by 1.2 pts.")
        a = alerts.final_result(season=2026, week=9, opp="Dylan", me=110.4, them=110.4, top5=False, rank=8, cut=120, game_url=url)
        self.assertEqual(a.title, "You tied Dylan 110.4 – 110.4")

    def test_playoff_loss_is_just_the_score(self):
        a = alerts.playoff_result(season=2026, week=16, opp="Dylan", me=200, them=210, won=False, next_round="Championship", champion=False, game_url="/")
        self.assertEqual((a.title, a.body), ("Dylan beat you 210.0 – 200.0", ""))
        a = alerts.playoff_result(season=2026, week=16, opp="Dylan", me=220, them=210, won=True, next_round="Championship", champion=False, game_url="/")
        self.assertEqual(a.body, "You're moving on to the championship.")

    def test_stat_fix_waivers_offers_playoffs_rollover(self):
        a = alerts.stat_correction(season=2026, week=9, correction_id="c1", result_changed=True, winner="Chase", winner_pts=101.4,
                                   loser="Connor", loser_pts=99.6)
        self.assertEqual((a.title, a.body, a.url), ("Stat correction, Week 9", "Result changed: Chase 101.4 – Connor 99.6.", "/activity?correction=c1"))
        a = alerts.stat_correction(season=2026, week=9, correction_id="c2", result_changed=False, winner="", winner_pts=0, loser="", loser_pts=0)
        self.assertEqual(a.body, "Score updated, result unchanged.")
        self.assertEqual(alerts.waivers_done(run_key="2026-11-15", moves=4, league_name="Legacy Dynasty").body,
                         "4 moves went through in Legacy Dynasty.")
        self.assertEqual(alerts.waivers_done(run_key="x", moves=1, league_name="Legacy Dynasty").body, "1 move went through in Legacy Dynasty.")
        self.assertEqual(alerts.trade_offer_received(offer_id="o", sender="Nando").body, "Nando sent you a trade offer.")
        self.assertEqual(alerts.trade_offer_answered(offer_id="o", partner="Dylan", accepted=True).title, "Trade accepted")
        self.assertEqual(alerts.trade_offer_answered(offer_id="o", partner="Grady", accepted=False).body, "Grady declined your trade offer.")
        self.assertEqual(alerts.bracket_set(season=2026, seed=2, opp="Dylan", week=15).body, "You're the 2 seed. You play Dylan in Week 15.")
        self.assertEqual(alerts.rollover_done(season=2027).body, "A new season has started in Legacy. Take a look at your team.")
        self.assertEqual(alerts.trade_completed(trade_id="t", league_name="Legacy Dynasty").body, "A trade was completed in Legacy Dynasty")

    def test_deadlines_and_cap(self):
        self.assertEqual(alerts.option_reminder(season=2027, left="7d", names=["Malik Nabers", "Brock Bowers"]).body,
                         "Malik Nabers and Brock Bowers to exercise their team option.")
        a = alerts.option_reminder(season=2027, left="24h", names=["Malik Nabers"])
        self.assertEqual((a.title, a.body), ("24 hours left", "Malik Nabers to exercise his team option."))
        kick = at(2027, 9, 9, 20, 20)
        self.assertEqual(alerts.taxi_last_day(season=2027, kickoff=kick, tz="America/New_York").body,
                         "The season starts tomorrow at 8:20 PM. After kickoff, no rookie can go on taxi.")
        dl = at(2026, 11, 24, 23, 59)
        self.assertEqual(alerts.trade_deadline(season=2026, left="7d", deadline=dl, tz="America/New_York").body,
                         "Trades lock Tue, Nov 24 at 11:59 PM.")
        a = alerts.over_cap(season=2027, day=date(2027, 7, 4), used=231, cap=225, day_number=1)
        self.assertEqual(a.title, "You're $6 over the cap")
        a = alerts.over_cap(season=2027, day=date(2027, 7, 7), used=231, cap=225, day_number=4)
        self.assertEqual((a.title, a.body), ("Still $6 over the cap (day 4)", "You're at $231 of $225."))


class RuleTests(unittest.TestCase):
    def test_quiet_hours_follow_the_owner(self):
        self.assertTrue(alerts.quiet(at(2026, 11, 8, 23, 30), "America/New_York"))
        self.assertTrue(alerts.quiet(at(2026, 11, 8, 7, 59), "America/New_York"))
        self.assertFalse(alerts.quiet(at(2026, 11, 8, 8, 0), "America/New_York"))
        # 1 AM Eastern is 11 PM in Denver: still quiet there.
        self.assertTrue(alerts.quiet(at(2026, 11, 9, 1, 0), "America/Denver"))
        self.assertFalse(alerts.quiet(at(2026, 11, 8, 23, 30), "America/Los_Angeles"))

    def test_gate(self):
        now = at(2026, 11, 8, 12)
        row = {"kind": "results", "urgent": False, "expires_at": None}
        self.assertTrue(alerts.gate(row, now=now, off=set(), tz=None, sent_this_week=0).send)
        self.assertEqual(alerts.gate(row, now=now, off={"results"}, tz=None, sent_this_week=0).status, "off")
        self.assertEqual(alerts.gate({**row, "expires_at": (now - timedelta(minutes=1)).isoformat()}, now=now, off=set(), tz=None,
                                     sent_this_week=0).status, "expired")
        night = at(2026, 11, 8, 3)
        held = alerts.gate(row, now=night, off=set(), tz="America/New_York", sent_this_week=0)
        self.assertEqual((held.send, held.status), (False, ""))
        self.assertTrue(alerts.gate({**row, "urgent": True}, now=night, off=set(), tz="America/New_York", sent_this_week=50).send)
        self.assertEqual(alerts.gate(row, now=now, off=set(), tz=None, sent_this_week=alerts.WEEKLY_LIMIT).status, "limited")
        self.assertTrue(alerts.gate({**row, "kind": "cap"}, now=now, off=set(), tz=None, sent_this_week=99).send)

    def test_clinched(self):
        pts = {1: 20, 2: 18, 3: 10, 4: 9, 5: 4, 6: 3}
        self.assertEqual(alerts.clinched_teams(pts, weeks_left=2, spots=4), {1, 2})
        self.assertEqual(alerts.clinched_teams(pts, weeks_left=0, spots=4), {1, 2, 3, 4})
        self.assertEqual(alerts.clinched_teams({1: 10, 2: 10, 3: 10}, weeks_left=0, spots=2), set())  # tied: points for decides

    def test_cap_streak(self):
        today = date(2027, 7, 7)
        self.assertEqual(alerts.cap_streak(today, []), 1)
        self.assertEqual(alerts.cap_streak(today, [date(2027, 7, 6), date(2027, 7, 5), date(2027, 7, 4), date(2027, 7, 1)]), 4)

    def test_trade_deadline_setting(self):
        week = lambda n: at(2026, 11, 26, 20, 15) if n == 12 else None
        self.assertEqual(alerts.parse_deadline("week 12", week), at(2026, 11, 26, 20, 15))
        self.assertEqual(alerts.parse_deadline("2026-11-24 23:59", week), at(2026, 11, 24, 23, 59))
        self.assertIsNone(alerts.parse_deadline("", week))

    def test_next_morning(self):
        self.assertEqual(alerts.next_morning(at(2026, 11, 8, 23, 30, MT)), at(2026, 11, 9, 9, 0, MT))
        self.assertEqual(alerts.next_morning(at(2026, 11, 8, 6, 0, MT)), at(2026, 11, 8, 9, 0, MT))

    def test_settings_view(self):
        view = alerts.settings_view({"off": ["cap"], "tz": "America/Chicago"})
        self.assertEqual([k["kind"] for k in view["kinds"]][:2], ["lineup", "injury"])
        self.assertFalse(next(k for k in view["kinds"] if k["kind"] == "cap")["on"])
        self.assertEqual(alerts.clean_off(["cap", "nope", "cap"]), ["cap"])


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.patch, self.removing = db, name, [], None, False

    def select(self, *_a):
        return self

    def eq(self, k, v):
        self.f.append(lambda r: r.get(k) == v)
        return self

    def in_(self, k, v):
        v = set(v)
        self.f.append(lambda r: r.get(k) in v)
        return self

    def lte(self, k, v):
        self.f.append(lambda r: str(r.get(k)) <= v)
        return self

    def gte(self, k, v):
        self.f.append(lambda r: str(r.get(k) or "") >= v)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def delete(self):
        self.removing = True
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        rows = rows if isinstance(rows, list) else [rows]
        have = self.db.setdefault(self.name, [])
        for r in rows:
            if not any(x["user_id"] == r["user_id"] and x["key"] == r["key"] for x in have):
                have.append({"id": f"r{len(have)}", **r})
        return self

    def execute(self):
        rows = [r for r in self.db.get(self.name, []) if all(f(r) for f in self.f)]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
        if self.removing:
            self.db[self.name] = [r for r in self.db.get(self.name, []) if r not in rows]
        return mock.Mock(data=rows)


class _Client:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Q(self.db, name)


class DeliveryTests(unittest.TestCase):
    def test_queue_once_then_deliver_with_switches_and_quiet_hours(self):
        db = {"push_tokens": [{"token": "aa", "user_id": "u1", "kind": "apns", "tz": "America/New_York"},
                              {"token": "bb", "user_id": "u2", "kind": "apns", "tz": "America/New_York"},
                              {"token": "ExponentPushToken[x]", "user_id": "u3", "kind": "expo"}],
              "push_prefs": [{"user_id": "u2", "league_id": "L", "off": ["waivers"], "tz": None}]}
        client = _Client(db)
        alert = alerts.waivers_done(run_key="2026-11-15", moves=4, league_name="Legacy Dynasty")
        alerts.enqueue(client, alert, league_id="L", user_ids=["u1", "u2", "u3"])
        alerts.enqueue(client, alert, league_id="L", user_ids=["u1"])  # same key: not queued twice
        self.assertEqual(len(db["push_outbox"]), 3)
        push = mock.Mock()
        push.send_alert.return_value = SimpleNamespace(ok=True, gone=False, status=200, reason="")

        night = at(2026, 11, 15, 3, 5)  # 3 AM Eastern: held for morning
        counts = alerts.deliver(client, push, night)
        self.assertEqual((counts["held"], counts["off"], counts["sent"]), (2, 1, 0))

        morning = at(2026, 11, 15, 10, 5)  # 8 AM in Denver too (u3 has no time zone: the league's)
        counts = alerts.deliver(client, push, morning)
        self.assertEqual((counts["sent"], counts["no_phone"]), (1, 1))
        token, body = push.send_alert.call_args[0]
        self.assertEqual(token, "aa")
        self.assertEqual(body, {"aps": {"alert": {"title": "Waivers processed", "body": "4 moves went through in Legacy Dynasty."},
                                        "sound": "default"}, "url": "/activity", "body": {"url": "/activity"}})
        self.assertEqual(alerts.deliver(client, push, morning)["sent"], 0)  # nothing twice

    def test_seeded_rows_never_send_and_gone_phones_are_removed(self):
        db = {"push_tokens": [{"token": "aa", "user_id": "u1", "kind": "apns"}]}
        client = _Client(db)
        alerts.enqueue(client, alerts.rollover_done(season=2027), league_id="L", user_ids=["u1"], seed=True)
        alerts.enqueue(client, alerts.clinched(season=2026), league_id="L", user_ids=["u1"])
        push = mock.Mock()
        push.send_alert.return_value = SimpleNamespace(ok=False, gone=True, status=410, reason="Unregistered")
        counts = alerts.deliver(client, push, at(2026, 11, 15, 12))
        self.assertEqual((counts["failed"], push.send_alert.call_count), (1, 1))
        self.assertEqual(db["push_tokens"], [])


class JobTests(unittest.TestCase):
    def setUp(self):
        self.sun = at(2026, 11, 8, 13, 0)
        self.starts = {"LAR": self.sun, "NYJ": self.sun, "MIA": at(2026, 11, 8, 16, 25), "SF": at(2026, 11, 8, 13, 0)}
        self.info = {
            "1": {"full_name": "Puka Nacua", "position": "WR", "team": "LAR", "injury_status": "IR"},
            "2": {"full_name": "Breece Hall", "position": "RB", "team": "NYJ", "injury_status": "Out", "injury_body_part": "Hamstring"},
            "3": {"full_name": "Sam LaPorta", "position": "TE", "team": "DET"},
            "4": {"full_name": "De'Von Achane", "position": "RB", "team": "MIA", "injury_status": "Questionable"},
            "9": {"full_name": "Healthy Guy", "position": "QB", "team": "SF"},
        }
        self.bench = [{"player_id": "7", "name": "Jauan Jennings", "pos": "WR", "proj": 11.8},
                      {"player_id": "8", "name": "Tony Pollard", "pos": "RB", "proj": 10.9}]

    def issues(self, now, status=None):
        return job.lineup_issues(week=10, slots=["QB", "WR", "RB", "TE", "RB", "FLEX"], starters=["9", "1", "2", "3", "4", "0"],
                                 info=self.info, starts=self.starts, now=now, bench=self.bench, status=status or {},
                                 tz="America/New_York")

    def test_saturday_finds_ir_out_bye_and_empty(self):
        got = {a.key: (a.title, a.body) for a, *_ in self.issues(at(2026, 11, 7, 18))}
        self.assertEqual(got["lineup:10:1"][1], "Puka Nacua is on IR and still in your WR spot. Look at starting Jauan Jennings (11.8 proj).")
        self.assertEqual(got["out:10:2"], ("Breece Hall ruled out", "Hamstring, out for Sunday. Look at starting Tony Pollard (10.9 proj)."))
        self.assertEqual(got["lineup:10:3"][1], "Sam LaPorta is on bye and still in your TE spot.")
        self.assertEqual(got["lineup:10:empty-FLEX"][1], "Your FLEX spot is empty. Look at starting Jauan Jennings (11.8 proj).")
        self.assertNotIn("out:10:4", got)  # questionable is no alert

    def test_inactive_thirty_minutes_before_kickoff_is_urgent(self):
        now = at(2026, 11, 8, 15, 55)
        got = {a.key: a for a, *_ in self.issues(now, {"4": "Out"})}
        self.assertTrue(got["out:10:4"].urgent)
        self.assertEqual(got["out:10:4"].title, "De'Von Achane is inactive")
        self.assertNotIn("lineup:10:1", got)  # 1 PM games have kicked off

    def test_lineup_alerts_open_24_hours_before(self):
        rows = self.issues(at(2026, 11, 7, 18))
        _, start, stale = next(r for r in rows if r[0].key == "lineup:10:3")
        self.assertEqual((start, stale), (self.sun - timedelta(hours=24), self.sun))

    def test_waiver_runs_and_week_over(self):
        now = at(2026, 11, 15, 3, 20)
        ms = lambda d: int(d.timestamp() * 1000)
        tx = [{"type": "waiver", "status": "complete", "status_updated": ms(at(2026, 11, 15, 3, 1))},
              {"type": "waiver", "status": "complete", "status_updated": ms(at(2026, 11, 15, 3, 1))},
              {"type": "waiver", "status": "failed", "status_updated": ms(at(2026, 11, 15, 3, 1))},
              {"type": "free_agent", "status": "complete", "status_updated": ms(at(2026, 11, 15, 3, 1))},
              {"type": "waiver", "status": "complete", "status_updated": ms(at(2026, 11, 11, 3, 1))}]
        self.assertEqual(job.waiver_runs(tx, now), {"2026-11-15": 2})
        self.assertFalse(job.week_over({"A": at(2026, 11, 9, 20, 15)}, at(2026, 11, 9, 23, 0)))
        self.assertTrue(job.week_over({"A": at(2026, 11, 9, 20, 15)}, at(2026, 11, 10, 0, 20)))

    def test_fits_is_the_lineup_rule(self):
        self.assertEqual(alerts.best_sub("FLEX", self.bench, lineups.fits)["name"], "Jauan Jennings")
        self.assertIsNone(alerts.best_sub("QB", self.bench, lineups.fits))


if __name__ == "__main__":
    unittest.main()
