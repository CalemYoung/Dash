"""The launcher window: hiding on focus loss, the tray menu, error notices,
the calculator and no-results rows, result actions, drag and drop, theme,
sizing, dates, accessibility and first-run tips."""
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import QEvent, QLocale, QMimeData, Qt, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)

from src import app_log, calculator, theme  # noqa: E402
from src.command_manager import CommandManager  # noqa: E402
from src.launcher_gui import MainWindow, ProgramDiscoveryThread, ResultRow, target_from_text  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.settings_editor import SettingsEditorPanel  # noqa: E402


class _LauncherFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tool = self.root / "tool.exe"
        self.tool.write_bytes(b"")
        self.commands_path = self.root / "commands.toml"
        self.commands_path.write_text(
            f"""[[command]]
name = "GitHub"
aliases = ["gh"]
location = 'https://github.com/search?q={{query}}'
description = "Search GitHub"
type = "url"

[[command]]
name = "Tool"
aliases = []
location = '{self.tool}'
description = "Opens Tool"

[[command]]
name = "Visual Studio Code"
aliases = []
location = '{self.tool}'
description = ""

[[command]]
name = "Start work"
aliases = []
location = ""
description = ""
type = "group"
targets = ["GitHub", "Tool"]
""",
            encoding="utf-8",
        )
        self.settings = Settings()
        self.settings.search.sort_results = "name"
        # Markers go in the temp folder; the tips are tested on their own.
        (self.root / "first_start_shown").write_text("shown", encoding="utf-8")
        (self.root / "first_tips_shown").write_text("shown", encoding="utf-8")
        self.manager = CommandManager(self.commands_path, self.settings)
        self.window = MainWindow(self.manager, self.settings, settings_path=self.root / "settings.toml")
        self.box = self.window.search_input_widget

    def tearDown(self):
        self.window._editor_panel = None
        self.window.close()
        self.window.deleteLater()
        app_log.set_error_reporter(None)
        self._tmp.cleanup()

    def _press(self, key, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
        event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, text)
        if not app.sendEvent(self.box, event) or not event.isAccepted():
            self.window.keyPressEvent(event)

    def _rows(self):
        results = self.window.results_list_widget
        return [results.itemWidget(results.item(i)) for i in range(results.count())]

    def _titles(self):
        return [row.command_label.full_text() for row in self._rows()]


class HideOnFocusLossTests(_LauncherFixture):
    def _shown(self):
        self.window.show()
        self.window._focus_grace_until = 0.0
        return self.window

    def test_losing_focus_hides_the_launcher(self):
        window = self._shown()
        with mock.patch.object(window, "isActiveWindow", return_value=False):
            window._hide_if_focus_lost()
        self.assertFalse(window.isVisible())

    def test_the_setting_off_keeps_it_open(self):
        window = self._shown()
        self.settings.general.hide_when_focus_lost = False
        with mock.patch.object(window, "isActiveWindow", return_value=False):
            window._hide_if_focus_lost()
        self.assertTrue(window.isVisible())

    def test_an_editor_a_dialog_or_the_grace_period_keep_it_open(self):
        window = self._shown()
        with mock.patch.object(window, "isActiveWindow", return_value=False):
            window._editor_panel = object()
            window._hide_if_focus_lost()
            self.assertTrue(window.isVisible(), "the editor panel counts as in use")
            window._editor_panel = None

            with window._dialog_open():
                window._hide_if_focus_lost()
                self.assertTrue(window.isVisible(), "a dialog being opened")
            window._hide_if_focus_lost()
            self.assertTrue(window.isVisible(), "just after a dialog closes")

            window._focus_grace_until = 0.0
            window._tray_menu_open = True
            window._hide_if_focus_lost()
            self.assertTrue(window.isVisible(), "the tray menu")
            window._tray_menu_open = False

            window._begin_focus_grace(500)
            window._hide_if_focus_lost()
            self.assertTrue(window.isVisible(), "the activation dance after showing")

    def test_activation_starts_a_grace_period(self):
        self.window.activate_launcher()
        self.assertGreater(self.window._focus_grace_until, time.monotonic())

    def test_deactivation_schedules_a_check(self):
        window = self._shown()
        with mock.patch("src.launcher_gui.QTimer.singleShot") as single_shot, mock.patch.object(window, "isActiveWindow", return_value=False):
            window.changeEvent(QEvent(QEvent.Type.ActivationChange))
        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[1], window._hide_if_focus_lost)


