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


class ReaderTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
