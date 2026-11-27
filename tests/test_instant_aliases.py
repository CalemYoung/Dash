import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

app = QApplication.instance() or QApplication(sys.argv)

from src.command_editor import CommandEditorPanel  # noqa: E402
from src.command_manager import CommandManager  # noqa: E402
from src.icon_manager import IconManager  # noqa: E402
from src.launcher_gui import INSTANT_SWALLOW_MS, MainWindow  # noqa: E402
from src.instant_aliases import check_instant_aliases  # noqa: E402
from src.settings import Settings  # noqa: E402

COMMANDS = """[[command]]
name = "GitHub"
aliases = ["gh", "git"]
instant_aliases = ["gh"]
location = 'https://github.com/'
description = ""
type = "url"

[[command]]
name = "Spotify"
aliases = ["sp"]
location = '{spotify}'
description = ""
type = "file"

[[command]]
name = "Jira"
aliases = ["j"]
location = 'https://jira.example.com/browse/{query}'
description = ""
type = "url"
"""


def lower(text: str) -> str:
    return text.lower()


class CheckInstantAliasesTests(unittest.TestCase):
    def check(self, commands, match_word_starts=False):
        return check_instant_aliases(commands, lower, match_word_starts)

    def test_an_alias_nothing_else_starts_with_is_accepted(self):
        accepted, refused = self.check(
            [
                {"name": "GitHub", "aliases": ["gh"], "instant_aliases": ["gh"]},
                {"name": "Gmail", "aliases": ["mail"]},
            ]
        )
        self.assertEqual(accepted, {"gh": "GitHub"})
        self.assertEqual(refused, [])

    def test_the_commands_own_name_may_start_with_it(self):
        accepted, _ = self.check([{"name": "Spotify", "aliases": ["s"], "instant_aliases": ["S"]}])
        self.assertEqual(accepted, {"s": "Spotify"})

    def test_another_commands_keyword_starting_with_it_refuses_it(self):
        accepted, refused = self.check(
            [
                {"name": "GitHub", "aliases": ["gh"], "instant_aliases": ["gh"]},
                {"name": "Ghost", "aliases": []},
            ]
        )
        self.assertEqual(accepted, {})
        self.assertEqual((refused[0].kind, refused[0].keyword, refused[0].keyword_owner), ("prefix", "Ghost", "Ghost"))

    def test_a_search_site_cannot_open_instantly(self):
        _, refused = self.check(
            [{"name": "Jira", "aliases": ["j"], "instant_aliases": ["j"], "type": "url", "location": "https://jira/{query}"}]
        )
        self.assertEqual(refused[0].kind, "search")

    def test_an_instant_alias_must_be_an_alias(self):
        _, refused = self.check([{"name": "GitHub", "aliases": [], "instant_aliases": ["gh"]}])
        self.assertEqual(refused[0].kind, "missing")

    def test_later_words_count_when_word_starts_match(self):
        commands = [
            {"name": "Code", "aliases": ["co"], "instant_aliases": ["co"]},
            {"name": "Visual Studio Codex", "aliases": []},
        ]
        self.assertEqual(self.check(commands)[0], {"co": "Code"})
        accepted, refused = self.check(commands, match_word_starts=True)
        self.assertEqual(accepted, {})
        self.assertEqual(refused[0].kind, "word")


class CommandManagerInstantTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "commands.toml"
        spotify = self.root / "Spotify.exe"
        spotify.write_bytes(b"")
        self.commands_text = COMMANDS.replace("{spotify}", str(spotify))
        self.path.write_text(self.commands_text, encoding="utf-8")
        self.manager = CommandManager(self.path, Settings())
        self.notes = []
        self.manager.on_instant_demoted = self.notes.extend

    def tearDown(self):
        self._tmp.cleanup()

    def program(self, name, aliases=()):
        exe = self.root / f"{name}.exe"
        exe.write_bytes(b"")
        return {"name": name, "aliases": list(aliases), "location": str(exe), "type": "file"}

    def test_every_alias_nothing_else_starts_with_opens_instantly(self):
        self.assertTrue(Settings().search.instant_aliases, "on by default")
        self.assertEqual(self.manager.instant_command("gh"), "GitHub")
        self.assertEqual(self.manager.instant_command("git"), "GitHub", "its own name starting the same way is fine")
        self.assertEqual(self.manager.instant_command("SP"), "Spotify")
        self.assertIsNone(self.manager.instant_command("s"))

    def test_a_search_site_alias_needs_enter(self):
        self.assertIsNone(self.manager.instant_command("j"))

    def test_with_the_setting_off_every_alias_needs_enter(self):
        settings = Settings()
        settings.search.instant_aliases = False
        manager = CommandManager(self.path, settings)
        self.assertIsNone(manager.instant_command("gh"))
        self.assertIsNone(manager.instant_command("sp"))

    def test_nothing_is_stored_and_old_flags_are_dropped_on_save(self):
        spotify = self.manager.commands["Spotify"]
        self.manager.save_command({**spotify, "aliases": ["sp", "music"]}, "Spotify")
        self.assertNotIn("instant_aliases", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.manager.instant_command("music"), "Spotify")
        self.assertEqual(self.manager.instant_command("gh"), "GitHub")
        self.assertEqual(self.notes, [])

    def test_a_new_command_starting_with_an_alias_makes_it_need_enter(self):
        self.manager.save_command(self.program("Ghost"))
        self.assertIn("Ghost", self.manager.commands)
        self.assertIsNone(self.manager.instant_command("gh"))
        self.assertEqual(self.manager.commands["GitHub"]["aliases"], ["gh", "git"], "the alias itself stays")
        self.assertEqual(self.notes, [("gh", "GitHub", "Ghost")])
        self.assertIsNone(CommandManager(self.path, Settings()).instant_command("gh"))

    def test_a_new_alias_that_starts_another_alias_needs_enter_and_changes_no_one(self):
        self.manager.save_command(self.program("Steam", ["s"]))
        self.assertIsNone(self.manager.instant_command("s"), "'sp' starts with it")
        self.assertEqual(self.manager.instant_command("sp"), "Spotify")
        self.assertEqual(self.notes, [])

    def test_removing_the_clash_makes_the_alias_instant_again(self):
        self.manager.save_command(self.program("Ghost"))
        self.manager.delete_command("Ghost")
        self.assertEqual(self.manager.instant_command("gh"), "GitHub")

    def test_added_programs_open_instantly_where_nothing_clashes(self):
        self.manager.import_program_commands([self.program("Obsidian", ["obs"]), self.program("Gimp", ["g"])])
        self.assertEqual(self.manager.instant_command("obs"), "Obsidian")
        self.assertIsNone(self.manager.instant_command("g"), "'gh' and 'git' start with it")

    def test_adding_a_program_that_starts_with_an_alias_says_so(self):
        summary = self.manager.import_program_commands([self.program("Ghostwriter")])
        self.assertEqual(summary["imported"], ["Ghostwriter"])
        self.assertIsNone(self.manager.instant_command("gh"))
        self.assertEqual(self.notes, [("gh", "GitHub", "Ghostwriter")])


class _GuiFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        spotify = root / "Spotify.exe"
        spotify.write_bytes(b"")
        self.path = root / "commands.toml"
        self.path.write_text(
            COMMANDS.replace("{spotify}", str(spotify))
            + f"""
[[command]]
name = "Tool"
aliases = ["tl", "z"]
location = '{spotify}'
description = ""
""",
            encoding="utf-8",
        )
        worker = mock.patch.object(IconManager, "_start_download_worker")
        worker.start()
        self.addCleanup(worker.stop)
        self.settings = Settings()
        (root / "first_start_shown").write_text("shown", encoding="utf-8")
        (root / "first_tips_shown").write_text("shown", encoding="utf-8")
        self.manager = CommandManager(self.path, self.settings)


