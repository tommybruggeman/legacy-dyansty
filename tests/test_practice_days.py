import unittest
from datetime import date, datetime, timezone

from league_ai import injury_outlook as io
from pipeline.nfl_data import practice_days as pd_
from pipeline.nfl_data.crosswalk import Crosswalk
from tests.test_nfl_data_pipeline import CROSSWALK_CSV

# Week 9: ATL plays Sunday Nov 8, CIN plays Thursday Nov 5. Week 8 a week earlier.
GAMES = [
    {"season": 2026, "week": 8, "gameday": "2026-11-01", "home_team": "ATL", "away_team": "TB"},
    {"season": 2026, "week": 9, "gameday": "2026-11-08", "home_team": "ATL", "away_team": "NO"},
    {"season": 2026, "week": 8, "gameday": "2026-10-29", "home_team": "CIN", "away_team": "CLE"},
    {"season": 2026, "week": 9, "gameday": "2026-11-05", "home_team": "BAL", "away_team": "CIN"},
]
SCHEDULE = pd_.upcoming_games(GAMES, 2026)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def nflverse(team, week, gsis, name, pos, practice, status=""):
    return {"season": 2026, "week": week, "team": team, "gsis_id": gsis, "full_name": name, "position": pos, "season_type": "REG",
            "practice_status": practice, "report_status": status, "practice_primary_injury": "Ankle", "report_primary_injury": ""}


class ReportDayTests(unittest.TestCase):
    def test_file_rebuilt_in_the_morning_holds_yesterdays_report(self):
        self.assertEqual(pd_.report_day(utc(2026, 11, 4, 15, 21)), date(2026, 11, 3))  # 10:21am ET Wed -> Tue

    def test_file_rebuilt_in_the_evening_holds_todays_report(self):
        self.assertEqual(pd_.report_day(utc(2026, 11, 4, 23, 30)), date(2026, 11, 4))  # 6:30pm ET Wed

    def test_report_days_follow_the_game(self):
        self.assertEqual(pd_.game_for_report(SCHEDULE, "ATL", date(2026, 11, 4)), (date(2026, 11, 8), 9))  # Wed for Sunday
        self.assertEqual(pd_.game_for_report(SCHEDULE, "ATL", date(2026, 11, 6)), (date(2026, 11, 8), 9))  # Fri
        self.assertIsNone(pd_.game_for_report(SCHEDULE, "ATL", date(2026, 11, 7)))  # no Saturday report for Sunday
        self.assertIsNone(pd_.game_for_report(SCHEDULE, "ATL", date(2026, 11, 2)))  # Monday after a game
        self.assertEqual(pd_.game_for_report(SCHEDULE, "CIN", date(2026, 11, 4)), (date(2026, 11, 5), 9))  # Wed before Thursday game
        self.assertEqual(pd_.game_for_report(SCHEDULE, "CIN", date(2026, 11, 2)), (date(2026, 11, 5), 9))  # Mon before Thursday game

    def test_short_week_games_report_the_three_days_before(self):
        games = [{"season": 2026, "week": 12, "gameday": "2026-11-25", "home_team": "LA", "away_team": "GB"},  # Wednesday
                 {"season": 2026, "week": 12, "gameday": "2026-11-26", "home_team": "BUF", "away_team": "KC"},  # Thanksgiving
                 {"season": 2026, "week": 16, "gameday": "2026-12-19", "home_team": "DEN", "away_team": "LV"}]  # Saturday
        sched = pd_.upcoming_games(games, 2026)
        self.assertEqual(pd_.game_for_report(sched, "GB", date(2026, 11, 22)), (date(2026, 11, 25), 12))  # Sun before Wed
        self.assertEqual(pd_.game_for_report(sched, "GB", date(2026, 11, 24)), (date(2026, 11, 25), 12))  # Tue
        self.assertEqual(pd_.game_for_report(sched, "KC", date(2026, 11, 23)), (date(2026, 11, 26), 12))  # Mon before Thu
        self.assertIsNone(pd_.game_for_report(sched, "KC", date(2026, 11, 22)))
        self.assertEqual(pd_.game_for_report(sched, "DEN", date(2026, 12, 16)), (date(2026, 12, 19), 16))  # Wed before Sat
        self.assertEqual(pd_.game_for_report(sched, "DEN", date(2026, 12, 18)), (date(2026, 12, 19), 16))  # Fri


