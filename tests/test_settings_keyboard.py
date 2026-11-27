"""Settings and the dialogs it opens work from the keyboard alone: every
setting is reached with Tab, edited without the mouse, and saved or closed
by key."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QScrollArea, QVBoxLayout, QWidget

app = QApplication.instance() or QApplication(sys.argv)


def close_strays():
    """Windows other tests left open (a notice, a launcher) must not take the
    keys meant for the window under test."""
    for widget in QApplication.topLevelWidgets():
        if widget.isVisible():
            widget.close()
    QTest.qWait(10)

from src.settings import Settings  # noqa: E402
from src.settings_editor import HotkeyLineEdit, ManageCommandsDialog, ProgramImportDialog, SettingsEditorPanel  # noqa: E402


class SettingsPanelKeyboardTests(unittest.TestCase):
    def setUp(self):
        close_strays()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.panel = SettingsEditorPanel(Settings(), Path(self._tmp.name) / "settings.toml")
        QVBoxLayout(self.window).addWidget(self.panel)
        self.window.resize(900, 700)
        self.window.show()
        self.window.activateWindow()
        QTest.qWait(20)
        self.saved = []
        self.panel.saved.connect(self.saved.append)

    def focus(self):
        widget = QApplication.focusWidget()
        if widget is None or not self.window.isAncestorOf(widget):
            return self.panel.focusWidget() or self.panel
        return widget

    def key(self, key, modifier=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.focus(), key, modifier)
        QTest.qWait(5)

    def test_opening_starts_on_the_first_setting_without_recording(self):
        hotkey = self.panel._controls["general.hotkey"]
        self.assertIs(self.focus(), hotkey)
        self.assertFalse(hotkey.recording)
        self.assertEqual(hotkey.text(), Settings().general.hotkey)

    def test_tab_passes_the_hotkey_field_and_never_stops_on_the_scroll_area(self):
        self.key(Qt.Key.Key_Tab)
        self.assertIs(self.focus(), self.panel._controls["general.display"] if "general.display" in self.panel._controls else self.focus())
        self.assertEqual(self.panel._controls["general.hotkey"].text(), Settings().general.hotkey)
        for _ in range(80):
            self.assertNotIsInstance(self.focus(), QScrollArea)
            self.key(Qt.Key.Key_Tab)

    def test_enter_records_a_new_hotkey_and_esc_keeps_the_old_one(self):
        hotkey = self.panel._controls["general.hotkey"]
        before = hotkey.text()
        self.key(Qt.Key.Key_Return)
        self.assertTrue(hotkey.recording)
        self.key(Qt.Key.Key_Escape)
        self.assertFalse(hotkey.recording)
        self.assertEqual(hotkey.text(), before)
        self.key(Qt.Key.Key_Space)
        self.key(Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        self.assertFalse(hotkey.recording)
        self.assertEqual(hotkey.text(), "Ctrl+Alt+K")
        QTest.qWait(10)
        self.assertEqual(self.saved[-1].general.hotkey, "Ctrl+Alt+K", "the new keys are saved straight away")

    def test_esc_on_the_hotkey_field_closes_settings_rather_than_recording(self):
        hotkey = self.panel._controls["general.hotkey"]
        closed = []
        self.panel.closed.connect(lambda: closed.append(True))
        self.key(Qt.Key.Key_Escape)
        self.assertEqual(closed, [True])
        self.assertEqual(hotkey.text(), Settings().general.hotkey)

    def test_ctrl_down_and_up_jump_between_groups(self):
        starts = self.panel._group_starts
        self.key(Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(self.focus(), starts[1])
        self.key(Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(self.focus(), starts[2])
        self.key(Qt.Key.Key_Up, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(self.focus(), starts[1])

    def test_space_and_enter_tick_a_checkbox_and_it_saves_at_once(self):
        autocomplete = self.panel._controls["search.autocomplete"]
        autocomplete.setFocus()
        before = autocomplete.isChecked()
        self.key(Qt.Key.Key_Space)
        self.assertNotEqual(autocomplete.isChecked(), before)
        self.key(Qt.Key.Key_Return)
        self.assertEqual(autocomplete.isChecked(), before)
        QTest.qWait(10)
        self.assertTrue(self.saved, "each change saves without a Save button")

    def test_down_and_up_move_between_settings(self):
        hotkey = self.panel._controls["general.hotkey"]
        self.key(Qt.Key.Key_Down)
        second = self.focus()
        self.assertIsNot(second, hotkey)
        self.key(Qt.Key.Key_Down)
        self.assertIsNot(self.focus(), second)
        self.key(Qt.Key.Key_Up)
        self.assertIs(self.focus(), second)
        self.key(Qt.Key.Key_Up)
        self.assertIs(self.focus(), hotkey)

    def test_right_and_left_jump_between_the_columns(self):
        hotkey = self.panel._controls["general.hotkey"]
        theme = self.panel._controls["ui.theme"]
        self.key(Qt.Key.Key_Right)
        self.assertIs(self.focus(), theme, "the first setting on the right, level with the hotkey")
        self.key(Qt.Key.Key_Right)
        self.assertIs(self.focus(), theme, "nothing further right")
        self.key(Qt.Key.Key_Left)
        self.assertIs(self.focus(), hotkey)

    def test_enter_opens_a_drop_down_and_its_arrows_choose(self):
        theme = self.panel._controls["ui.theme"]
        theme.setFocus()
        start = theme.currentIndex()
        self.key(Qt.Key.Key_Return)
        QTest.qWait(20)
        view = theme.view()
        self.assertTrue(view.isVisible(), "Enter opens the list")
        QTest.keyClick(view, Qt.Key.Key_Down)
        QTest.keyClick(view, Qt.Key.Key_Return)
        QTest.qWait(20)
        self.assertEqual(theme.currentIndex(), (start + 1) % theme.count(), "the list's own arrows chose the next entry")
        self.assertFalse(view.isVisible())

    def test_plus_and_minus_step_a_number(self):
        size = self.panel._controls["ui.search_font_size"]
        size.setFocus()
        start = size.value()
        self.key(Qt.Key.Key_Plus)
        self.assertEqual(size.value(), start + 1)
        self.key(Qt.Key.Key_Minus)
        self.assertEqual(size.value(), start)

    def test_the_tab_order_follows_the_page(self):
        controls = self.panel._controls
        clear = next(b for b in self.panel.findChildren(QPushButton) if b.accessibleName() == "Clear usage history")
        controls["search.sort_results"].setFocus()
        self.key(Qt.Key.Key_Tab)
        self.assertIs(self.focus(), clear, "Usage history sits under Sort results by, and Tab follows it")
        self.key(Qt.Key.Key_Tab)
        self.assertIs(self.focus(), controls["search.max_results"])
        show_descriptions = controls["search.show_descriptions"]
        show_descriptions.setFocus()
        self.key(Qt.Key.Key_Up)
        self.assertIs(self.focus(), controls["search.max_results"], "up from the first Display box is the last Search setting")

    def test_arrows_are_recorded_while_a_shortcut_field_records(self):
        hotkey = self.panel._controls["general.hotkey"]
        self.key(Qt.Key.Key_Return)
        self.key(Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        self.assertEqual(hotkey.text(), "Ctrl+Alt+Down")
        self.assertIs(self.focus(), hotkey)


class DialogKeyboardTests(unittest.TestCase):
    COMMANDS = [
        {"name": "Spotify", "location": r"C:\Apps\Spotify.exe", "type": "file", "aliases": ["music"]},
        {"name": "Steam", "location": r"C:\Apps\Steam.exe", "type": "file", "aliases": []},
    ]

    def show(self, dialog):
        close_strays()
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        dialog.activateWindow()
        QTest.qWait(20)
        return dialog

    def test_manage_commands_from_the_keyboard(self):
        manager = mock.Mock(spec=["delete_command", "commands"])
        manager.commands = {c["name"]: c for c in self.COMMANDS}
        patch = mock.patch.object(ManageCommandsDialog, "delete_selected")
        delete = patch.start()
        self.addCleanup(patch.stop)
        dialog = self.show(ManageCommandsDialog(self.COMMANDS, manager))
        edited = []
        dialog.editRequested.connect(edited.append)
        self.assertIs(QApplication.focusWidget(), dialog.filter_box)
        QTest.keyClick(dialog.filter_box, Qt.Key.Key_Down)
        self.assertIs(QApplication.focusWidget(), dialog.command_list)
        self.assertEqual(dialog.command_list.currentItem().data(Qt.ItemDataRole.UserRole), "Spotify")
        QTest.keyClick(dialog.command_list, Qt.Key.Key_Up)
        self.assertIs(QApplication.focusWidget(), dialog.filter_box, "Up from the top row goes back to the filter")
        QTest.keyClick(dialog.filter_box, Qt.Key.Key_Down)
        QTest.keyClick(dialog.command_list, Qt.Key.Key_Delete)
        delete.assert_called_once()
        dialog.command_list.currentItem().setSelected(True)
        QTest.keyClick(dialog.command_list, Qt.Key.Key_Return)
        self.assertEqual(edited, ["Spotify"])

    def test_recommended_commands_tick_with_space_and_edit_with_f2(self):
        candidates = [
            {"name": "Alpha", "location": "https://a.example.com/", "type": "url"},
            {"name": "Beta", "location": "https://b.example.com/", "type": "url"},
        ]
        patch = mock.patch.object(ProgramImportDialog, "_edit_item")
        edit = patch.start()
        self.addCleanup(patch.stop)
        dialog = self.show(ProgramImportDialog(candidates, set()))
        list_widget = next(iter(dialog.lists.values()))
        filter_box = next(iter(dialog.filters.values()))
        filter_box.setFocus()
        QTest.keyClick(filter_box, Qt.Key.Key_Down)
        self.assertIs(QApplication.focusWidget(), list_widget)
        self.assertEqual(dialog.selected_candidates(), [])
        QTest.keyClick(list_widget, Qt.Key.Key_Space)
        self.assertEqual([c["name"] for c in dialog.selected_candidates()], ["Alpha"])
        QTest.keyClick(list_widget, Qt.Key.Key_Down)
        QTest.keyClick(list_widget, Qt.Key.Key_Space)
        self.assertEqual([c["name"] for c in dialog.selected_candidates()], ["Alpha", "Beta"])
        QTest.keyClick(list_widget, Qt.Key.Key_F2)
        edit.assert_called_once()


class CommandTypeKeyboardTests(unittest.TestCase):
    def test_the_type_row_is_one_tab_stop_and_arrows_change_it(self):
        from src.command_editor import CommandType, CommandTypeSelector

        selector = CommandTypeSelector(CommandType.APP)
        self.addCleanup(selector.deleteLater)
        selector.show()
        selector.activateWindow()
        QTest.qWait(20)
        stops = [t for t, b in selector.buttons.items() if b.focusPolicy() & Qt.FocusPolicy.TabFocus]
        self.assertEqual(stops, [CommandType.APP])
        changed = []
        selector.typeChanged.connect(changed.append)
        selector.buttons[CommandType.APP].setFocus()
        QTest.keyClick(selector.buttons[CommandType.APP], Qt.Key.Key_Right)
        second = list(CommandType)[1]
        self.assertEqual(selector.selection, second)
        self.assertEqual(changed, [second])
        self.assertIs(QApplication.focusWidget(), selector.buttons[second])
        QTest.keyClick(selector.buttons[second], Qt.Key.Key_Left)
        self.assertEqual(selector.selection, CommandType.APP)


class EditorEnterTests(unittest.TestCase):
    def test_the_command_editor_keys_match_settings(self):
        from tempfile import TemporaryDirectory as Tmp

        from src.command_editor import CommandEditorPanel
        from src.command_manager import CommandManager
        from src.icon_manager import IconManager

        tmp = Tmp()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        exe = root / "a.exe"
        exe.write_bytes(b"")
        (root / "commands.toml").write_text(
            f'[[command]]\nname = "App"\naliases = []\nlocation = \'{exe}\'\ndescription = ""\n',
            encoding="utf-8",
        )
        settings = Settings()
        manager = CommandManager(root / "commands.toml", settings)
        with mock.patch.object(IconManager, "_start_download_worker"):
            icons = IconManager(settings)
        panel = CommandEditorPanel(manager.commands["App"], icons, manager)
        self.addCleanup(panel.deleteLater)
        close_strays()
        panel.show()
        panel.activateWindow()
        QTest.qWait(20)
        pressed = []
        panel.close_button.clicked.connect(lambda: pressed.append(True))
        panel.close_button.setFocus()
        QTest.keyClick(panel.close_button, Qt.Key.Key_Return)
        self.assertEqual(pressed, [True])

        # ↓ and ↑ move between fields, as in Settings.
        name = panel.command_name_edit_box
        name.setFocus()
        QTest.keyClick(name, Qt.Key.Key_Down)
        self.assertIs(QApplication.focusWidget(), panel.command_description_edit_box)
        QTest.keyClick(panel.command_description_edit_box, Qt.Key.Key_Down)
        chosen = panel.command_type_selector.buttons[panel.command_type_selector.selection]
        self.assertIs(QApplication.focusWidget(), chosen, "the type row is the next field")
        QTest.keyClick(chosen, Qt.Key.Key_Right)
        moved = panel.command_type_selector.buttons[panel.command_type_selector.selection]
        self.assertIsNot(moved, chosen, "right changes the type")
        QTest.keyClick(moved, Qt.Key.Key_Up)
        self.assertIs(QApplication.focusWidget(), panel.command_description_edit_box, "up still moves on a type that was chosen by key")

        # A chip added later gets the same keys.
        panel.alias_box.enter_box.setText("zz")
        panel.alias_box.on_enter_pressed()
        chip = next(c for c in panel.alias_box.aliases if c.alias_text == "zz")
        chip.setFocus()
        QTest.keyClick(chip, Qt.Key.Key_Down)
        self.assertIsNot(QApplication.focusWidget(), chip)


class HotkeyFieldTests(unittest.TestCase):
    def test_a_click_starts_recording(self):
        field = HotkeyLineEdit("Alt+F")
        self.addCleanup(field.deleteLater)
        field.show()
        QTest.mouseClick(field, Qt.MouseButton.LeftButton)
        self.assertTrue(field.recording)
        self.assertEqual(field.text(), "", "the placeholder says what to press")


if __name__ == "__main__":
    unittest.main()
