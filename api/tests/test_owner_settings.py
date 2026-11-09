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


class InviteAndToolErrorTest(unittest.TestCase):
    def test_invite_row_stores_only_the_hash(self):
        from unittest import mock
        from api import owner_settings
        client = mock.MagicMock()
        out = owner_settings.create_invite(client, user_id="u", league_id="L", team_id="T", email=" Dylan@Example.com ", role="co_owner")
        row = client.table.return_value.insert.call_args[0][0]
        self.assertEqual((row["email"], row["role"], row["token"], row["status"]), ("dylan@example.com", "co_owner", None, "pending"))
        self.assertIn("invite_token=", out["link"])
        with self.assertRaises(ValueError):
            owner_settings.create_invite(client, user_id="u", league_id="L", team_id="T", email="nope", role="owner")

    def test_commissioner_errors_read_plainly(self):
        from api import commissioner
        self.assertEqual(commissioner.tool_error(Exception("P0001 contract_season_missing")),
                         "That season isn't set up in the league yet, so the contract can't run that long.")
        self.assertEqual(commissioner.tool_error(Exception("Commissioner authority required")), "Only the commissioner can do that.")


class TeamLabelsTest(unittest.TestCase):
    def test_label_teams_walks_the_response(self):
        out = profiles.label_teams({"games": [{"teams": [{"roster_id": 1, "name": "Tommy"}, {"roster_id": 2, "name": "Dylan"}]}],
                                    "me": {"roster_id": 1, "name": "Tommy", "players": [{"name": "Josh Allen"}]}},
                                   {1: {"team_name": "Legacy Kings", "team_abbr": "LK"}})
        self.assertEqual(out["games"][0]["teams"][0]["team_name"], "Legacy Kings")
        self.assertNotIn("team_name", out["games"][0]["teams"][1])
        self.assertEqual(out["me"]["team_abbr"], "LK")
        self.assertNotIn("team_name", out["me"]["players"][0])
