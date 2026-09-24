import json
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.command_manager import CommandManager
from src.settings import Settings


class CommandLoadingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "commands.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_unreadable_file_is_set_aside_and_dash_starts_without_it(self):
        self.path.write_text("[[command]]\nname = 'Broken\n", encoding="utf-8")
        manager = CommandManager(self.path, Settings())
        self.assertEqual([c["name"] for c in manager.commands.values()], ["Dash Settings"])
        self.assertFalse(self.path.exists())
        self.assertTrue((self.root / "commands.toml.bad").exists())
        self.assertEqual(len(manager.load_warnings), 1)
        self.assertIn("commands.toml.bad", manager.load_warnings[0])
        # A new command can be saved straight away.
        manager.save_command({"name": "Site", "aliases": [], "location": "https://example.com/", "type": "url"})
        self.assertIn("Site", manager.commands)

    def test_text_that_is_not_utf8_is_set_aside_too(self):
        self.path.write_bytes(b"[[command]]\nname = '\xff\xfe'\n")
        manager = CommandManager(self.path, Settings())
        self.assertTrue(manager.load_warnings)
        self.assertTrue((self.root / "commands.toml.bad").exists())

    def test_malformed_entries_are_left_out_and_counted(self):
        self.path.write_text(
            """[[command]]
aliases = []
location = 'https://nameless.example.com/'

[[command]]
name = "Numbers"
location = 42

[[command]]
name = "Listy"
aliases = "not a list"
location = 'https://listy.example.com/'

[[command]]
name = "Good"
aliases = ["g"]
location = 'https://good.example.com/'
""",
            encoding="utf-8",
        )
        manager = CommandManager(self.path, Settings())
        self.assertIn("Good", manager.commands)
        self.assertNotIn("Numbers", manager.commands)
        self.assertNotIn("Listy", manager.commands)
        self.assertEqual(len(manager.load_warnings), 1)
        self.assertIn("Left out 3 commands", manager.load_warnings[0])
        self.assertIn("'Numbers'", manager.load_warnings[0])
        # The file as it was is kept before a save drops the bad entries.
        manager.save_command({"name": "Other", "aliases": [], "location": "https://other.example.com/", "type": "url"})
        self.assertIn("Numbers", (self.root / "commands.toml.bad").read_text(encoding="utf-8"))
        stored = tomllib.loads(self.path.read_text(encoding="utf-8"))["command"]
        self.assertEqual([entry["name"] for entry in stored], ["Good", "Other"])

    def test_clashing_aliases_are_dropped_from_the_later_command(self):
        self.path.write_text(
            """[[command]]
name = "First"
aliases = ["f", "shared"]
location = 'https://first.example.com/'

[[command]]
name = "Second"
aliases = ["SHARED", "s2", "settings"]
location = 'https://second.example.com/'

[[command]]
name = "first"
aliases = []
location = 'https://dupe.example.com/'
""",
            encoding="utf-8",
        )
        manager = CommandManager(self.path, Settings())
        self.assertEqual(manager.commands["First"]["aliases"], ["f", "shared"])
        self.assertEqual(manager.commands["Second"]["aliases"], ["s2"])
        self.assertNotIn("first", manager.commands)
        self.assertEqual(manager.keyword_index["shared"], "First")
        warnings = " ".join(manager.load_warnings)
        self.assertIn("'SHARED' of 'Second'", warnings)
        self.assertIn("'settings' of 'Second'", warnings)
        self.assertIn("second command named 'first'", warnings)
        # Reloading does not raise and does not repeat the notes.
        manager.reload_command_trie()
        self.assertEqual(len(manager.load_warnings), 3)

    def test_saves_are_atomic(self):
        self.path.write_text("", encoding="utf-8")
        manager = CommandManager(self.path, Settings())
        with mock.patch("src.command_manager.atomic_write_text") as write:
            manager._write_raw_commands([])
            manager._save_run_counts({"A": 1})
            manager.export_commands([], self.root / "export.toml")
        self.assertEqual([call.args[0] for call in write.call_args_list], [self.path, manager.run_counts_path, self.root / "export.toml"])

    def test_unreadable_run_counts_are_set_aside(self):
        self.path.write_text("", encoding="utf-8")
        (self.root / "run_counts.json").write_text("{not json", encoding="utf-8")
        manager = CommandManager(self.path, Settings())
        self.assertEqual(manager.run_counts, {})
        self.assertTrue((self.root / "run_counts.json.bad").exists())
        manager.increment_run_count("Dash Settings")
        manager.run_counts["X"] = 2
        manager._save_run_counts()
        self.assertEqual(json.loads((self.root / "run_counts.json").read_text(encoding="utf-8")), {"X": 2})


if __name__ == "__main__":
    unittest.main()


class UnreadableFileSaveGuardTests(unittest.TestCase):
    def test_a_file_that_could_not_be_set_aside_is_never_saved_over(self):
        from src.command_manager import CommandsFileUnreadableError

        with TemporaryDirectory() as folder:
            path = Path(folder) / "commands.toml"
            path.write_text("[[command]\nname = 'Kept'\n", encoding="utf-8")
            with mock.patch("src.command_manager.quarantine_file", return_value=None):
                manager = CommandManager(path, Settings())
                with self.assertRaises(CommandsFileUnreadableError):
                    manager.save_command({"name": "New", "location": "https://example.com", "type": "url", "aliases": []})
            self.assertIn("name = 'Kept'", path.read_text(encoding="utf-8"))


class RuntimeDamageTests(unittest.TestCase):
    """A file broken by a hand edit while Dash runs must never cost commands."""

    def setUp(self):
        self._folder = TemporaryDirectory()
        self.path = Path(self._folder.name) / "commands.toml"
        self.path.write_text(
            "[[command]]\nname = 'Alpha'\nlocation = 'https://a.example'\ntype = 'url'\n"
            "[[command]]\nname = 'Beta'\nlocation = 'https://b.example'\ntype = 'url'\n",
            encoding="utf-8",
        )
        self.manager = CommandManager(self.path, Settings())
        self.broken = self.path.read_text(encoding="utf-8") + "[[command]\nname = 'Oops'\n"
        self.path.write_text(self.broken, encoding="utf-8")

    def tearDown(self):
        self._folder.cleanup()

    def test_the_file_stays_where_it_is_and_the_loaded_commands_stay(self):
        self.manager.reload_command_trie()
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.broken)
        self.assertFalse(list(self.path.parent.glob("*.bad*")))
        self.assertIn("Alpha", self.manager.commands)
        self.assertTrue(self.manager.has_user_commands())
        self.assertTrue(self.manager.load_warnings)

    def test_saving_and_deleting_are_refused_until_the_file_is_fixed(self):
        from src.command_manager import CommandsFileUnreadableError

        with self.assertRaises(CommandsFileUnreadableError):
            self.manager.save_command({"name": "Gamma", "location": "https://c.example", "type": "url", "aliases": []})
        with self.assertRaises(CommandsFileUnreadableError):
            self.manager.delete_command("Alpha")
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.broken)

    def test_read_only_uses_fall_back_to_the_loaded_commands(self):
        self.assertIsNone(self.manager.validate_command({"name": "Gamma", "location": "https://c.example", "type": "url", "aliases": []}))
        self.assertIsNotNone(self.manager.validate_command({"name": "Alpha", "location": "https://c.example", "type": "url", "aliases": []}))
