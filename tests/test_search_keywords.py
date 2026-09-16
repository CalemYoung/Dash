import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.browsers import fill_query, is_search_link
from src.command_manager import CommandManager
from src.settings import Settings

JIRA = "https://jira.example.com/browse/{query}"
GOOGLE = "https://www.google.com/search?q={query}"


class FillQueryTests(unittest.TestCase):
    def test_query_text_is_encoded_for_where_it_goes(self):
        self.assertEqual(fill_query(GOOGLE, "cable sleeve & size"), "https://www.google.com/search?q=cable+sleeve+%26+size")
        self.assertEqual(fill_query(JIRA, "EC-123"), "https://jira.example.com/browse/EC-123")
        self.assertEqual(fill_query("https://wiki.example.com/{query}", "a b/c"), "https://wiki.example.com/a%20b%2Fc")

    def test_an_empty_search_opens_the_site(self):
        self.assertEqual(fill_query(GOOGLE, "   "), "https://www.google.com/")
        self.assertEqual(fill_query("jira.example.com/browse/{query}", ""), "https://jira.example.com/")

    def test_a_plain_address_is_left_alone(self):
        self.assertFalse(is_search_link("https://example.com/"))
        self.assertEqual(fill_query("https://example.com/", "ignored"), "https://example.com/")


class SearchKeywordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        commands_path = Path(self._tmp.name) / "commands.toml"
        commands_path.write_text(
            f"""[[command]]
name = "Jira"
aliases = ["j"]
location = '{JIRA}'
description = "Search Jira"
type = "url"

[[command]]
name = "google search"
aliases = []
location = '{GOOGLE}'
description = ""
type = "url"

[[command]]
name = "Google"
aliases = []
location = 'https://www.google.com/maps?q={{query}}'
description = ""
type = "url"

[[command]]
name = "Remote Desktop"
aliases = ["remote"]
location = 'https://remote.example.com/'
description = ""
type = "url"
""",
            encoding="utf-8",
        )
        self.manager = CommandManager(commands_path, Settings())

    def tearDown(self):
        self._tmp.cleanup()

    def test_keyword_space_text_is_a_search(self):
        command, query = self.manager.match_search_keyword("jira EC-123")
        self.assertEqual((command["name"], query), ("Jira", "EC-123"))
        command, query = self.manager.match_search_keyword("J ec 12")
        self.assertEqual((command["name"], query), ("Jira", "ec 12"))

    def test_the_longest_keyword_wins(self):
        command, query = self.manager.match_search_keyword("google search cats")
        self.assertEqual((command["name"], query), ("google search", "cats"))
        command, query = self.manager.match_search_keyword("google cats")
        self.assertEqual((command["name"], query), ("Google", "cats"))

    def test_a_keyword_and_a_space_alone_searches_for_nothing_yet(self):
        command, query = self.manager.match_search_keyword("jira ")
        self.assertEqual((command["name"], query), ("Jira", ""))

    def test_other_text_is_not_a_search(self):
        self.assertIsNone(self.manager.match_search_keyword("jira"))
        self.assertIsNone(self.manager.match_search_keyword("remote desktop"))
        self.assertIsNone(self.manager.match_search_keyword("remote x"), "a plain website is not a keyword")
        self.assertIsNone(self.manager.match_search_keyword("nothing here"))

    def test_the_search_leads_the_results(self):
        results = self.manager.find_matching_commands("jira EC-123")
        self.assertEqual(results[0]["name"], "Jira")
        self.assertEqual(results[0]["_query"], "EC-123")
        self.assertNotIn("_query", self.manager.commands["Jira"], "the stored command is not changed")
        self.assertEqual([r["name"] for r in self.manager.find_matching_commands("remote d")], ["Remote Desktop"])

    def test_running_a_search_fills_in_the_query(self):
        with mock.patch.object(self.manager, "_open_url") as open_url:
            self.assertIsNone(self.manager.execute_command(None, "Jira", query="EC 12"))
            self.assertIsNone(self.manager.execute_command(None, "Jira"))
        self.assertEqual(open_url.call_args_list[0].args[0], "https://jira.example.com/browse/EC%2012")
        self.assertEqual(open_url.call_args_list[1].args[0], "https://jira.example.com/")


class WebSearchSettingTests(unittest.TestCase):
    def test_web_search_defaults_to_google_and_saves(self):
        settings = Settings()
        self.assertTrue(is_search_link(settings.general.web_search))
        self.assertTrue(settings.general.switch_to_open_apps)
        self.assertFalse(settings.general.web_search_enabled, "the web search offer is on by default")
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "settings.toml"
            settings.general.web_search = ""
            settings.general.switch_to_open_apps = False
            settings.general.web_search_enabled = True
            settings.save(path)
            loaded = Settings.load(path)
        self.assertEqual(loaded.general.web_search, "")
        self.assertFalse(loaded.general.switch_to_open_apps)
        self.assertTrue(loaded.general.web_search_enabled)


if __name__ == "__main__":
    unittest.main()
