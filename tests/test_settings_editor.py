"""The Settings page: theme, size presets, web search choice, new switches,
and the command dialogs it opens."""
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFormLayout

app = QApplication.instance() or QApplication(sys.argv)

from src import settings_editor  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.settings_editor import (  # noqa: E402
    SIZE_PRESETS,
    WEB_SEARCH_CUSTOM,
    WEB_SEARCH_PRESETS,
    ManageCommandsDialog,
    ProgramImportDialog,
    SettingsEditorPanel,
    delete_confirmation_text,
    recently_used,
    size_preset,
    web_search_preset,
    web_search_problem,
)


def _row_visible(control) -> bool:
    form = control.parentWidget().layout()
    assert isinstance(form, QFormLayout)
    return form.isRowVisible(control)


class WebSearchPresetTests(unittest.TestCase):
    def test_every_preset_address_maps_back_to_its_preset(self):
        for key, _label, address in WEB_SEARCH_PRESETS:
            with self.subTest(key=key):
                self.assertEqual(web_search_preset(address), key)

    def test_spelling_differences_still_match(self):
        self.assertEqual(web_search_preset("http://google.com/search?q={query}"), "google")
        self.assertEqual(web_search_preset("  HTTPS://www.Bing.com/search?q={query} "), "bing")

    def test_anything_else_is_custom(self):
        self.assertEqual(web_search_preset("https://example.com/?s={query}"), WEB_SEARCH_CUSTOM)
        self.assertEqual(web_search_preset(""), WEB_SEARCH_CUSTOM)

    def test_custom_addresses_need_a_scheme_and_the_query_marker(self):
        self.assertIsNone(web_search_problem("https://example.com/?s={query}"))
        self.assertIsNone(web_search_problem("http://example.com/{query}"))
        self.assertIn("http", web_search_problem("example.com/?s={query}"))
        self.assertIn("{query}", web_search_problem("https://example.com/?s="))
        self.assertIsNotNone(web_search_problem("   "))


class SizePresetTests(unittest.TestCase):
    def test_the_shipped_sizes_are_medium(self):
        self.assertEqual(size_preset(Settings().ui), "medium")

    def test_each_preset_is_recognized(self):
        for name, values in SIZE_PRESETS.items():
            with self.subTest(preset=name):
                self.assertEqual(size_preset(values), name)

    def test_any_difference_is_custom(self):
        values = dict(SIZE_PRESETS["medium"], result_font_size=15)
        self.assertEqual(size_preset(values), "custom")

    def test_presets_grow_from_small_to_large(self):
        for key in SIZE_PRESETS["medium"]:
            with self.subTest(key=key):
                self.assertLess(SIZE_PRESETS["small"][key], SIZE_PRESETS["medium"][key])
                self.assertLess(SIZE_PRESETS["medium"][key], SIZE_PRESETS["large"][key])


class SettingsPanelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings_path = Path(self._tmp.name) / "settings.toml"
        self.settings = Settings()

    def _panel(self):
        return SettingsEditorPanel(self.settings, self.settings_path)

    def test_it_starts_clean_with_the_defaults(self):
        panel = self._panel()
        self.assertFalse(panel._is_dirty())
        self.assertTrue(panel.save_button.isHidden())

    def test_it_starts_clean_with_unusual_stored_values(self):
        self.settings.general.web_search = "http://google.com/search?q={query}"
        self.settings.ui.theme = "light"
        self.settings.ui.program_width = 777
        self.settings.ui.search_text_color = "#123456"
        self.assertFalse(self._panel()._is_dirty())

        self.settings.general.web_search = "https://example.com/?s={query}"
        panel = self._panel()
        self.assertFalse(panel._is_dirty())
        self.assertEqual(panel._controls["general.web_search"].currentData(), WEB_SEARCH_CUSTOM)
        self.assertTrue(_row_visible(panel._web_search_custom))

    def test_the_new_settings_are_collected(self):
        panel = self._panel()
        controls = panel._controls
        controls["general.hide_when_focus_lost"].setChecked(not self.settings.general.hide_when_focus_lost)
        controls["general.download_favicons"].setChecked(not self.settings.general.download_favicons)
        controls["search.match_word_starts"].setChecked(not self.settings.search.match_word_starts)
        controls["ui.theme"].setCurrentIndex(controls["ui.theme"].findData("light"))
        controls["general.web_search"].setCurrentIndex(controls["general.web_search"].findData("duckduckgo"))

        collected = panel._collect()
        self.assertEqual(collected.general.hide_when_focus_lost, not self.settings.general.hide_when_focus_lost)
        self.assertEqual(collected.general.download_favicons, not self.settings.general.download_favicons)
        self.assertEqual(collected.search.match_word_starts, not self.settings.search.match_word_starts)
        self.assertEqual(collected.ui.theme, "light")
        self.assertEqual(collected.general.web_search, "https://duckduckgo.com/?q={query}")
        self.assertTrue(panel._is_dirty())

    def test_a_custom_search_address_is_checked_before_saving(self):
        panel = self._panel()
        choice = panel._controls["general.web_search"]
        self.assertFalse(_row_visible(panel._web_search_custom))
        choice.setCurrentIndex(choice.findData(WEB_SEARCH_CUSTOM))
        self.assertTrue(_row_visible(panel._web_search_custom))

        panel._web_search_custom.setText("example.com/?s={query}")
        self.assertTrue(_row_visible(panel._web_search_problem))
        self.assertFalse(panel.save_button.isEnabled())

        panel._web_search_custom.setText("https://example.com/?s={query}")
        self.assertFalse(_row_visible(panel._web_search_problem))
        self.assertTrue(panel.save_button.isEnabled())
        self.assertEqual(panel._collect().general.web_search, "https://example.com/?s={query}")

    def test_a_size_choice_sets_every_size_and_hides_the_pixel_boxes(self):
        panel = self._panel()
        width = panel._controls["ui.program_width"]
        self.assertFalse(_row_visible(width), "Medium hides the pixel boxes")

        panel._size_choice.setCurrentIndex(panel._size_choice.findData("large"))
        collected = panel._collect().ui
        for key, value in SIZE_PRESETS["large"].items():
            self.assertEqual(getattr(collected, key), value, key)
        self.assertFalse(_row_visible(width))

        panel._size_choice.setCurrentIndex(panel._size_choice.findData("custom"))
        self.assertTrue(_row_visible(width))
        self.assertEqual(panel._collect().ui.program_width, SIZE_PRESETS["large"]["program_width"], "Custom keeps the numbers")

    def test_changing_a_text_size_by_hand_makes_the_size_custom(self):
        panel = self._panel()
        panel._controls["ui.result_font_size"].setValue(15)
        self.assertEqual(panel._size_choice.currentData(), "custom")
        self.assertTrue(_row_visible(panel._controls["ui.program_width"]))

    def test_non_preset_sizes_open_as_custom(self):
        self.settings.ui.program_width = 777
        panel = self._panel()
        self.assertEqual(panel._size_choice.currentData(), "custom")
        self.assertTrue(_row_visible(panel._controls["ui.program_width"]))

    def test_a_clashing_hotkey_shows_a_warning(self):
        warn = lambda hotkey: "Windows uses this one" if hotkey == "Alt+Space" else None  # noqa: E731
        with mock.patch.object(settings_editor, "_hotkey_conflict_warning", warn):
            self.settings.general.hotkey = "Alt+Space"
            panel = self._panel()
            self.assertTrue(_row_visible(panel._hotkey_warning))
            self.assertEqual(panel._hotkey_warning.text(), "Windows uses this one")
            panel._controls["general.hotkey"].setText("Alt+F")
            self.assertFalse(_row_visible(panel._hotkey_warning))

    def test_no_warning_without_the_clash_check(self):
        with mock.patch.object(settings_editor, "_hotkey_conflict_warning", None):
            panel = self._panel()
            self.assertFalse(_row_visible(panel._hotkey_warning))

    def test_saving_a_new_theme_re_themes_the_app(self):
        panel = self._panel()
        panel._controls["ui.theme"].setCurrentIndex(panel._controls["ui.theme"].findData("dark"))
        heard = []
        panel.themeChanged.connect(heard.append)
        with mock.patch.object(settings_editor.theme, "apply_theme", return_value="dark") as apply_theme:
            panel._save()
        apply_theme.assert_called_once()
        self.assertEqual(apply_theme.call_args.args[1].ui.theme, "dark")
        self.assertEqual(heard, ["dark"])
        self.assertEqual(Settings.load(self.settings_path).ui.theme, "dark")

    def test_saving_without_a_theme_change_leaves_the_style_alone(self):
        panel = self._panel()
        panel._controls["search.match_word_starts"].toggle()
        with mock.patch.object(settings_editor.theme, "apply_theme") as apply_theme:
            panel._save()
        apply_theme.assert_not_called()

    def test_every_control_has_an_accessible_name(self):
        panel = self._panel()
        for key, control in panel._controls.items():
            with self.subTest(key=key):
                self.assertTrue(control.accessibleName())
        self.assertEqual(panel._controls["ui.search_text_color"].accessibleName(), "Search box text color")

    def test_labels_use_us_spelling(self):
        from PyQt6.QtWidgets import QLabel

        panel = self._panel()
        texts = " ".join(label.text() for label in panel.findChildren(QLabel))
        self.assertIn("Ignore capitalization", texts)
        self.assertNotIn("colour", texts.lower())
        self.assertNotIn("\u2014", texts)