class NflverseDayTests(unittest.TestCase):
    def test_only_this_weeks_report_on_a_report_day_is_kept(self):
        cw = Crosswalk.from_csv(CROSSWALK_CSV)
        records = [
            nflverse("ATL", 9, "00-0039139", "Bijan Robinson", "RB", "Limited Participation in Practice"),
            nflverse("ATL", 8, "00-0039139", "Bijan Robinson", "RB", "Full Participation in Practice", "Questionable"),
            nflverse("CIN", 9, "00-0036900", "Ja'Marr Chase", "WR", "Did Not Participate In Practice"),
            nflverse("ATL", 9, "00-0000009", "Some Tackle", "T", "Did Not Participate In Practice"),
        ]
        rows = pd_.from_nflverse(records, cw, SCHEDULE, 2026, utc(2026, 11, 5, 15, 0))  # Thu morning -> Wed report
        by = {r["gsis_id"]: r for r in rows}
        self.assertEqual(set(by), {"00-0039139", "00-0036900"})
        self.assertEqual(by["00-0039139"]["practice_status"], "Limited")
        self.assertEqual(by["00-0039139"]["day"], "Wed")
        self.assertEqual(by["00-0039139"]["week"], 9)
        self.assertEqual(by["00-0039139"]["days_to_game"], 4)
        self.assertEqual(by["00-0039139"]["sleeper_id"], "9226")
        self.assertEqual(by["00-0036900"]["practice_status"], "DNP")

    def test_last_weeks_file_on_a_non_report_day_writes_nothing(self):
        cw = Crosswalk.from_csv(CROSSWALK_CSV)
        records = [nflverse("ATL", 8, "00-0039139", "Bijan Robinson", "RB", "Full Participation in Practice")]
        self.assertEqual(pd_.from_nflverse(records, cw, SCHEDULE, 2026, utc(2026, 11, 3, 15, 0)), [])  # Mon report day: ATL has none


class EspnNoteTests(unittest.TestCase):
    def test_weekday_names_the_day(self):
        got = pd_.practice_mentions("Robinson (ankle) did not practice Wednesday but was a limited participant Thursday.", date(2026, 11, 5))
        self.assertEqual(got, [(date(2026, 11, 4), "DNP"), (date(2026, 11, 5), "Limited")])

    def test_no_weekday_means_the_day_written(self):
        self.assertEqual(pd_.practice_mentions("Chase practiced fully, coach said.", date(2026, 11, 6)), [(date(2026, 11, 6), "Full")])

    def test_limited_role_is_not_practice(self):
        self.assertEqual(pd_.practice_mentions("He played a limited role in Sunday's loss.", date(2026, 11, 2)), [])

    def test_espn_payload_rows(self):
        cw = Crosswalk.from_csv(CROSSWALK_CSV)
        payload = {"injuries": [{"abbreviation": "ATL", "injuries": [{
            "athlete": {"id": "4430807", "displayName": "Bijan Robinson", "position": {"abbreviation": "RB"}, "team": {"abbreviation": "ATL"}},
            "date": "2026-11-04T22:40Z", "status": "Questionable", "details": {"type": "Ankle"},
            "shortComment": "Robinson (ankle) was limited at Wednesday's practice."}]}]}
        rows = pd_.from_espn(payload, cw, SCHEDULE, 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["practice_date"], rows[0]["practice_status"], rows[0]["source"], rows[0]["week"]), ("2026-11-04", "Limited", "espn_news", 9))

    def test_weekday_carries_to_the_next_clause_of_the_sentence(self):
        note = ("Evans joined the team on Wednesday as it began prepping for Sunday, but the wideout was merely a spectator for "
                "the first practice. He looks as though he could see some involvement in drills Thursday.")
        self.assertEqual(pd_.practice_mentions(note, date(2026, 11, 5)), [(date(2026, 11, 4), "DNP")])

    def test_a_guess_about_a_coming_practice_is_skipped(self):
        self.assertEqual(pd_.practice_mentions("Robinson is expected to be limited at Thursday's practice.", date(2026, 11, 4)), [])
        self.assertEqual(pd_.practice_mentions("Robinson could practice fully Friday.", date(2026, 11, 5)), [])

    def test_game_status_from_the_note(self):
        self.assertEqual(pd_.game_status_mention("He practiced fully Friday and is listed as questionable for Sunday."), "Questionable")
        self.assertEqual(pd_.game_status_mention("Robinson has been ruled out for Sunday's game against the Saints."), "Out")
        self.assertIsNone(pd_.game_status_mention("He could be ruled out if he doesn't practice Friday."))
        self.assertIsNone(pd_.game_status_mention("He is questionable to return to Sunday's game."))

    def test_friday_game_status_rides_on_fridays_line(self):
        cw = Crosswalk.from_csv(CROSSWALK_CSV)
        payload = {"injuries": [{"abbreviation": "ATL", "injuries": [{
            "athlete": {"id": "4430807", "displayName": "Bijan Robinson", "position": {"abbreviation": "RB"}, "team": {"abbreviation": "ATL"}},
            "date": "2026-11-06T21:40Z", "status": "Questionable", "details": {"type": "Ankle"},
            "shortComment": "Robinson (ankle) was limited at Friday's practice and is listed as questionable for Sunday."}]}]}
        rows = pd_.from_espn(payload, cw, SCHEDULE, 2026)
        self.assertEqual([(r["day"], r["practice_status"], r["game_status"]) for r in rows], [("Fri", "Limited", "Questionable")])

    def test_saturday_ruling_counts_for_fridays_report(self):
        cw = Crosswalk.from_csv(CROSSWALK_CSV)
        payload = {"injuries": [{"abbreviation": "ATL", "injuries": [{
            "athlete": {"id": "4430807", "displayName": "Bijan Robinson", "position": {"abbreviation": "RB"}, "team": {"abbreviation": "ATL"}},
            "date": "2026-11-07T17:00Z", "status": "Out", "details": {"type": "Ankle"},
            "shortComment": "Robinson (ankle) has been ruled out for Sunday's game."}]}]}
        rows = pd_.from_espn(payload, cw, SCHEDULE, 2026)
        self.assertEqual([(r["practice_date"], r["practice_status"], r["game_status"]) for r in rows], [("2026-11-06", None, "Out")])