class TrayTests(_LauncherFixture):
    def test_tray_menu_items(self):
        titles = [action.text() for action in self.window.tray_menu.actions() if not action.isSeparator()]
        for expected in ("Open Dash", "Settings...", "Open Install Location", "Open Config Folder", "Open Log Folder", "Check for Updates...", "About", "Quit"):
            self.assertIn(expected, titles)
        self.assertEqual(self.window.tray_menu.defaultAction().text(), "Open Dash")

    def test_clicking_the_icon_opens_the_launcher_once(self):
        with mock.patch.object(self.window, "activate_launcher") as activate:
            self.window._on_tray_activated(mock.sentinel.context)
            activate.assert_not_called()
            self.window._on_tray_activated(self.window.tray.ActivationReason.DoubleClick)
            activate.assert_called_once()

    def test_a_hotkey_failure_becomes_a_tray_notice(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Listener(QObject):
            registrationFailed = pyqtSignal(str)

        listener = Listener()
        self.window.set_hotkey_listener(listener)
        with mock.patch.object(self.window, "show_tray_notice") as notice:
            listener.registrationFailed.emit("Alt+Space is already used by another app.")
        notice.assert_called_once_with("Dash hotkey", "Alt+Space is already used by another app.")

    def test_notices_wait_for_the_tray(self):
        tray = self.window.tray
        self.window.tray = None
        self.window.show_tray_notice("Title", "Message")
        self.assertEqual(self.window._pending_tray_messages, [("Title", "Message")])
        self.window.tray = tray


class ErrorNoticeTests(_LauncherFixture):
    def test_the_reporter_is_registered_and_rate_limited(self):
        self.assertIsNotNone(app_log._error_reporter)
        with mock.patch("src.launcher_gui.QMessageBox.show") as show:
            self.window._show_error_notice("ValueError: boom")
            self.window._error_notice = None
            self.window._show_error_notice("ValueError: boom again")
        self.assertEqual(show.call_count, 1, "at most one notice every 30 seconds")

    def test_the_notice_is_plain_and_never_shows_the_exception(self):
        with mock.patch("src.launcher_gui.QMessageBox.show"):
            self.window._show_error_notice("KeyError: 'secret detail'")
        notice = self.window._error_notice
        self.assertEqual(notice.text(), "Something went wrong in Dash. Details were saved to the log.")
        self.assertNotIn("secret", notice.informativeText() + notice.detailedText())
        self.assertIn("Open Log Folder", [button.text() for button in notice.buttons()])

    def test_load_warnings_are_shown_once_then_cleared(self):
        self.settings.load_warnings.append("Settings were reset.")
        self.manager.load_warnings.append("One command was left out.")
        with mock.patch("src.launcher_gui.QMessageBox.show") as show:
            self.window.show_load_warnings()
            self.window.show_load_warnings()
        show.assert_called_once()
        self.assertEqual((self.settings.load_warnings, self.manager.load_warnings), ([], []))

    def test_a_failed_save_is_reported_plainly(self):
        with mock.patch.object(self.manager, "delete_command", side_effect=PermissionError(13, "Access is denied")), mock.patch(
            "src.launcher_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
        ), mock.patch("src.launcher_gui.QMessageBox.warning") as warning:
            self.window.delete_command_with_confirmation("Tool")
        message = warning.call_args.args[2]
        self.assertNotIn("Access is denied", message)
        self.assertIn("couldn't save", message)


class CalculatorAndNoResultTests(_LauncherFixture):
    def test_a_calculation_shows_the_formatted_result_and_enter_copies_it(self):
        self.box.setText("0.1+0.2")
        self.assertEqual(self._titles(), ["= 0.3"])
        with mock.patch("src.launcher_gui.pyperclip.copy") as copy:
            self.window.on_enter_pressed()
        copy.assert_called_once_with("0.3")

    def test_the_locale_decimal_separator_is_used(self):
        self.window._decimal_separator = ","
        self.box.setText("1,5*2")
        self.assertEqual(self._titles(), ["= 3"])
        self.box.setText("1/4")
        self.assertEqual(self._titles(), ["= 0,25"])

    def test_a_calculation_that_cannot_be_done_says_why_and_enter_keeps_the_text(self):
        self.window.show()
        self.box.setText("1/0")
        self.assertEqual(self._titles(), [calculator.DIVIDE_BY_ZERO])
        self.window.on_enter_pressed()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.box.text(), "1/0")

    def test_with_web_search_off_only_the_no_match_line_shows(self):
        for text in ("zzqx", "12*"):
            self.box.setText(text)
            self.assertEqual(self._titles(), [f"No commands match \u201c{text}\u201d"], text)
            self.assertIsNone(self.window.get_selected_row(), "nothing is selected, so Enter does nothing")

    def test_with_web_search_on_the_search_comes_first(self):
        self.settings.general.web_search_enabled = True
        self.box.setText("zzqx")
        self.assertEqual(self._titles(), ["Search the web for \u201czzqx\u201d", "No commands match \u201czzqx\u201d"])
        self.assertEqual(self.window.results_list_widget.currentRow(), 0)
        # The arrows stay on the search: the line below is only for reading.
        self._press(Qt.Key.Key_Down)
        self.assertEqual(self.window.results_list_widget.currentRow(), 0)

    def test_enter_with_no_match_does_not_open_the_editor(self):
        self.window.show()
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.box.setText("zzqx")
            self.window.on_enter_pressed()
        open_new.assert_not_called()
        self.assertEqual(self.box.text(), "zzqx")

    def test_the_no_match_line_names_the_new_command_shortcut(self):
        self.box.setText("zzqx")
        row = self._rows()[0]
        shortcut = self.window._format_shortcut(self.settings.shortcuts.new_command)
        self.assertIn(shortcut, row.description_label.full_text())

    def test_ctrl_n_opens_the_editor_with_the_name_filled_in(self):
        self.window.show()
        self.box.setText("zzqx")
        self.window.open_new_command()
        panel = self.window._editor_panel
        self.assertIsNotNone(panel)
        self.assertEqual(panel.command_name_edit_box.text(), "zzqx")
        self.window.close_editor()

    def test_add_to_dash_from_explorer_opens_the_editor_with_the_path(self):
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.window.add_from_explorer('"C:/Tools/My App.lnk"')
        open_new.assert_called_once_with(target=r"C:\Tools\My App.lnk")

    def test_ctrl_n_uses_a_web_address_as_the_target(self):
        self.window.show()
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.box.setText("example.org")
            self.window.open_new_command()
        open_new.assert_called_once_with(name=None, target="https://example.org")

    def test_ctrl_n_starts_from_the_typed_text(self):
        self.window.show()
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.box.setText("zzqx")
            self.window.open_new_command()
            open_new.assert_called_with(name="zzqx", target=None)
            self.box.setText("C:\\Tools\\app.exe")
            self.window.open_new_command()
            open_new.assert_called_with(name=None, target="C:\\Tools\\app.exe")

    def test_prefill_uses_the_editor_api_when_there_is_one(self):
        panel = mock.Mock()
        MainWindow._prefill_editor(panel, name="x", target=None)
        panel.prefill.assert_called_once_with(name="x", target=None)

    def test_text_that_names_a_target(self):
        self.assertEqual(target_from_text("www.bbc.co.uk"), "https://www.bbc.co.uk")
        self.assertEqual(target_from_text("C:\\Users"), "C:\\Users")
        self.assertIsNone(target_from_text("notes.txt"))
        self.assertIsNone(target_from_text("hello world"))