class LauncherInstantTests(_GuiFixture):
    def setUp(self):
        super().setUp()
        self.window = MainWindow(self.manager, self.settings, settings_path=Path(self._tmp.name) / "settings.toml")
        self.box = self.window.search_input_widget
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.show()

    def type(self, text):
        for character in text:
            QApplication.sendEvent(self.box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, character))

    def test_typing_an_instant_alias_opens_it_without_enter(self):
        with mock.patch.object(self.manager, "execute_command", return_value=None) as execute:
            self.type("tl")
            execute.assert_called_once_with(self.window, "Tool")
            self.assertTrue(self.box.isReadOnly(), "keys typed straight after are ignored")
            QTest.qWait(INSTANT_SWALLOW_MS + 100)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.box.isReadOnly())
        self.assertEqual(self.box.text(), "")

    def test_the_start_of_an_instant_alias_does_nothing(self):
        with mock.patch.object(self.manager, "execute_command", return_value=None) as execute:
            self.type("t")
            execute.assert_not_called()
        self.assertTrue(self.window.isVisible())

    def test_backspacing_onto_an_instant_alias_does_not_open_it(self):
        with mock.patch.object(self.manager, "execute_command", return_value=None) as execute:
            self.box.setText("tlx")
            self.window.is_deleting = False
            self.box.setFocus()
            self.box.setCursorPosition(3)
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
            self.window.eventFilter(self.box, event)
            QApplication.sendEvent(self.box, event)
            self.assertEqual(self.box.text()[:2], "tl")
            execute.assert_not_called()


class OnlyMatchTests(_GuiFixture):
    """The open_only_match setting: off by default; on, the one command left
    opens without Enter. GitHub (gh, git), Spotify (sp), Jira (a search
    site, j) and Tool (tl, z)."""

    def setUp(self):
        super().setUp()
        self.settings.search.instant_aliases = False  # only this setting at work
        self.manager.reload_command_trie()
        self.window = MainWindow(self.manager, self.settings, settings_path=Path(self._tmp.name) / "settings.toml")
        self.window.fallback_index.set_candidates([])
        self.box = self.window.search_input_widget
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.show()

    def type(self, text):
        for character in text:
            QApplication.sendEvent(self.box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, character))

    def test_off_by_default(self):
        self.assertFalse(Settings().search.open_only_match)
        with mock.patch.object(self.manager, "execute_command", return_value=None) as execute:
            self.type("spo")
        execute.assert_not_called()

    def test_the_one_command_left_opens_without_enter(self):
        self.settings.search.open_only_match = True
        with mock.patch.object(self.manager, "execute_command", return_value=None) as execute:
            self.type("spo")
            execute.assert_called_once_with(self.window, "Spotify")
            QTest.qWait(INSTANT_SWALLOW_MS + 100)
        self.assertFalse(self.window.isVisible())

    def test_every_match_counts_not_just_those_shown(self):
        spotless = Path(self._tmp.name) / "Spotless.exe"
        spotless.write_bytes(b"")
        self.manager.save_command({"name": "Spotless", "aliases": [], "location": str(spotless), "type": "file"})
        self.settings.search.max_results = 1
        self.assertIsNone(self.manager.only_match("spo"), "Spotify and Spotless both match")
        self.assertEqual(self.manager.only_match("spoti"), "Spotify")

    def test_a_search_site_waits_for_the_search(self):
        self.assertIsNone(self.manager.only_match("ji"))
        self.assertIsNone(self.manager.only_match("j something"))

    def test_nothing_typed_or_nothing_matching_opens_nothing(self):
        self.assertIsNone(self.manager.only_match(""))
        self.assertIsNone(self.manager.only_match("qqq"))


class EditorInstantTests(_GuiFixture):
    def test_the_editor_has_no_per_alias_instant_switch(self):
        panel = CommandEditorPanel(self.manager.commands["Tool"], IconManager(self.settings), self.manager)
        self.assertEqual([label for label in panel.findChildren(QLabel) if label.objectName() == "AliasInstant"], [])
        self.assertNotIn("instant_aliases", panel._collect())


if __name__ == "__main__":
    unittest.main()
