import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src import launcher_gui  # noqa: E402
from src.command_manager import CommandManager  # noqa: E402
from src.fallback import FallbackIndex, command_location_keys, location_key  # noqa: E402
from src.icon_manager import IconManager  # noqa: E402
from src.launcher_gui import MainWindow  # noqa: E402
from src.settings import Settings  # noqa: E402


def candidates(root: Path) -> list[dict]:
    return [
        {"name": "Spotify", "aliases": ["sp"], "location": str(root / "Spotify.exe"), "description": "Opens Spotify", "type": "file"},
        {"name": "Steam", "aliases": [], "location": str(root / "Steam.exe"), "description": "Opens Steam", "type": "file"},
        {"name": "Visual Studio Code", "aliases": ["vsc"], "location": str(root / "Code.exe"), "description": "Opens Code", "type": "file"},
        {"name": "YouTube", "aliases": ["yt"], "location": "https://www.youtube.com/", "description": "youtube.com", "type": "url"},
        {"name": "Bluetooth", "aliases": [], "location": "ms-settings:bluetooth", "description": "Windows Settings", "type": "url"},
        {"name": "Tool", "aliases": [], "location": str(root / "tool.exe"), "description": "Opens Tool", "type": "file"},
    ]


class FallbackIndexTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("C:/Apps")
        self.index = FallbackIndex()
        self.index.set_candidates(candidates(self.root), now=100.0)

    def names(self, text, known=frozenset()):
        return [candidate["name"] for candidate in self.index.search(text, known)]

    def test_prefix_on_name_and_alias_then_later_words(self):
        self.assertEqual(self.names("s"), ["Spotify", "Steam", "Visual Studio Code"])
        self.assertEqual(self.names("vsc"), ["Visual Studio Code"])
        self.assertEqual(self.names("code"), ["Visual Studio Code"])
        self.assertEqual(self.names("blue"), ["Bluetooth"])

    def test_an_exact_alias_comes_first(self):
        self.index.set_candidates(
            [
                {"name": "Spark", "aliases": [], "location": "C:/Spark.exe"},
                {"name": "Spotify", "aliases": ["sp"], "location": "C:/Spotify.exe"},
            ]
        )
        self.assertEqual(self.names("sp"), ["Spotify", "Spark"])

    def test_whatever_is_already_a_command_is_left_out(self):
        known = {location_key(str(self.root / "spotify.EXE"))}
        self.assertEqual(self.names("st", known), ["Steam", "Visual Studio Code"])
        self.assertEqual(self.names("sp", known), [])
        known = {location_key("https://www.youtube.com")}
        self.assertEqual(self.names("yt", known), [])

    def test_nothing_typed_finds_nothing(self):
        self.assertEqual(self.names("   "), [])

    def test_staleness(self):
        self.assertTrue(FallbackIndex().is_stale())
        self.assertFalse(self.index.is_stale(now=100.0 + 60))
        self.assertTrue(self.index.is_stale(now=100.0 + 3600))

    def test_command_keys_include_the_program_a_shortcut_runs(self):
        keys = command_location_keys([{"location": "C:/Links/App.lnk", "process_path": "C:/Apps/App.exe"}])
        self.assertIn(location_key("c:/apps/app.exe"), keys)


class LauncherFallbackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        tool = self.root / "tool.exe"
        tool.write_bytes(b"")
        commands = self.root / "commands.toml"
        commands.write_text(
            f"""[[command]]
name = "Tool"
aliases = []
location = '{tool}'
description = ""
""",
            encoding="utf-8",
        )
        worker = mock.patch.object(IconManager, "_start_download_worker")
        worker.start()
        self.addCleanup(worker.stop)
        self.settings = Settings()
        self.settings.general.web_search_enabled = False
        (self.root / "first_start_shown").write_text("shown", encoding="utf-8")
        (self.root / "first_tips_shown").write_text("shown", encoding="utf-8")
        self.manager = CommandManager(commands, self.settings)
        self.window = MainWindow(self.manager, self.settings, settings_path=self.root / "settings.toml")
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.show()
        # A real error dialog is modal and would wait on whoever is at the screen.
        popup = mock.patch.object(self.window, "display_error_popup")
        self.error_popup = popup.start()
        self.addCleanup(popup.stop)
        self.found = candidates(self.root)
        for candidate in self.found:
            if candidate["type"] == "file":
                Path(candidate["location"]).write_bytes(b"")

    def loaded(self):
        self.window.fallback_index.set_candidates(self.found)

    def type(self, text):
        self.window.search_input_widget.setText(text)

    def rows(self):
        results = self.window.results_list_widget
        return [results.itemWidget(results.item(index)) for index in range(results.count())]

    def fallback_names(self):
        return [row.fallback_candidate["name"] for row in self.rows() if row.fallback_candidate is not None]

    def press(self, key):
        self.window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))

    def look(self, text):
        """Type a miss, then ↓ to look on this PC."""
        self.type(text)
        self.press(Qt.Key.Key_Down)

    def test_a_command_match_shows_no_fallback(self):
        self.loaded()
        self.type("to")
        self.assertEqual([row.command_name for row in self.rows()], ["Tool"])

    def test_a_miss_looks_at_once_but_shows_rows_only_on_down(self):
        self.loaded()
        self.type("st")
        self.assertEqual(self.fallback_names(), [], "rows stay hidden until ↓")
        self.assertIsNone(self.window.get_selected_row(), "Enter must not open anything")
        self.assertIn("Press ↓ to see 2 on this PC", self.rows()[0].description_label.full_text())
        self.press(Qt.Key.Key_Down)
        self.assertEqual(self.fallback_names(), ["Steam", "Visual Studio Code"])
        self.assertEqual(self.window.get_selected_row().fallback_candidate["name"], "Steam")
        self.assertIn("add one of these", self.rows()[0].description_label.full_text())

    def test_the_scan_starts_on_the_miss_itself(self):
        with mock.patch.object(launcher_gui.ProgramDiscoveryThread, "start") as start:
            self.type("ste")
        start.assert_called_once()
        self.assertEqual(self.fallback_names(), [])
        self.assertIn("Press ↓ to look on this PC", self.rows()[0].description_label.full_text())
        self.window._fallback_thread.candidates = self.found
        self.window._on_fallback_scan_finished()
        self.assertIn("Press ↓ to see 1 on this PC", self.rows()[0].description_label.full_text())
        self.assertEqual(self.fallback_names(), [])

    def test_nothing_on_this_pc_offers_no_down(self):
        self.loaded()
        self.type("qqq")
        self.assertNotIn("↓", self.rows()[0].description_label.full_text())

    def test_changing_the_text_puts_the_rows_away_until_down_again(self):
        self.loaded()
        self.look("ste")
        self.assertEqual(self.fallback_names(), ["Steam"])
        self.type("st")
        self.assertEqual(self.fallback_names(), [], "an edit hides them again")
        self.assertIn("Press ↓ to see 2 on this PC", self.rows()[0].description_label.full_text())
        self.type("ste")
        self.assertEqual(self.fallback_names(), [], "typing the same letters back does not bring them back")
        self.press(Qt.Key.Key_Down)
        self.assertEqual(self.fallback_names(), ["Steam"])

    def test_looking_and_finding_nothing_says_so(self):
        self.loaded()
        self.look("qqq")
        self.assertEqual(self.fallback_names(), [])
        self.assertIn("Nothing on this PC", self.rows()[0].description_label.full_text())

    def test_rows_offer_to_add_by_name(self):
        self.loaded()
        self.look("spot")
        row = self.rows()[1]
        self.assertEqual(row.command_label.full_text(), "Add Spotify")
        self.assertEqual(row.description_label.full_text(), "App")
        self.assertEqual(row.toolTip(), self.found[0]["location"])

    def test_descriptions_say_what_it_is_in_a_few_words(self):
        describe = MainWindow._fallback_description
        folder = self.root / "Projects"
        folder.mkdir()
        self.assertEqual(describe({"name": "Projects", "location": str(folder), "type": "file"}), f"Folder in {self.root.name}")
        self.assertEqual(describe({"name": "Notes", "location": str(self.root / "notes.txt"), "type": "file"}), f"File in {self.root.name}")
        self.assertEqual(describe({"name": "YouTube", "location": "https://www.youtube.com/", "type": "url"}), "youtube.com")
        self.assertEqual(describe({"name": "Clock", "location": r"shell:AppsFolder\Clock!App", "description": "Opens Clock", "type": "url"}), "App")
        self.assertEqual(describe({"name": "Bluetooth", "location": "ms-settings:bluetooth", "description": "Windows Settings", "type": "url"}), "Windows Settings")

    def test_a_website_shows_its_own_description(self):
        self.loaded()
        self.look("you")
        self.assertEqual(self.rows()[1].description_label.full_text(), "youtube.com")

    def editor(self):
        panel = self.window._editor_panel
        self.assertIsNotNone(panel, "the new-command editor is open")
        return panel

    def test_enter_does_nothing_until_down_then_opens_the_editor_filled_in(self):
        self.loaded()
        self.type("spot")
        self.window.on_enter_pressed()
        self.assertIsNone(self.window._editor_panel)
        self.press(Qt.Key.Key_Down)
        self.window.on_enter_pressed()
        panel = self.editor()
        self.assertEqual(panel.command_name_edit_box.text(), "Spotify")
        self.assertEqual(panel.command_action.command_action_edit_box.text(), self.found[0]["location"])
        self.assertEqual(panel.alias_box.alias_texts(), ["sp"])
        self.assertNotIn("Spotify", self.manager.commands, "nothing is added until Save")

    def test_saving_the_editor_adds_it(self):
        self.loaded()
        self.look("spot")
        self.window.on_enter_pressed()
        self.editor()._save_and_close()
        self.assertIn("Spotify", self.manager.commands)
        self.assertEqual(self.manager.commands["Spotify"]["aliases"], ["sp"])
        self.error_popup.assert_not_called()

    def test_down_visibly_highlights_a_fallback_row(self):
        from src import theme

        self.addCleanup(app.setStyleSheet, "")
        theme.apply_theme(app, self.settings, (Path(__file__).parents[1] / "style.qss").read_text(encoding="utf-8"))
        self.loaded()
        self.look("spot")
        row = self.rows()[1]
        self.assertIs(self.window.get_selected_row(), row)
        app.processEvents()
        selected = row.grab().toImage().pixelColor(2, row.height() // 2)
        self.press(Qt.Key.Key_Up)
        app.processEvents()
        self.assertIsNone(self.window.get_selected_row())
        unselected = row.grab().toImage().pixelColor(2, row.height() // 2)
        self.assertNotEqual(selected.name(), unselected.name(), "the selected fallback row must look selected")

    def test_up_from_the_first_fallback_row_selects_nothing_again(self):
        self.loaded()
        self.type("st")
        self.press(Qt.Key.Key_Down)
        self.press(Qt.Key.Key_Down)
        self.assertEqual(self.window.get_selected_row().fallback_candidate["name"], "Visual Studio Code")
        self.press(Qt.Key.Key_Up)
        self.assertEqual(self.window.get_selected_row().fallback_candidate["name"], "Steam")
        self.press(Qt.Key.Key_Up)
        self.assertIsNone(self.window.get_selected_row())
        with mock.patch.object(self.manager, "execute_command") as execute:
            self.window.on_enter_pressed()
        execute.assert_not_called()
        self.assertEqual(self.window.search_input_widget.text(), "st")

    def test_fallback_rows_are_styled_as_fallback_from_the_start(self):
        from src import theme

        self.addCleanup(app.setStyleSheet, "")
        theme.apply_theme(app, self.settings, (Path(__file__).parents[1] / "style.qss").read_text(encoding="utf-8"))
        self.type("to")
        command = self.rows()[0]
        self.assertTrue(command.command_label.font().bold())
        self.loaded()
        self.look("st")
        fallback = self.rows()[1]
        self.assertFalse(fallback.command_label.font().bold(), "a fallback title must not look like a command's")

    def test_open_without_adding_opens_and_adds_nothing(self):
        self.loaded()
        self.type("spot")
        with mock.patch.object(self.manager, "open_candidate", return_value=None) as open_candidate:
            self.window._open_fallback(self.found[0])
        open_candidate.assert_called_once()
        self.assertNotIn("Spotify", self.manager.commands)

    def test_whatever_is_already_a_command_is_not_offered(self):
        self.loaded()
        self.type("tool")
        self.assertEqual(self.fallback_names(), [])

    def test_the_first_miss_starts_one_scan_and_says_so(self):
        with mock.patch.object(launcher_gui.ProgramDiscoveryThread, "start") as start:
            self.look("ste")
            self.look("stea")
        start.assert_called_once()
        self.assertIn("Looking on this PC\u2026", [row.command_label.full_text() for row in self.rows()])
        self.window._fallback_thread.candidates = self.found
        self.window._on_fallback_scan_finished()
        self.assertEqual(self.fallback_names(), ["Steam"])

    def test_an_old_list_answers_while_a_new_scan_adds_what_was_installed(self):
        self.window.fallback_index.set_candidates(self.found, now=0.0)
        with mock.patch.object(launcher_gui.ProgramDiscoveryThread, "start") as start:
            self.look("st")
        start.assert_called_once()
        self.assertEqual(self.fallback_names(), ["Steam", "Visual Studio Code"])
        installed = {"name": "Stellarium", "aliases": [], "location": str(self.root / "stellarium.exe"), "type": "file"}
        Path(installed["location"]).write_bytes(b"")
        self.window._fallback_thread.candidates = [*self.found, installed]
        self.window._on_fallback_scan_finished()
        self.assertEqual(self.fallback_names(), ["Steam", "Stellarium", "Visual Studio Code"])

    def test_with_the_setting_off_a_miss_offers_nothing_and_looks_nowhere(self):
        self.settings.search.suggest_when_no_match = False
        with mock.patch.object(launcher_gui.ProgramDiscoveryThread, "start") as start:
            self.look("ste")
        start.assert_not_called()
        self.assertEqual(self.fallback_names(), [])
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn("↓", self.rows()[0].description_label.full_text())

    def test_no_scan_starts_while_commands_match(self):
        with mock.patch.object(launcher_gui.ProgramDiscoveryThread, "start") as start:
            self.type("to")
        start.assert_not_called()

    def test_ctrl_n_on_a_fallback_row_opens_the_same_editor(self):
        self.loaded()
        self.look("spot")
        self.window.open_new_command()
        self.assertEqual(self.editor().command_name_edit_box.text(), "Spotify")
        self.assertNotIn("Spotify", self.manager.commands)

    def test_a_taken_alias_is_left_out_of_the_editor(self):
        self.manager.commands["Tool"]["aliases"] = ["sp"]
        self.loaded()
        self.look("spot")
        self.window.on_enter_pressed()
        self.assertEqual(self.editor().alias_box.alias_texts(), [])

    def test_ctrl_n_with_nothing_selected_still_adds_what_was_typed(self):
        self.loaded()
        self.type("spot")
        with mock.patch.object(self.window, "open_new_command_with") as new_command:
            self.window.open_new_command()
        new_command.assert_called_once_with(name="spot", target=None)


if __name__ == "__main__":
    unittest.main()