class ReaderTests(unittest.TestCase):
    def test_game_status_only_line_adds_no_practice_day(self):
        rows = [
            {"practice_date": "2026-11-04", "day": "Wed", "source": "nflverse", "practice_status": "DNP"},
            {"practice_date": "2026-11-05", "day": "Thu", "source": "nflverse", "practice_status": "Limited"},
            {"practice_date": "2026-11-06", "day": "Fri", "source": "espn_news", "practice_status": None, "game_status": "Questionable"},
        ]
        days = io.practice_week(rows)
        self.assertEqual(io.practice_trend(days), "Wed DNP -> Thu Limited")
        self.assertEqual(io.latest_game_status(days), "Questionable")
        from api.player import health
        h = health({"injury_status": "Questionable"}, None, [], [], 2026, None, [dict(r, season=2026, week=9) for r in rows])
        self.assertEqual(h["this_week"]["days"], [{"day": "Wed", "practice": "DNP"}, {"day": "Thu", "practice": "Limited"}])
        self.assertEqual(h["this_week"]["game_status"], "Questionable")

    def test_official_line_wins_and_days_are_ordered(self):
        rows = [
            {"practice_date": "2026-11-05", "day": "Thu", "source": "espn_news", "practice_status": "Limited"},
            {"practice_date": "2026-11-04", "day": "Wed", "source": "espn_news", "practice_status": "Limited"},
            {"practice_date": "2026-11-04", "day": "Wed", "source": "nflverse", "practice_status": "DNP"},
        ]
        days = io.practice_week(rows)
        self.assertEqual([(d["day"], d["practice"], d["source"]) for d in days], [("Wed", "DNP", "official report"), ("Thu", "Limited", "ESPN news")])
        self.assertEqual(io.practice_trend(days), "Wed DNP -> Thu Limited")

    def test_player_page_health_uses_this_weeks_days(self):
        from api.player import health
        weekly = [{"season": 2026, "week": 7, "practice_status": "Did Not Participate In Practice", "report_status": "Out", "report_injury": "Achilles"}]
        days = [{"season": 2026, "week": 9, "practice_date": "2026-11-04", "day": "Wed", "source": "nflverse", "practice_status": "Limited"}]
        h = health({"injury_status": "Questionable"}, None, weekly, [], 2026, None, days)
        self.assertEqual(h["this_week"]["week"], 9)
        self.assertEqual(h["this_week"]["practice"], "Limited practice")
        self.assertEqual(h["this_week"]["days"], [{"day": "Wed", "practice": "Limited"}])
        self.assertIsNone(h["this_week"]["game_status"])  # last week's Out doesn't carry over


class NewsSummaryTests(unittest.TestCase):
    def test_newest_headline_leads(self):
        from api.player_cards import news_summary
        got = news_summary({"short_comment": "Reid said Walker will practice Tuesday.", "long_comment": "Walker was a non-participant Monday.",
                            "reported_at": "2026-11-24T18:03:00+00:00"})
        self.assertEqual(got["text"], "Reid said Walker will practice Tuesday.\n\nWalker was a non-participant Monday.")
        self.assertEqual(news_summary({"long_comment": "Only the long one."})["text"], "Only the long one.")
        self.assertEqual(news_summary({"short_comment": "Same.", "long_comment": "Same. And more."})["text"], "Same. And more.")


class FrequentCheckTests(unittest.TestCase):
    def test_skips_only_when_neither_report_changed(self):
        import os
        import tempfile
        from unittest import mock
        from pipeline.nfl_data import run

        state = os.path.join(tempfile.mkdtemp(), "state")
        espn = {"injuries": [{"abbreviation": "ATL", "injuries": []}], "timestamp": "a"}
        built = {"Last-Modified": "Fri, 20 Nov 2026 16:04:51 GMT"}
        with mock.patch.object(run, "fetch_json", return_value=espn), mock.patch.object(run, "fetch_text_meta", return_value=("x", built)):
            unchanged, fp = run.practice_sources_unchanged(state, 2026)
            self.assertFalse(unchanged)  # first check always runs
            with open(state, "w") as fh:
                fh.write(fp)
            self.assertTrue(run.practice_sources_unchanged(state, 2026)[0])
            espn["timestamp"] = "b"  # only the feed's clock moved
            self.assertTrue(run.practice_sources_unchanged(state, 2026)[0])
        with mock.patch.object(run, "fetch_json", return_value=espn), \
                mock.patch.object(run, "fetch_text_meta", return_value=("x", {"Last-Modified": "Fri, 20 Nov 2026 22:04:51 GMT"})):
            self.assertFalse(run.practice_sources_unchanged(state, 2026)[0])  # official file rebuilt


if __name__ == "__main__":
    unittest.main()
