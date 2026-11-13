import unittest

from league_ai.prompt import SYSTEM_PROMPT
from league_ai.routing import effort_for, is_trade_question, topic
from league_ai.runtime import NOT_A_TRADE_NOTE, with_question_focus

NOT_TRADE = [
    "how does Jefferson look with Kirk now",
    "How does Jefferson look now that Kirk got traded to Minnesota?",
    "After the trade, how does Kirk fit with JJ and Addison?",
    "What's Jefferson's ceiling with Cousins?",
    "Who should I start this week?",
    "What is my record?",
]
TRADE = [
    "Find me a trade that helps now",
    "Should I accept Dylan's trade for my 2027 1st?",
    "should I trade Jefferson",
    "Should I sell high on Jefferson?",
    "is this trade fair",
    "What could I get for Jefferson?",
    "Trade Jefferson to Nando for his 1st?",
    "Waddle for Devonta smith?",
    "Jefferson trade value",
    "buy low on Jefferson?",
]


class QuestionFocusTests(unittest.TestCase):
    def test_trade_news_is_not_a_trade_question(self):
        for q in NOT_TRADE:
            self.assertFalse(is_trade_question(q), q)
        for q in TRADE:
            self.assertTrue(is_trade_question(q), q)
            self.assertEqual(topic(q), "trade", q)

    def test_outlook_question_is_not_tagged_trade(self):
        self.assertEqual(topic("How does Jefferson look now that Kirk got traded?"), "other")
        self.assertEqual(effort_for("Should I accept Dylan's trade for my 2027 1st?"), "high")

    def test_non_trade_question_gets_the_focus_note(self):
        self.assertIn(NOT_A_TRADE_NOTE, with_question_focus("This week: Vikings @ GB", "how does Jefferson look with Kirk now"))
        self.assertEqual(with_question_focus(None, "how does Jefferson look with Kirk now"), NOT_A_TRADE_NOTE)
        self.assertEqual(with_question_focus("This week", "should I trade Jefferson"), "This week")
        self.assertIsNone(with_question_focus(None, "Find me a trade that helps now"))

    def test_prompt_covers_outlook_questions(self):
        self.assertIn("Outlook and what-if questions", SYSTEM_PROMPT)
        self.assertIn("hold/sell/buy call", SYSTEM_PROMPT)

    def test_prompt_uses_tables_for_comparisons(self):
        self.assertIn("compact Markdown table", SYSTEM_PROMPT)
        self.assertNotIn("and no table", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
