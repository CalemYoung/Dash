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
        return SimpleNamespace(
            results_list_widget=QListWidget(),
            settings=self.settings,
            icon_manager=self.icon_manager,
            hide_launcher=mock.Mock(),
            display_error_popup=mock.Mock(),
        )

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


if __name__ == "__main__":
    unittest.main()