class ResultRowTests(_LauncherFixture):
    def test_site_search_rows_say_tab_to_search_only_when_selected(self):
        self.box.setText("g")
        rows = {row.command_name: row for row in self._rows()}
        self.assertIsNotNone(rows["GitHub"].search_hint_label)
        self.assertIsNone(rows["Tool"].search_hint_label if "Tool" in rows else None)
        self.window.results_list_widget.setCurrentRow(self._rows().index(rows["GitHub"]))
        self.assertFalse(rows["GitHub"].search_hint_label.isHidden())

    def test_a_group_without_a_description_says_what_it_opens(self):
        self.box.setText("start")
        self.assertEqual(self._rows()[0].description_label.full_text(), "Opens: GitHub, Tool")

    def test_rows_carry_accessible_text_and_named_edit_buttons(self):
        self.box.setText("tool")
        item = self.window.results_list_widget.item(0)
        self.assertEqual(item.data(Qt.ItemDataRole.AccessibleTextRole), "Tool, Opens Tool")
        self.assertEqual(self._rows()[0].edit_button.accessibleName(), "Edit Tool")
        self.assertEqual(self.box.accessibleName(), "Search commands")

    def test_tab_on_a_word_start_row_fills_in_the_name(self):
        self.box.setText("code")
        self.assertEqual(self._titles(), ["Visual Studio Code"])
        self.assertEqual(self.box.text(), "code", "no completion is spliced in for a word-start match")
        self._press(Qt.Key.Key_Tab)
        self.assertEqual(self.box.text(), "Visual Studio Code")

    def test_tab_on_a_search_row_keeps_the_query(self):
        self.box.setText("gh cats")
        self._press(Qt.Key.Key_Tab)
        self.assertEqual(self.box.text(), "gh cats")

    def test_launch_option_keys(self):
        self.box.setText("tool")
        with mock.patch.object(self.manager, "execute_command", return_value=None) as execute, mock.patch.object(
            self.manager, "can_run_as_administrator", return_value=True
        ):
            self._press(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        execute.assert_called_once_with(self.window, "Tool", query=None, new_instance=True)

        self.box.setText("tool")
        with mock.patch.object(self.manager, "run_as_administrator", return_value="Denied") as run_as, mock.patch.object(
            self.manager, "can_run_as_administrator", return_value=True
        ), mock.patch.object(self.window, "_show_launch_failure") as failure:
            self._press(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        run_as.assert_called_once_with("Tool")
        failure.assert_called_once_with(self.manager.commands["Tool"], "Denied")

        self.box.setText("tool")
        with mock.patch.object(self.manager, "open_containing_folder", return_value=None) as folder:
            self._press(Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier)
        folder.assert_called_once_with("Tool")

    def test_ctrl_c_copies_the_path_unless_text_is_selected(self):
        self.box.setText("tool")
        with mock.patch("src.launcher_gui.pyperclip.copy") as copy:
            self._press(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        copy.assert_called_once_with(str(self.tool))

        self.box.setText("gh cats")
        self.box.setSelection(0, 2)
        with mock.patch("src.launcher_gui.pyperclip.copy") as copy:
            self._press(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        copy.assert_not_called()

    def test_the_menu_offers_what_the_command_can_do(self):
        self.box.setText("tool")
        captured = {}

        def fake_exec(menu, *_args):
            captured["titles"] = [action.text().split("\t")[0] for action in menu.actions() if not action.isSeparator()]
            captured["enabled"] = {action.text().split("\t")[0]: action.isEnabled() for action in menu.actions()}

        with mock.patch("src.launcher_gui.QMenu.exec", new=fake_exec), mock.patch.object(self.manager, "can_run_as_administrator", return_value=False):
            self.window.show_selected_row_menu()
        self.assertEqual(captured["titles"], ["Open", "Run as administrator", "Open containing folder", "Copy path", "Edit", "Delete..."])
        self.assertFalse(captured["enabled"]["Run as administrator"])

    def test_the_menu_key_shows_the_row_menu_once(self):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QContextMenuEvent

        self.box.setText("tool")
        with mock.patch.object(self.window, "show_row_menu") as show:
            self._press(Qt.Key.Key_Menu)
            app.sendEvent(self.box, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(1, 1)))
            self.assertEqual(show.call_count, 1, "the key press and its context menu event are one request")

    def test_shift_f10_opens_no_menu(self):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QContextMenuEvent

        self.box.setText("tool")
        with mock.patch.object(self.window, "show_row_menu") as show:
            self._press(Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
            with mock.patch("src.launcher_gui.QApplication.keyboardModifiers", return_value=Qt.KeyboardModifier.ShiftModifier):
                handled = self.window.eventFilter(self.box, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(1, 1)))
        self.assertTrue(handled, "the box's own edit menu doesn't open either")
        show.assert_not_called()

    def test_delete_asks_first_and_names_the_groups(self):
        with mock.patch("src.launcher_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.window.delete_command_with_confirmation("Tool")
        self.assertIn("Start work", question.call_args.args[2])
        self.assertIn("Tool", self.manager.commands)
        with mock.patch("src.launcher_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window.delete_command_with_confirmation("Tool")
        self.assertNotIn("Tool", self.manager.commands)

    def test_right_click_never_opens_the_row(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent

        self.box.setText("tool")
        row = self._rows()[0]
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
        with mock.patch.object(self.window, "activate_row") as activate:
            row.mousePressEvent(event)
        activate.assert_not_called()


class DropTests(_LauncherFixture):
    def test_files_shortcuts_and_addresses_are_accepted(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.root / "App.lnk"))])
        self.assertTrue(MainWindow._dropped_target(mime).endswith("App.lnk"))
        web = QMimeData()
        web.setUrls([QUrl("https://example.com/page")])
        self.assertEqual(MainWindow._dropped_target(web), "https://example.com/page")
        text = QMimeData()
        text.setText("just words")
        self.assertIsNone(MainWindow._dropped_target(text))

    def test_a_drop_opens_the_new_command_editor_with_the_target(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QDropEvent

        mime = QMimeData()
        mime.setUrls([QUrl("https://example.com")])
        event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.window.dropEvent(event)
            app.processEvents()
        open_new.assert_called_once_with(target="https://example.com")

    def test_pasting_a_copied_file_opens_the_editor_but_text_still_pastes(self):
        paste = lambda: QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, "v")  # noqa: E731
        file_mime = QMimeData()
        file_mime.setUrls([QUrl.fromLocalFile(str(self.root / "Report.docx"))])
        app.clipboard().setMimeData(file_mime)
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.assertTrue(self.window.eventFilter(self.box, paste()))
            app.processEvents()
        self.assertTrue(open_new.call_args.kwargs["target"].endswith("Report.docx"))

        app.clipboard().setText("hello")
        with mock.patch.object(self.window, "open_new_command_with") as open_new:
            self.assertFalse(self.window.eventFilter(self.box, paste()))
        open_new.assert_not_called()


class ThemeAndSizeTests(_LauncherFixture):
    def tearDown(self):
        theme.reset_for_tests()
        super().tearDown()

    def test_the_command_tree_paints_with_theme_colors(self):
        self.settings.search.show_command_tree = True
        self.box.setText("g")
        with mock.patch("src.launcher_gui.theme.color", wraps=theme.color) as color:
            self.window.search_tree_widget.grab()
        used = {call.args[0] for call in color.call_args_list}
        self.assertTrue({"tree_accent", "tree_text", "tree_muted", "tree_line", "tree_node_fill"} <= used)

    def test_a_theme_change_rebuilds_the_rows(self):
        self.box.setText("g")
        before = self._rows()[0]
        theme.apply_theme(None, Settings())  # emits notifier().changed
        self.assertIsNot(self._rows()[0], before)

    def test_rows_and_the_search_bar_grow_with_their_fonts(self):
        self.assertEqual(self.window._row_height(), 48, "the default fonts keep the default row")
        self.assertEqual(self.window._search_height(), self.settings.ui.search_height)
        self.settings.ui.result_font_size = 32
        self.settings.ui.search_font_size = 48
        self.assertGreater(self.window._row_height(), 48)
        self.assertGreater(self.window._search_height(), self.settings.ui.search_height)
        self.box.setText("g")
        heights = {self.window.results_list_widget.item(i).sizeHint().height() for i in range(self.window.results_list_widget.count())}
        self.assertEqual(heights, {self.window._row_height()})


class DateAndScreenTests(_LauncherFixture):
    def test_day_and_month_follow_the_locale_order(self):
        self.assertEqual(MainWindow._month_day_format(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)), "MMMM d")
        self.assertEqual(MainWindow._month_day_format(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom)), "d MMMM")
        self.assertEqual(MainWindow._month_day_format(QLocale(QLocale.Language.German, QLocale.Country.Germany)), "d MMMM")

    def test_a_missing_display_falls_back_to_the_one_under_the_pointer(self):
        self.settings.general.launcher_screen = "Not connected"
        under_mouse = mock.Mock(name="under mouse")
        with mock.patch("src.launcher_gui.QApplication.screenAt", return_value=under_mouse), mock.patch("src.launcher_gui.log") as log:
            self.assertIs(self.window._target_screen(), under_mouse)
            self.assertIs(self.window._target_screen(), under_mouse)
        self.assertEqual(log.info.call_count, 1, "logged once")


class SettingsWiringTests(_LauncherFixture):
    def test_settings_refreshes_browsers_and_manage_commands_can_edit(self):
        with mock.patch("src.launcher_gui.browsers.refresh_installed_browsers") as refresh:
            self.window.open_settings_editor()
        refresh.assert_called_once()
        self.assertIsInstance(self.window._editor_panel, SettingsEditorPanel)

        def fake_exec(dialog):
            dialog.editRequested.emit("Tool")
            return 1

        with mock.patch("src.launcher_gui.ManageCommandsDialog.exec", new=fake_exec):
            self.window._editor_panel.manageCommandsRequested.emit()
        panel = self.window._editor_panel
        self.assertNotIsInstance(panel, SettingsEditorPanel, "Settings made way for the editor")
        self.assertEqual(panel.command_name_edit_box.text(), "Tool")
        self.window.close_editor()

    def test_saving_settings_only_reregisters_a_changed_hotkey(self):
        listener = mock.Mock(registration_error=None)
        self.window._hotkey_listener = listener
        same = Settings()
        self.window._apply_settings(same)
        listener.update_hotkey.assert_not_called()
        changed = Settings()
        changed.general.hotkey = "Ctrl+Space"
        self.window._apply_settings(changed)
        listener.update_hotkey.assert_called_once_with("Ctrl+Space")


class TipsTests(_LauncherFixture):
    def test_tips_show_once_when_there_are_commands(self):
        marker = self.root / "first_tips_shown"
        marker.unlink()
        self.window._tips_checked = False
        self.window.activate_launcher()
        self.assertFalse(self.window.tip_label.isHidden())
        self.assertIn("Tab completes", self.window.tip_label.text())
        self.assertTrue(marker.exists())
        self.window.dismiss_tips()
        self.assertTrue(self.window.tip_label.isHidden())
        self.window._tips_checked = False
        self.window.activate_launcher()
        self.assertTrue(self.window.tip_label.isHidden(), "never twice")


class DiscoveryTests(unittest.TestCase):
    def test_windows_suggestions_are_found_on_the_scan_thread_and_lead(self):
        suggestion = {"name": "Downloads", "location": "C:\\Users\\me\\Downloads"}
        program = {"name": "App", "location": "C:\\App\\app.exe"}
        with mock.patch("src.installed_programs.discover_recent_program_commands", return_value=[program]), mock.patch(
            "src.installed_programs.discover_packaged_apps", return_value=[]
        ), mock.patch("src.personal_places.discover_quick_access_folders", return_value=[]), mock.patch(
            "src.personal_places.discover_bookmark_bar", return_value=[]
        ), mock.patch("src.windows_settings.discover_settings_pages", return_value=[]), mock.patch(
            "src.popular_websites.discover_popular_websites", return_value=[]
        ), mock.patch("src.installed_programs.discover_windows_suggestions", return_value=[suggestion]):
            thread = ProgramDiscoveryThread()
            thread.run()
        self.assertEqual([c["name"] for c in thread.candidates], ["Downloads", "App"])
        self.assertEqual(thread.error_message, "")


class SeedRunCountTests(unittest.TestCase):
    def test_counts_are_raised_never_lowered_and_saved(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.exe").write_bytes(b"")
            path = root / "commands.toml"
            path.write_text(f"[[command]]\nname = \"A\"\naliases = []\nlocation = '{root / 'a.exe'}'\ndescription = \"\"\n", encoding="utf-8")
            manager = CommandManager(path, Settings())
            manager.run_counts["A"] = 5
            manager.seed_run_counts({"A": 3, "Missing": 9, "Dash Settings": 4})
            self.assertEqual(manager.run_counts, {"A": 5})
            manager.seed_run_counts({"A": 12})
            self.assertEqual(manager.commands["A"]["times_executed"], 12)
            reloaded = CommandManager(path, Settings())
            self.assertEqual(reloaded.run_counts, {"A": 12})


if __name__ == "__main__":
    unittest.main()