class RecommendedCommandTests(unittest.TestCase):
    def test_recently_used_needs_a_recent_date(self):
        now = time.time()
        self.assertTrue(recently_used({"last_opened": now - 86_400}, now))
        self.assertFalse(recently_used({"last_opened": now - 86_400 * 365}, now))
        self.assertFalse(recently_used({"opened": 40}, now), "a count alone may be old")
        self.assertFalse(recently_used({}, now))

    def test_recently_used_candidates_start_ticked(self):
        now = time.time()
        candidates = [
            {"name": "Recent", "location": "https://a.example.com/", "type": "url", "opened": 3, "last_opened": now - 3600},
            {"name": "Old", "location": "https://b.example.com/", "type": "url", "opened": 9, "last_opened": now - 86_400 * 400},
            {"name": "Never", "location": "https://c.example.com/", "type": "url"},
        ]
        dialog = ProgramImportDialog(candidates, set())
        self.assertEqual([c["name"] for c in dialog.selected_candidates()], ["Recent"])

        dialog._set_visible_checked(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selected_candidates(), [])
        dialog.select_recently_used()
        self.assertEqual([c["name"] for c in dialog.selected_candidates()], ["Recent"])


class ManageCommandsTests(unittest.TestCase):
    COMMANDS = [
        {"name": "Spotify", "location": r"C:\Apps\Spotify.exe", "type": "file", "aliases": ["music"]},
        {"name": "Steam", "location": r"C:\Apps\Steam.exe", "type": "file", "aliases": []},
        {"name": "Start work", "type": "group", "targets": ["Spotify", "Steam"], "aliases": []},
        {"name": "Quit Dash", "type": "system", "aliases": []},
    ]

    def _dialog(self, manager=None):
        manager = manager or mock.Mock(spec=["delete_command", "commands"])
        manager.commands = {c["name"]: c for c in self.COMMANDS}
        return ManageCommandsDialog(self.COMMANDS, manager), manager

    def _select(self, dialog, *names):
        for item in dialog._items():
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in names)

    def test_system_commands_are_not_listed(self):
        dialog, _ = self._dialog()
        self.assertEqual([i.data(Qt.ItemDataRole.UserRole) for i in dialog._items()], ["Spotify", "Start work", "Steam"])

    def test_the_filter_matches_aliases_and_drops_hidden_rows_from_the_selection(self):
        dialog, _ = self._dialog()
        self._select(dialog, "Spotify", "Steam")
        dialog.filter_box.setText("music")
        self.assertEqual(dialog.selected_names(), ["Spotify"])
        self.assertTrue(dialog.edit_button.isEnabled())

    def test_edit_emits_the_name(self):
        dialog, _ = self._dialog()
        heard = []
        dialog.editRequested.connect(heard.append)
        self._select(dialog, "Steam")
        dialog.edit_button.click()
        self.assertEqual(heard, ["Steam"])

    def test_deleting_asks_first_then_removes_every_selected_command(self):
        dialog, manager = self._dialog()
        deleted = []
        dialog.commandsDeleted.connect(deleted.append)
        self._select(dialog, "Spotify", "Steam")
        with mock.patch.object(dialog, "_confirm_delete", return_value=False):
            dialog.delete_selected()
        manager.delete_command.assert_not_called()

        with mock.patch.object(dialog, "_confirm_delete", return_value=True):
            dialog.delete_selected()
        self.assertEqual([c.args[0] for c in manager.delete_command.call_args_list], ["Spotify", "Steam"])
        self.assertEqual(deleted, [["Spotify", "Steam"]])
        self.assertEqual([i.data(Qt.ItemDataRole.UserRole) for i in dialog._items()], ["Start work"])

    def test_groups_are_found_with_or_without_the_manager_helper(self):
        _, manager = self._dialog()
        self.assertEqual(settings_editor.groups_containing(manager, "Steam"), ["Start work"])
        manager.groups_containing = lambda name: ["From the manager"]
        self.assertEqual(settings_editor.groups_containing(manager, "Steam"), ["From the manager"])

    def test_the_confirmation_counts_and_names_the_groups(self):
        text = delete_confirmation_text(["Spotify", "Steam"], {"Spotify": ["Start work"], "Steam": ["Start work"]})
        self.assertTrue(text.startswith("Delete 2 commands?"))
        self.assertIn("Start work: Spotify, Steam", text)

        single = delete_confirmation_text(["Steam"], {"Steam": ["Start work"]})
        self.assertTrue(single.startswith('Delete "Steam"?'))
        self.assertIn("Start work", single)

        self.assertNotIn("Start work", delete_confirmation_text(["Steam", "Start work"], {"Steam": ["Start work"]}),
                         "a group being deleted too is not a warning")


if __name__ == "__main__":
    unittest.main()
