"""The command editor: launch options, deleting, group hints, the prefill
API the launcher uses, accessibility and theme colors."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import QEvent, Qt, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

app = QApplication.instance() or QApplication(sys.argv)

from src import theme  # noqa: E402
from src.command_editor import CommandEditorPanel, CommandType  # noqa: E402
from src.command_manager import CommandManager, CommandsFileUnreadableError  # noqa: E402
from src.icon_manager import IconManager  # noqa: E402
from src.settings import Settings  # noqa: E402


def press(widget, key):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


class EditorFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tool = self.root / "tool.exe"
        self.tool.write_bytes(b"")
        self.projects = self.root / "Projects"
        self.projects.mkdir()
        self.commands_path = self.root / "commands.toml"
        self.commands_path.write_text(
            f"""[[command]]
name = "Tool"
aliases = ["t"]
location = '{self.tool}'
description = ""
arguments = "--fast"
working_folder = '{self.projects}'

[[command]]
name = "Jira"
aliases = []
location = 'https://jira.example.com'
description = ""
type = "url"

[[command]]
name = "Start day"
aliases = []
location = ""
description = ""
type = "group"
targets = ["Tool", "Jira"]

[[command]]
name = "Evening"
aliases = []
location = ""
description = ""
type = "group"
targets = ["Tool"]
""",
            encoding="utf-8",
        )
        worker = mock.patch.object(IconManager, "_start_download_worker")
        worker.start()
        self.addCleanup(worker.stop)
        self.settings = Settings()
        self.icon_manager = IconManager(self.settings)
        self.manager = CommandManager(self.commands_path, self.settings)

    def panel(self, name=None, **kwargs):
        command = self.manager.commands[name] if name else None
        return CommandEditorPanel(command, self.icon_manager, self.manager, **kwargs)


class TypeLabelTests(EditorFixture):
    def test_the_app_type_says_it_opens_files_too(self):
        panel = self.panel()
        self.assertEqual(panel.command_type_selector.buttons[CommandType.APP].text(), "App or file")
        self.assertEqual(CommandType.APP.to_stored_type(), "file", "the stored value is unchanged")


class LaunchOptionTests(EditorFixture):
    def test_an_app_shows_its_arguments_and_start_folder(self):
        panel = self.panel("Tool")
        action = panel.command_action
        self.assertTrue(action.launch_options.isVisibleTo(panel))
        self.assertEqual(action.arguments(), "--fast")
        self.assertEqual(action.working_folder(), str(self.projects))
        self.assertEqual(action.working_folder_edit.placeholderText(), "Default")
        self.assertFalse(panel._is_dirty())

    def test_other_types_and_windows_links_hide_them(self):
        panel = self.panel()
        action = panel.command_action
        for command_type in (CommandType.URL, CommandType.FOLDER, CommandType.GROUP):
            panel.command_type_selector.select(command_type)
            self.assertFalse(action.launch_options.isVisibleTo(panel), command_type)
        panel.command_type_selector.select(CommandType.APP)
        self.assertTrue(action.launch_options.isVisibleTo(panel))
        for link in ("ms-settings:display", "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"):
            action.command_action_edit_box.setText(link)
            self.assertFalse(action.launch_options.isVisibleTo(panel), link)

    def test_changing_them_makes_the_editor_dirty(self):
        panel = self.panel("Tool")
        panel.command_action.arguments_edit.setText("--slow")
        self.assertTrue(panel._is_dirty())
        self.assertFalse(panel.save_button.isHidden())
        panel.command_action.arguments_edit.setText("--fast")
        self.assertFalse(panel._is_dirty())
        panel.command_action.working_folder_edit.setText("")
        self.assertTrue(panel._is_dirty())

    def test_saving_sends_them_and_empty_clears(self):
        panel = self.panel("Tool")
        panel.command_action.arguments_edit.setText("")
        panel.command_action.working_folder_edit.setText(str(self.root))
        panel._save_and_close()
        saved = self.manager.commands["Tool"]
        self.assertNotIn("arguments", {k for k, v in saved.items() if v})
        self.assertEqual(saved.get("working_folder"), str(self.root))

    def test_a_missing_start_folder_is_reported_on_that_field(self):
        panel = self.panel("Tool")
        panel.command_action.working_folder_edit.setText(str(self.root / "nowhere"))
        closed = mock.Mock()
        panel.closed.connect(closed)
        with mock.patch.object(panel.command_action.working_folder_edit, "setFocus") as focus:
            panel._save_and_close()
        closed.assert_not_called()
        focus.assert_called_once()
        self.assertIn("working folder", panel.validation_message.text())

    def test_a_folder_leaves_stored_options_to_the_manager(self):
        panel = self.panel()
        panel.command_type_selector.select(CommandType.FOLDER)
        panel.command_action.command_action_edit_box.setText(str(self.projects))
        panel.command_name_edit_box.setText("Projects")
        entry = panel._collect()
        self.assertNotIn("arguments", entry)
        self.assertNotIn("working_folder", entry)

    def test_a_shortcuts_program_is_kept_while_its_location_is(self):
        shortcut = self.root / "Discord.lnk"
        shortcut.write_bytes(b"")
        command = {"name": "Discord", "aliases": [], "location": str(shortcut), "description": "", "type": "file", "process_path": str(self.tool)}
        panel = CommandEditorPanel(command, self.icon_manager, self.manager, standalone=True)
        self.assertEqual(panel._collect()["process_path"], str(self.tool))
        panel.command_action.command_action_edit_box.setText(str(self.tool))
        self.assertNotIn("process_path", panel._collect())


class DeleteTests(EditorFixture):
    def test_the_question_names_the_groups_that_lose_it(self):
        question, detail = self.panel("Tool").delete_question()
        self.assertEqual(question, "Delete Tool?")
        self.assertEqual(detail, "It will also be removed from the groups: Start day, Evening.")
        question, detail = self.panel("Jira").delete_question()
        self.assertEqual(detail, "It will also be removed from the group: Start day.")
        self.assertEqual(self.panel("Evening").delete_question(), ("Delete Evening?", ""))

    def test_cancel_is_the_default_and_nothing_is_deleted(self):
        panel = self.panel("Tool")
        seen = {}

        def answer():
            box = panel._delete_box
            seen["default"] = box.defaultButton()
            seen["text"] = box.text()
            seen["detail"] = box.informativeText()
            return int(QMessageBox.StandardButton.Cancel)

        with mock.patch.object(QMessageBox, "exec", side_effect=answer):
            panel._delete_and_close()
        self.assertIn("Tool", self.manager.commands)
        self.assertEqual(seen["default"].text().replace("&", ""), "Cancel")
        self.assertEqual(seen["text"], "Delete Tool?")
        self.assertIn("Start day", seen["detail"])

    def test_confirming_deletes(self):
        panel = self.panel("Jira")
        closed = mock.Mock()
        panel.closed.connect(closed)
        with mock.patch.object(CommandEditorPanel, "_confirm_delete", return_value=True):
            panel._delete_and_close()
        self.assertNotIn("Jira", self.manager.commands)
        closed.assert_called_once()

    def test_a_failed_delete_is_shown_and_keeps_the_editor_open(self):
        panel = self.panel("Jira")
        closed = mock.Mock()
        panel.closed.connect(closed)
        with (
            mock.patch.object(CommandEditorPanel, "_confirm_delete", return_value=True),
            mock.patch.object(self.manager, "delete_command", side_effect=CommandsFileUnreadableError("commands.toml could not be read")),
            mock.patch.object(self.icon_manager, "delete_command_icon") as delete_icon,
        ):
            panel._delete_and_close()
        closed.assert_not_called()
        delete_icon.assert_not_called()
        self.assertFalse(panel.validation_message.isHidden())
        self.assertIn("commands.toml could not be read", panel.validation_message.text())

    def test_a_failed_save_is_shown_and_keeps_the_editor_open(self):
        panel = self.panel("Jira")
        panel.command_description_edit_box.setText("Tickets")
        closed = mock.Mock()
        panel.closed.connect(closed)
        with mock.patch.object(self.manager, "save_command", side_effect=PermissionError("Access is denied")):
            panel._save_and_close()
        closed.assert_not_called()
        self.assertIn("Access is denied", panel.validation_message.text())


class GroupHintTests(EditorFixture):
    def test_a_group_without_a_description_hints_at_what_it_opens(self):
        panel = self.panel("Start day")
        self.assertEqual(panel.command_description_edit_box.placeholderText(), "Opens: Tool, Jira")
        self.assertEqual(self.panel("Tool").command_description_edit_box.placeholderText(), "Description")
        self.assertEqual(self.panel().command_description_edit_box.placeholderText(), "Description")


class PrefillTests(EditorFixture):
    def test_a_name_alone_makes_a_dirty_new_command(self):
        panel = self.panel()
        panel.prefill(name="spotify")
        self.assertEqual(panel.command_name_edit_box.text(), "spotify")
        self.assertFalse(panel.save_button.isHidden())
        self.assertEqual(panel.command_type_selector.selection, CommandType.APP)

    def test_a_web_address_switches_to_url(self):
        panel = self.panel()
        panel.prefill(target="www.example.com")
        self.assertEqual(panel.command_type_selector.selection, CommandType.URL)
        self.assertEqual(panel.command_action.command_action_edit_box.text(), "https://www.example.com")
        self.assertEqual(panel.command_name_edit_box.text(), "example")

    def test_a_folder_switches_to_folder(self):
        panel = self.panel()
        panel.prefill(target=f'"{self.projects}"')
        self.assertEqual(panel.command_type_selector.selection, CommandType.FOLDER)
        self.assertEqual(panel.command_action.command_action_edit_box.text(), str(self.projects))
        self.assertFalse(panel.save_button.isHidden())

    def test_a_dropped_file_url_becomes_its_path(self):
        document = self.root / "notes.txt"
        document.write_text("x")
        panel = self.panel()
        panel.prefill(target=QUrl.fromLocalFile(str(document)).toString())
        self.assertEqual(panel.command_type_selector.selection, CommandType.APP)
        self.assertEqual(panel.command_action.command_action_edit_box.text(), str(document))

    def test_a_given_name_beats_the_suggestion_and_stays(self):
        panel = self.panel()
        panel.prefill(name="Work", target=str(self.projects))
        self.assertEqual(panel.command_name_edit_box.text(), "Work")
        self.assertTrue(panel.command_description_edit_box.text(), "the description is still suggested")
        panel.command_action.command_action_edit_box.setText(str(self.root))
        self.assertEqual(panel.command_name_edit_box.text(), "Work")

    def test_a_clashing_name_is_flagged_at_once(self):
        panel = self.panel()
        panel.prefill(name="Tool")
        self.assertFalse(panel.validation_message.isHidden())


class AccessibilityTests(EditorFixture):
    def test_controls_without_a_visible_label_have_names(self):
        panel = self.panel("Tool")
        action = panel.command_action
        for widget in (
            panel._command_icon,
            panel.command_name_edit_box,
            action.status_dot,
            action.browse_button,
            action.working_folder_button,
            panel.alias_box.enter_box,
            action.targets_box.enter_box,
            panel.command_type_selector,
        ):
            self.assertTrue(widget.accessibleName(), widget)

    def test_field_labels_are_buddies(self):
        panel = self.panel("Tool")
        buddies = {label.text(): label.buddy() for label in panel.findChildren(QLabel) if label.buddy() is not None}
        action = panel.command_action
        self.assertIs(buddies["Description"], panel.command_description_edit_box)
        self.assertIs(buddies["Aliases"], panel.alias_box.enter_box)
        self.assertIs(buddies["Arguments"], action.arguments_edit)
        self.assertIs(buddies["Start in"], action.working_folder_edit)
        self.assertIs(buddies["App or file to open"], action.command_action_edit_box)

    def test_an_alias_chip_is_removed_from_the_keyboard(self):
        panel = self.panel("Tool")
        chip = panel.alias_box.aliases[0]
        self.assertEqual(chip.accessibleName(), "Alias t")
        self.assertNotEqual(chip.focusPolicy(), Qt.FocusPolicy.NoFocus)
        press(chip, Qt.Key.Key_Delete)
        self.assertEqual(panel.alias_box.alias_texts(), [])
        self.assertTrue(panel._is_dirty())

    def test_tab_reaches_the_chips_before_the_entry_box(self):
        panel = self.panel("Tool")
        panel.alias_box.enter_box.setText("tool2")
        panel.alias_box.on_enter_pressed()
        chips = panel.alias_box.aliases
        chain = []
        widget: QWidget = panel._command_icon
        for _ in range(200):
            widget = widget.nextInFocusChain()
            if widget is panel._command_icon:
                break
            chain.append(widget)
        positions = [chain.index(chip) for chip in chips] + [chain.index(panel.alias_box.enter_box)]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(chain.index(panel.alias_box.enter_box), chain.index(panel.close_button))
        self.assertLess(chain.index(panel.command_name_edit_box), chain.index(chips[0]))


class ThemeTests(EditorFixture):
    def tearDown(self):
        theme.reset_for_tests()
        super().tearDown()

    def test_the_status_dot_follows_the_theme(self):
        panel = self.panel("Tool")
        action = panel.command_action
        self.assertEqual(action._status_state, "ok")
        self.assertIn(theme.DARK["status_ok"], action.status_dot.styleSheet())
        theme._state.update(palette=theme.LIGHT, name="light")
        theme.notifier().changed.emit("light")
        self.assertIn(theme.LIGHT["status_ok"], action.status_dot.styleSheet())


if __name__ == "__main__":
    unittest.main()
