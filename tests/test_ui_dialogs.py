import base64
import sys
import unittest
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for Qt widget instantiation
app = QApplication.instance() or QApplication(sys.argv)

from src.settings import Settings
from src.settings_editor import ProgramImportDialog, SettingsEditorPanel, usage_sort_key, usage_summary
from src.command_editor import CommandEditorPanel, CommandType, suggested_description, suggested_name
from src.command_manager import CommandManager
from src.icon_manager import IconManager


# Smallest valid PNG, for standing in for a command's icon files on disk.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestUIDialogs(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.settings_path = Path("config/settings.toml")
        self.icon_manager = IconManager(self.settings)

    def test_settings_editor_dirty_state(self):
        panel = SettingsEditorPanel(self.settings, self.settings_path)
        # Initially clean: Close button should not be hidden, Save and Cancel hidden
        self.assertFalse(panel.close_button.isHidden())
        self.assertTrue(panel.cancel_button.isHidden())
        self.assertTrue(panel.save_button.isHidden())

        # Modify a setting (e.g. search font size)
        panel._controls["ui.search_font_size"].setValue(panel._controls["ui.search_font_size"].value() + 1)
        # Now dirty: Close button should be hidden, Save and Cancel visible
        self.assertTrue(panel.close_button.isHidden())
        self.assertFalse(panel.cancel_button.isHidden())
        self.assertFalse(panel.save_button.isHidden())

        # Revert change back
        panel._controls["ui.search_font_size"].setValue(panel._controls["ui.search_font_size"].value() - 1)
        self.assertFalse(panel.close_button.isHidden())
        self.assertTrue(panel.cancel_button.isHidden())
        self.assertTrue(panel.save_button.isHidden())

    def test_command_editor_dirty_state(self):
        cmd_manager = CommandManager(Path("config/commands.toml"), self.settings)
        cmd = {"name": "test_cmd", "location": "C:\\test.exe", "aliases": ["tc"], "type": "file"}
        panel = CommandEditorPanel(cmd, self.icon_manager, cmd_manager)

        # Initially clean: Close button should not be hidden, Save and Cancel hidden
        self.assertFalse(panel.close_button.isHidden())
        self.assertTrue(panel.cancel_button.isHidden())
        self.assertTrue(panel.save_button.isHidden())

        # Modify name
        panel.command_name_edit_box.setText("test_cmd_modified")
        self.assertTrue(panel.close_button.isHidden())
        self.assertFalse(panel.cancel_button.isHidden())
        self.assertFalse(panel.save_button.isHidden())

    def _new_command_panel(self):
        cmd_manager = CommandManager(Path("config/commands.toml"), self.settings)
        return CommandEditorPanel(None, self.icon_manager, cmd_manager)

    def test_new_command_prefills_name_and_description_from_target(self):
        panel = self._new_command_panel()
        location = panel.command_action.command_action_edit_box

        location.setText(r"C:\Tools\Some Tool - Shortcut.lnk")
        self.assertEqual(panel.command_name_edit_box.text(), "Some Tool")
        self.assertEqual(panel.command_description_edit_box.text(), "Opens Some Tool")

        # The offer follows the target until the user writes something.
        location.setText(r"C:\Tools\other.exe")
        self.assertEqual(panel.command_name_edit_box.text(), "other")
        self.assertEqual(panel.command_description_edit_box.text(), "Opens other")

        panel.command_type_selector.select(CommandType.FOLDER)
        location.setText(r"C:\Users\me\Projects")
        self.assertEqual(panel.command_name_edit_box.text(), "Projects")
        self.assertEqual(panel.command_description_edit_box.text(), "Opens the Projects folder")

        panel.command_type_selector.select(CommandType.URL)
        location.setText("https://www.github.com/CalemYoung/dash")
        self.assertEqual(panel.command_name_edit_box.text(), "github")
        self.assertEqual(panel.command_description_edit_box.text(), "Opens github.com")

    def test_prefill_leaves_what_the_user_wrote_alone(self):
        panel = self._new_command_panel()
        location = panel.command_action.command_action_edit_box

        panel.command_name_edit_box.setText("mine")
        location.setText(r"C:\Tools\other.exe")
        self.assertEqual(panel.command_name_edit_box.text(), "mine")
        self.assertEqual(panel.command_description_edit_box.text(), "Opens other")

        panel.command_description_edit_box.setText("My own words")
        location.setText(r"C:\Tools\another.exe")
        self.assertEqual(panel.command_name_edit_box.text(), "mine")
        self.assertEqual(panel.command_description_edit_box.text(), "My own words")

        # Clearing a field hands it back to the editor.
        panel.command_name_edit_box.clear()
        location.setText(r"C:\Tools\third.exe")
        self.assertEqual(panel.command_name_edit_box.text(), "third")
        self.assertEqual(panel.command_description_edit_box.text(), "My own words")

    def test_editing_a_command_never_renames_it_from_the_target(self):
        cmd_manager = CommandManager(Path("config/commands.toml"), self.settings)
        cmd = {"name": "test_cmd", "description": "Does things", "location": r"C:\test.exe", "aliases": [], "type": "file"}
        panel = CommandEditorPanel(cmd, self.icon_manager, cmd_manager)
        panel.command_action.command_action_edit_box.setText(r"C:\Tools\other.exe")
        self.assertEqual(panel.command_name_edit_box.text(), "test_cmd")
        self.assertEqual(panel.command_description_edit_box.text(), "Does things")

    def test_clearing_a_custom_icon_goes_back_to_the_commands_own(self):
        cmd_manager = CommandManager(Path("config/commands.toml"), self.settings)
        # The editor only keeps an icon it can show, so the files have to be there.
        stored = self.icon_manager.command_icon_path("icon_reset_cmd")
        source = self.icon_manager.command_source_icon_path("icon_reset_cmd")
        for path in (stored, source):
            path.write_bytes(ONE_PIXEL_PNG)
            self.addCleanup(path.unlink, True)
        cmd = {
            "name": "icon_reset_cmd",
            "location": r"C:	est.exe",
            "aliases": [],
            "type": "file",
            "icon": str(stored),
            "icon_source": str(source),
            "icon_color": "#ff0000",
        }
        panel = CommandEditorPanel(cmd, self.icon_manager, cmd_manager)
        self.assertEqual(panel._icon_path, str(stored))
        self.assertIsNotNone(panel._icon_recipe)

        panel._clear_icon()

        # Nothing of the user's icon is left to save...
        self.assertIsNone(panel._icon_path)
        self.assertIsNone(panel._icon_recipe)
        entry = panel._collect()
        self.assertIsNone(entry["icon"])
        self.assertNotIn("icon_source", entry)
        self.assertNotIn("icon_color", entry)
        # ...its files are queued to go once the command is saved without it...
        self.assertEqual(panel._dropped_icon_files, {str(stored), str(source)})
        # ...and dropping it is a change worth saving.
        self.assertFalse(panel.save_button.isHidden())

    def test_a_late_favicon_does_not_take_over_an_icon_of_your_own(self):
        cmd_manager = CommandManager(Path("config/commands.toml"), self.settings)
        stored = self.icon_manager.command_icon_path("icon_keep_cmd")
        stored.write_bytes(ONE_PIXEL_PNG)
        self.addCleanup(stored.unlink, True)
        cmd = {
            "name": "icon_keep_cmd",
            "location": "https://github.com/",
            "aliases": [],
            "type": "url",
            "icon": str(stored),
            "icon_glyph": "outline:BRAND_GITHUB",
        }
        panel = CommandEditorPanel(cmd, self.icon_manager, cmd_manager)

        # What the site resolves to, arriving after the editor opened.
        panel.command_action.iconResolved.emit(QIcon(self.settings.paths.url_command_icon))
        self.assertEqual(panel._icon_path, str(stored))
        self.assertIsNotNone(panel._icon_recipe)
        self.assertTrue(panel.save_button.isHidden())

        # Typing a new target is asking for that target's icon, though.
        panel.command_action.command_action_edit_box.setText("https://example.com/")
        panel.command_action.command_action_edit_box.textEdited.emit("https://example.com/")
        panel.command_action.iconResolved.emit(QIcon(self.settings.paths.url_command_icon))
        self.assertIsNone(panel._icon_path)
        self.assertIsNone(panel._icon_recipe)

    def test_icon_studio_only_offers_a_reset_when_there_is_one_to_make(self):
        from PyQt6.QtWidgets import QDialogButtonBox
        from src.icon_browser import IconStudio

        reset = QDialogButtonBox.StandardButton.Reset
        plain = IconStudio()
        self.assertIsNone(plain.findChild(QDialogButtonBox).button(reset))
        clearable = IconStudio(allow_clear=True)
        self.assertIsNotNone(clearable.findChild(QDialogButtonBox).button(reset))

    def test_suggested_names(self):
        cases = {
            (CommandType.APP, r"C:\Program Files\Microsoft Office\WINWORD.EXE"): "Word",
            (CommandType.APP, r"C:\Docs\report.docx"): "report",
            (CommandType.FOLDER, "C:\\"): "C:\\",
            (CommandType.URL, "https://mail.google.com/mail/u/0/"): "google",
            (CommandType.URL, "bbc.co.uk/news"): "bbc",
            (CommandType.URL, "http://localhost:8000/"): "localhost",
            (CommandType.URL, "http://192.168.1.1/"): "192.168.1.1",
            (CommandType.URL, "   "): "",
        }
        for (command_type, location), expected in cases.items():
            with self.subTest(location=location):
                self.assertEqual(suggested_name(command_type, location), expected)
        self.assertEqual(suggested_description(CommandType.URL, "bbc.co.uk/news", "bbc"), "Opens bbc.co.uk")
        self.assertEqual(suggested_description(CommandType.APP, "x", ""), "")

    def test_recommended_commands_are_most_used_first(self):
        # 2026-09-01 00:00:00 UTC; the row shows the local date, so only the year is checked.
        moment = 1_788_220_800
        candidates = [
            {"name": "Never used suggestion", "location": "https://a.example.com/", "type": "url", "suggested": True},
            {"name": "Never used", "location": "https://b.example.com/", "type": "url"},
            {"name": "Opened once recently", "location": "https://c.example.com/", "type": "url", "opened": 1, "last_opened": moment},
            {"name": "Opened often", "location": "https://d.example.com/", "type": "url", "opened": 30, "last_opened": moment - 86_400 * 30},
            {"name": "Only a date", "location": "https://e.example.com/", "type": "url", "last_opened": moment - 86_400},
            {"name": "Older date", "location": "https://f.example.com/", "type": "url", "last_opened": moment - 86_400 * 9},
        ]
        self.assertEqual(
            [c["name"] for c in sorted(candidates, key=usage_sort_key)],
            ["Opened often", "Opened once recently", "Only a date", "Older date", "Never used suggestion", "Never used"],
        )
        self.assertEqual(usage_summary(candidates[3])[:15], "Opened 30 times")
        self.assertIn("2026", usage_summary(candidates[3]))
        self.assertEqual(usage_summary(candidates[2])[:14], "Opened 1 time ")
        self.assertTrue(usage_summary(candidates[4]).startswith("Last opened "))
        self.assertEqual(usage_summary(candidates[1]), "")

        cmd_manager = CommandManager(Path("config/commands.toml"), self.settings)
        dialog = ProgramImportDialog(candidates, set(), self.icon_manager, command_manager=cmd_manager)
        websites = dialog.lists["website"]
        items = [websites.item(i) for i in range(websites.count())]
        rows = [dialog._row_widget(item) for item in items]
        names = [item.data(Qt.ItemDataRole.UserRole)["name"] for item in items]
        self.assertEqual(names[:2], ["Opened often", "Opened once recently"])
        self.assertTrue(rows[0].usage_label.text().startswith("Opened 30 times"))
        self.assertFalse(hasattr(rows[-1], "usage_label"))


if __name__ == "__main__":
    unittest.main()
