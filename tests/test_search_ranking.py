import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.command_manager import CommandManager
from src.command_trie import CommandTrie, WordStartIndex, word_start_offsets
from src.settings import Settings


def _commands_toml(entries: list[tuple[str, list[str], str]]) -> str:
    blocks = []
    for name, aliases, location in entries:
        alias_list = ", ".join(f"'{alias}'" for alias in aliases)
        blocks.append(f"[[command]]\nname = '{name}'\naliases = [{alias_list}]\nlocation = '{location}'\ndescription = ''\ntype = 'url'\n")
    return "\n".join(blocks)


class _ManagerTest(unittest.TestCase):
    def manager(self, entries, **search) -> CommandManager:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "commands.toml"
        path.write_text(_commands_toml(entries), encoding="utf-8")
        settings = Settings()
        for key, value in search.items():
            setattr(settings.search, key, value)
        return CommandManager(path, settings)

    @staticmethod
    def names(results) -> list[str]:
        return [result["name"] for result in results]


class SortBeforeCutTests(_ManagerTest):
    def test_the_most_used_match_is_never_cut_off(self):
        entries = [(f"Site {index:02}", [], f"https://s{index}.example.com/") for index in range(12)]
        manager = self.manager(entries, max_results=3, sort_results="popularity", match_word_starts=False)
        manager.run_counts = {}
        manager.commands["Site 11"]["times_executed"] = 50
        manager.commands["Site 07"]["times_executed"] = 9
        self.assertEqual(self.names(manager.find_matching_commands("si")), ["Site 11", "Site 07", "Site 00"])

    def test_an_exact_alias_still_comes_first(self):
        manager = self.manager(
            [("Stack", [], "https://a.example.com/"), ("Spotify", ["st"], "https://b.example.com/")],
            match_word_starts=False,
        )
        self.assertEqual(self.names(manager.find_matching_commands("st")), ["Spotify", "Stack"])


class SiteSearchPrecedenceTests(_ManagerTest):
    def setUp(self):
        self.m = self.manager(
            [
                ("Google", ["g"], "https://www.google.com/search?q={query}"),
                ("Google Maps", [], "https://maps.google.com/"),
                ("Jira", [], "https://jira.example.com/browse/{query}"),
            ]
        )

    def test_a_command_that_carries_on_with_the_text_leads(self):
        for text in ("google ma", "google maps", "Google M"):
            with self.subTest(text=text):
                results = self.m.find_matching_commands(text)
                self.assertEqual(self.names(results)[:2], ["Google Maps", "Google"])
                self.assertIn("_query", results[1])
                self.assertEqual(self.m.completion_keyword(results[0]["name"], text), "Google Maps")

    def test_the_search_leads_when_nothing_else_starts_with_the_text(self):
        results = self.m.find_matching_commands("google cats")
        self.assertEqual(results[0]["name"], "Google")
        self.assertEqual(results[0]["_query"], "cats")

    def test_the_search_row_survives_the_cut(self):
        self.m.settings.search.max_results = 1
        results = self.m.find_matching_commands("google ma")
        self.assertEqual(self.names(results), ["Google"])
        self.assertEqual(results[0]["_query"], "ma")


class WordStartTests(_ManagerTest):
    ENTRIES = [
        ("Visual Studio Code", ["vsc"], "https://code.example.com/"),
        ("Microsoft Word", [], "https://word.example.com/"),
        ("Codecademy", [], "https://cc.example.com/"),
        ("WordPad", [], "https://wp.example.com/"),
        ("OneNote", [], "https://on.example.com/"),
        ("Foxit Reader", [], "https://fr.example.com/"),
        ("Adobe Reader", [], "https://ar.example.com/"),
    ]

    def test_later_words_match_after_every_prefix_match(self):
        manager = self.manager(self.ENTRIES)
        self.assertEqual(self.names(manager.find_matching_commands("code")), ["Codecademy", "Visual Studio Code"])
        self.assertEqual(self.names(manager.find_matching_commands("word")), ["WordPad", "Microsoft Word"])
        self.assertEqual(self.names(manager.find_matching_commands("studio c")), ["Visual Studio Code"])
        self.assertEqual(self.names(manager.find_matching_commands("note")), ["OneNote"], "camelCase starts a word")
        self.assertEqual(self.names(manager.find_matching_commands("reader")), ["Adobe Reader", "Foxit Reader"])

    def test_word_matches_never_complete_the_box(self):
        manager = self.manager(self.ENTRIES)
        self.assertIsNone(manager.completion_keyword("Visual Studio Code", "code"))

    def test_the_setting_turns_it_off(self):
        manager = self.manager(self.ENTRIES, match_word_starts=False)
        self.assertEqual(self.names(manager.find_matching_commands("code")), ["Codecademy"])

    def test_word_matches_follow_the_sort_order(self):
        manager = self.manager(self.ENTRIES, sort_results="popularity")
        manager.commands["Foxit Reader"]["times_executed"] = 3
        self.assertEqual(self.names(manager.find_matching_commands("reader")), ["Foxit Reader", "Adobe Reader"])


class TrieTests(unittest.TestCase):
    def test_unlimited_prefix_search(self):
        trie = CommandTrie()
        for index in range(30):
            trie.insert(f"a{index}", f"id{index}")
        self.assertEqual(len(trie.search_prefix("a", max_results=None)), 30)
        self.assertEqual(len(trie.search_prefix("a", max_results=5)), 5)

    def test_word_start_offsets(self):
        self.assertEqual(word_start_offsets("Visual Studio Code"), [7, 14])
        self.assertEqual(word_start_offsets("my-tool_v2.app"), [3, 8, 11])
        self.assertEqual(word_start_offsets("PowerShell"), [5])
        self.assertEqual(word_start_offsets("Word"), [])

    def test_the_word_index_keeps_every_command_sharing_a_word(self):
        index = WordStartIndex()
        index.build([("Adobe Reader", "a"), ("Foxit Reader", "b"), ("Readme", "c")])
        self.assertEqual(sorted(index.search_prefix("rea")), ["a", "b"])
        self.assertEqual(index.search_prefix(""), [])


if __name__ == "__main__":
    unittest.main()
