"""Editor, launcher and recommendation UI for Settings pages, search keywords,
the web search offer and groups."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidget

app = QApplication.instance() or QApplication(sys.argv)

from src.command_editor import CommandEditorPanel, CommandType, suggested_description, suggested_name  # noqa: E402
from src.command_manager import CommandManager  # noqa: E402
from src.icon_manager import IconManager  # noqa: E402
from src.launcher_gui import MainWindow  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.settings_editor import ProgramImportDialog, XCheckBox, candidate_kind  # noqa: E402
from src.windows_settings import discover_settings_pages  # noqa: E402


class _CommandsFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        tool = self.root / "tool.exe"
        tool.write_bytes(b"")
        self.commands_path = self.root / "commands.toml"
        self.commands_path.write_text(
            f"""[[command]]
name = "Tool"
aliases = []
location = '{tool}'
description = ""

[[command]]
name = "Jira"
aliases = ["j"]
location = 'https://jira.example.com/browse/{{query}}'
description = "Search Jira"
type = "url"
""",
            encoding="utf-8",
        )
        self.settings = Settings()
        self.icon_manager = IconManager(self.settings)
        self.manager = CommandManager(self.commands_path, self.settings)

    def tearDown(self):
        self._tmp.cleanup()


class SettingsPageUiTests(_CommandsFixture):
    def test_settings_pages_get_their_own_tab_with_icons_drawn_in_memory(self):
        with mock.patch("src.windows_settings.available_pages", return_value=None):
            pages = discover_settings_pages()[:3]
        self.assertEqual(candidate_kind(pages[1]), "setting")
        with mock.patch.object(self.icon_manager, "render_recipe_icon") as writes_file:
            dialog = ProgramImportDialog(pages, set(), self.icon_manager, command_manager=self.manager)
        writes_file.assert_not_called()
        headings = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        self.assertEqual(headings, ["Windows Settings (3)"])
        settings_list = dialog.lists["setting"]
        names = [settings_list.item(i).data(Qt.ItemDataRole.UserRole)["name"] for i in range(settings_list.count())]
        self.assertEqual(names, ["Windows Settings", "Display", "Sound"], "the listed order is kept")
        row = settings_list.itemWidget(settings_list.item(0))
        self.assertIsInstance(row.check, XCheckBox, "rows tick with the same X as Settings")

    def test_typing_a_settings_link_prefills_its_name(self):
        self.assertEqual(suggested_name(CommandType.APP, "ms-settings:bluetooth"), "Bluetooth & devices")
        self.assertEqual(suggested_description(CommandType.APP, "ms-settings:bluetooth", "Bluetooth & devices"), "Opens Bluetooth & devices in Windows Settings")
        self.assertEqual(CommandType.from_command({"location": "ms-settings:display"}), CommandType.APP)
        panel = CommandEditorPanel(None, self.icon_manager, self.manager)
        panel.command_action.command_action_edit_box.setText("ms-settings:sound")
        self.assertEqual(panel.command_name_edit_box.text(), "Sound")
        self.assertEqual(panel.command_action.status_dot.toolTip(), "A Windows Settings page")


class GroupEditorTests(_CommandsFixture):
    def test_a_group_is_built_from_command_names_in_order(self):
        panel = CommandEditorPanel(None, self.icon_manager, self.manager)
        panel.command_name_edit_box.setText("Start day")
        panel.command_type_selector.select(CommandType.GROUP)
        targets = panel.command_action.targets_box
        self.assertTrue(targets.isVisibleTo(panel))
        self.assertFalse(panel.command_action.command_action_edit_box.isVisibleTo(panel))

        for text in ("jira", "Nope", "Start day", "tool", "TOOL"):
            targets.enter_box.setText(text)
            targets.on_enter_pressed()
        self.assertEqual(panel.command_action.targets(), ["Jira", "Tool"], "names are stored as the commands spell them")
        self.assertEqual(targets.error_label.text(), "'Tool' is already in the list.")
        targets.enter_box.clear()  # a rejected name left in the box would stop the save

        with mock.patch.object(self.manager, "save_command") as save:
            panel._save_and_close()
        saved = save.call_args.args[0]
        self.assertEqual((saved["type"], saved["targets"], saved["location"]), ("group", ["Jira", "Tool"], ""))
        self.assertEqual(saved["icon_glyph"], "outline:STACK_2", "a group without a chosen icon gets the stack")

    def test_an_existing_group_opens_in_group_mode_and_tracks_changes(self):
        self.manager.save_command({"name": "Start day", "aliases": [], "location": "", "description": "", "type": "group", "targets": ["Tool", "Jira"]})
        panel = CommandEditorPanel(self.manager.commands["Start day"], self.icon_manager, self.manager)
        self.assertEqual(panel.command_type_selector.selection, CommandType.GROUP)
        self.assertEqual(panel.command_action.targets(), ["Tool", "Jira"])
        self.assertFalse(panel._is_dirty())
        panel.command_action.targets_box.remove_alias(panel.command_action.targets_box.aliases[0])
        self.assertTrue(panel._is_dirty())


class LauncherRowTests(_CommandsFixture):
    def _window(self):
        window = SimpleNamespace(
            results_list_widget=QListWidget(),
            settings=self.settings,
            icon_manager=self.icon_manager,
            hide_launcher=mock.Mock(),
            display_error_popup=mock.Mock(),
        )
        window._add_row = lambda row, *args: MainWindow._add_row(window, row, *args)
        return window

    def test_a_search_row_names_the_command_and_carries_the_query(self):
        window = self._window()
        command = self.manager.commands["Jira"]
        row = MainWindow._search_row(window, command, "EC-123")
        self.assertEqual(row.command_label.text(), "Search Jira for “EC-123”")
        self.assertEqual((row.command_name, row.query), ("Jira", "EC-123"))
        empty = MainWindow._search_row(window, command, "")
        self.assertEqual(empty.command_label.text(), "Search Jira")

    def test_run_counters_only_leave_room_for_edit_buttons_that_are_shown(self):
        from src.launcher_gui import ResultRow

        def trailing_space(show_edit_button):
            self.settings.search.show_edit_button = show_edit_button
            row = ResultRow(self._window(), "", "Task Manager", "", run_counter="2 runs")
            layout = row.layout()
            return layout.itemAt(layout.count() - 1).spacerItem() is not None

        self.assertTrue(trailing_space(True))
        self.assertFalse(trailing_space(False))

    def test_unmatched_text_offers_a_web_search(self):
        window = self._window()
        self.settings.general.web_search_enabled = True
        self.assertTrue(MainWindow._show_web_search_row(window, "cable sleeve sizes"))
        row = window.results_list_widget.itemWidget(window.results_list_widget.item(0))
        self.assertEqual(row.command_label.text(), "Search the web for “cable sleeve sizes”")
        with mock.patch("src.launcher_gui.open_in_browser") as open_browser:
            row.action()
        open_browser.assert_called_once_with("https://www.google.com/search?q=cable+sleeve+sizes", "default")
        window.hide_launcher.assert_called_once()

    def test_the_web_search_offer_can_be_turned_off(self):
        window = self._window()
        self.settings.general.web_search_enabled = False
        self.assertFalse(MainWindow._show_web_search_row(window, "anything"))
        self.settings.general.web_search_enabled = True
        self.settings.general.web_search = ""
        self.assertFalse(MainWindow._show_web_search_row(window, "anything"))
        self.assertEqual(window.results_list_widget.count(), 0)


class LauncherCompletionTests(unittest.TestCase):
    """The blue completion in the search box follows the selected row, and Tab keeps it."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        tool = root / "tool.exe"
        tool.write_bytes(b"")
        commands_path = root / "commands.toml"
        commands_path.write_text(
            f"""[[command]]
name = "TestTrack"
aliases = []
location = '{tool}'
description = ""

[[command]]
name = "Templates"
aliases = []
location = '{tool}'
description = ""

[[command]]
name = "Jira"
aliases = []
location = 'https://jira.example.com/browse/{{query}}'
description = "Search Jira"
type = "url"

[[command]]
name = "Silicon Expert"
aliases = ["se"]
location = '{tool}'
description = ""

[[command]]
name = "Service Manual"
aliases = ["servicing"]
location = '{tool}'
description = ""
""",
            encoding="utf-8",
        )
        self.settings = Settings()
        self.settings.search.sort_results = "name"
        # The settings path decides where the first-start marker is written: keep it in the temp folder.
        self.window = MainWindow(CommandManager(commands_path, self.settings), self.settings, settings_path=root / "settings.toml")
        self.box = self.window.search_input_widget

    def tearDown(self):
        self.window.close()
        self._tmp.cleanup()

    def _press(self, key):
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtCore import QEvent

        event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        if not app.sendEvent(self.box, event) or not event.isAccepted():
            self.window.keyPressEvent(event)

    def _rows(self):
        results = self.window.results_list_widget
        return [results.itemWidget(results.item(i)).command_name for i in range(results.count())]

    def test_moving_the_selection_completes_the_new_row(self):
        self.box.setText("te")
        self.assertEqual(self._rows(), ["Templates", "TestTrack"])
        self.assertEqual((self.box.text(), self.box.selectedText()), ("templates", "mplates"))
        self._press(Qt.Key.Key_Down)
        self.assertEqual((self.box.text(), self.box.selectedText()), ("testtrack", "sttrack"))
        self._press(Qt.Key.Key_Up)
        self.assertEqual((self.box.text(), self.box.selectedText()), ("templates", "mplates"))
        self.assertEqual(self.window.user_text, "te", "what was typed is kept")

    def test_a_row_that_does_not_start_with_the_typed_text_clears_the_completion(self):
        self.box.setText("te")
        with mock.patch.object(self.window, "get_selected_command", return_value="Jira"):
            self.window._follow_selection()
        self.assertEqual((self.box.text(), self.box.selectedText()), ("te", ""))

    def test_rows_matched_through_an_alias_complete_to_that_alias(self):
        self.box.setText("se")
        self.assertEqual(self._rows(), ["Silicon Expert", "Dash Settings", "Service Manual"], "an exact alias leads")
        self.assertEqual((self.box.text(), self.box.selectedText()), ("se", ""), "the alias is already complete")
        self._press(Qt.Key.Key_Down)
        self.assertEqual((self.box.text(), self.box.selectedText()), ("settings", "ttings"))
        self._press(Qt.Key.Key_Down)
        self.assertEqual((self.box.text(), self.box.selectedText()), ("service manual", "rvice manual"))

    def test_the_name_is_preferred_when_it_also_fits(self):
        self.assertEqual(self.window.cmd_manager.completion_keyword("Silicon Expert", "si"), "Silicon Expert")
        self.assertEqual(self.window.cmd_manager.completion_keyword("Silicon Expert", "SE"), "se")
        self.assertIsNone(self.window.cmd_manager.completion_keyword("Silicon Expert", "x"))

    def test_tab_keeps_the_completion_and_narrows_the_results(self):
        self.box.setText("te")
        self._press(Qt.Key.Key_Down)
        self._press(Qt.Key.Key_Tab)
        self.assertEqual((self.box.text(), self.box.selectedText()), ("testtrack", ""))
        self.assertEqual(self.box.cursorPosition(), len("testtrack"))
        self.assertEqual(self._rows(), ["TestTrack"])
        self.assertEqual(self.window.user_text, "testtrack")

    def test_tab_on_a_search_keyword_adds_the_space_that_starts_a_search(self):
        self.box.setText("ji")
        self.assertEqual(self.box.selectedText(), "ra")
        self._press(Qt.Key.Key_Tab)
        self.assertEqual(self.box.text(), "jira ")
        row = self.window.get_selected_row()
        self.assertEqual((row.command_name, row.query), ("Jira", ""))
        self.box.insert("EC-1")
        self.assertEqual(self.window.get_selected_row().query, "EC-1")

    def test_tab_never_moves_focus_out_of_the_box(self):
        self.window.show()
        self.box.setFocus()
        app.processEvents()
        for text in ("zzz", "te", "jira x"):
            self.box.setText(text)
            before = self.box.text().replace(self.box.selectedText(), "") if self.box.hasSelectedText() else text
            self._press(Qt.Key.Key_Tab)
            app.processEvents()
            self.assertIs(app.focusWidget(), self.box, text)
        self.box.setText("zzz")
        self._press(Qt.Key.Key_Tab)
        self.assertEqual(self.box.text(), "zzz", "nothing to complete leaves the text alone")

    def test_tab_completes_even_after_deleting_hid_the_blue_part(self):
        self.box.setText("ji")
        self.window.is_deleting = True
        self.box.setText("jir")  # as if the completion had been backspaced away
        self.assertEqual(self.box.selectedText(), "")
        self._press(Qt.Key.Key_Tab)
        self.assertEqual(self.box.text(), "jira ")


if __name__ == "__main__":
    unittest.main()
