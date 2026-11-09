import unittest

from api import profiles
from league_ai import owner_prefs


class ProfilesTest(unittest.TestCase):
    def test_abbr(self):
        self.assertEqual(profiles.abbr("Tampa Bucs"), "TB")
        self.assertEqual(profiles.abbr("Bills"), "BIL")
        self.assertEqual(profiles.abbr("The Big Red Machine Go"), "TBRM")
        self.assertEqual(profiles.abbr(""), "")

    def test_names_and_pictures_overlay(self):
        mine = {1: {"display_name": "Tom", "picture_url": "https://x.supabase.co/a.jpg"}, 3: {"picture_url": "https://x.supabase.co/c.jpg"}}
        self.assertEqual(profiles.names(mine, {1: "Tommy", 2: "Dylan"}), {1: "Tom", 2: "Dylan"})
        self.assertEqual(profiles.pictures(mine, {1: "sleeper1", 2: "sleeper2"}),
                         {1: "https://x.supabase.co/a.jpg", 2: "sleeper2", 3: "https://x.supabase.co/c.jpg"})

    def test_overlay_team_keeps_defaults(self):
        row = profiles.overlay_team({"owner_name": "Tommy", "team_name": "Old"}, {"team_name": "Legacy Kings"})
        self.assertEqual((row["owner_name"], row["team_name"], row["team_abbr"]), ("Tommy", "Legacy Kings", "LK"))
        self.assertEqual(profiles.overlay_team({"owner_name": "A"}, None), {"owner_name": "A"})


class OwnerPrefsTest(unittest.TestCase):
    def test_note(self):
        self.assertEqual(owner_prefs.note({"answer_length": "short", "team_plan": "auto"}), "")
        text = owner_prefs.with_prefs("## This week", {"answer_length": "detailed", "team_plan": "rebuild"})
        self.assertTrue(text.startswith("## This week\n\n## Owner preferences"))
        self.assertIn("rebuilding", text)
        self.assertEqual(owner_prefs.with_prefs(None, {}), None)


if __name__ == "__main__":
    unittest.main()
