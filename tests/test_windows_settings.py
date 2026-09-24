import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src import windows_settings
from src.command_manager import CommandManager
from src.installed_programs import command_name_for_link, drop_known_names, is_app_id_location, is_link_location
from src.settings import Settings


class SettingsPageDiscoveryTests(unittest.TestCase):
    def test_pages_this_windows_lacks_are_left_out_and_the_home_page_always_stays(self):
        with mock.patch.object(windows_settings, "available_pages", return_value=frozenset({"display", "bluetooth"})):
            names = [candidate["name"] for candidate in windows_settings.discover_settings_pages()]
        self.assertEqual(names, ["Windows Settings", "Display", "Bluetooth & devices"])

    def test_every_page_is_offered_when_the_settings_program_cannot_be_read(self):
        with mock.patch.object(windows_settings, "available_pages", return_value=None):
            candidates = windows_settings.discover_settings_pages()
        self.assertEqual(len(candidates), len(windows_settings.SETTINGS_PAGES))

    def test_a_page_is_a_link_command_with_a_glyph_icon(self):
        with mock.patch.object(windows_settings, "available_pages", return_value=None):
            display = next(c for c in windows_settings.discover_settings_pages() if c["name"] == "Display")
        self.assertEqual(display["location"], "ms-settings:display")
        self.assertEqual(display["type"], "file")
        self.assertEqual(display["description"], "Opens Display in Windows Settings")
        self.assertEqual(display["icon_glyph"], "outline:DEVICE_DESKTOP")
        self.assertTrue(display["icon_color"] and display["icon_background"])

    def test_available_pages_are_read_from_the_settings_program(self):
        with TemporaryDirectory() as tmp_dir:
            library = Path(tmp_dir) / "SystemSettings.dll"
            library.write_bytes(b"MZ\x00" + "ms-settings:display".encode("utf-16-le") + b"\x00\x00junk" + "ms-settings:Network-WiFi".encode("utf-16-le"))
            windows_settings.available_pages.cache_clear()
            try:
                with mock.patch.object(windows_settings, "settings_library_path", return_value=library):
                    pages = windows_settings.available_pages()
            finally:
                windows_settings.available_pages.cache_clear()
        self.assertEqual(pages, frozenset({"display", "network-wifi"}))

    def test_the_listed_glyphs_and_keywords_are_valid(self):
        from pytablericons import OutlineIcon

        keywords = [keyword.casefold() for name, _page, aliases, _glyph in windows_settings.SETTINGS_PAGES for keyword in (name, *aliases)]
        self.assertEqual(len(keywords), len(set(keywords)), "a name or alias is listed twice")
        for _name, _page, _aliases, glyph in windows_settings.SETTINGS_PAGES:
            self.assertIn(glyph, OutlineIcon.__members__)

    def test_page_names(self):
        self.assertEqual(windows_settings.settings_page_name("ms-settings:display"), "Display")
        self.assertEqual(windows_settings.settings_page_name("MS-SETTINGS:Bluetooth"), "Bluetooth & devices")
        self.assertEqual(windows_settings.settings_page_name("ms-settings:privacy-contacts"), "Privacy contacts")
        self.assertEqual(windows_settings.settings_page_name("ms-settings:"), "Windows Settings")

    def test_page_names_use_us_spelling_with_british_aliases(self):
        pages = {name: aliases for name, _page, aliases, _glyph in windows_settings.SETTINGS_PAGES}
        self.assertEqual(windows_settings.settings_page_name("ms-settings:personalization"), "Personalization")
        self.assertEqual(windows_settings.settings_page_name("ms-settings:colors"), "Colors")
        self.assertIn("personalisation", pages["Personalization"])
        self.assertIn("colours", pages["Colors"])
        self.assertFalse([name for name in pages if "colour" in name.casefold() or "isation" in name.casefold()])


class LinkLocationTests(unittest.TestCase):
    def test_links_and_paths_are_told_apart(self):
        for link in ("ms-settings:display", "ms-settings:", "shell:startup", r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"):
            with self.subTest(link=link):
                self.assertTrue(is_link_location(link))
        for path in (r"C:\Tools\tool.exe", "C:", "d:relative", r"\\server\share", "https://example.com", "", "notes.txt"):
            with self.subTest(path=path):
                self.assertFalse(is_link_location(path))
        self.assertTrue(is_app_id_location(r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"))
        self.assertFalse(is_app_id_location("shell:startup"))

    def test_link_names(self):
        self.assertEqual(command_name_for_link("ms-settings:sound"), "Sound")
        self.assertEqual(command_name_for_link(r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"), "Claude")
        self.assertEqual(command_name_for_link(r"shell:AppsFolder\Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"), "WindowsTerminal")
        self.assertEqual(command_name_for_link("shell:startup"), "startup")

    def test_a_taken_alias_is_dropped_but_the_recommendation_stays(self):
        candidates = [{"name": "Sound", "aliases": ["volume", "audio"]}, {"name": "Taken", "aliases": []}]
        kept = drop_known_names(candidates, {"volume", "taken"})
        self.assertEqual(kept, [{"name": "Sound", "aliases": ["audio"]}])
        self.assertEqual(candidates[0]["aliases"], ["volume", "audio"], "the original is not changed")


class SettingsCommandTests(unittest.TestCase):
    def test_a_settings_page_saves_imports_and_opens_as_a_link(self):
        with TemporaryDirectory() as tmp_dir:
            commands_path = Path(tmp_dir) / "commands.toml"
            commands_path.write_text("", encoding="utf-8")
            manager = CommandManager(commands_path, Settings())
            page = {"name": "Display", "aliases": [], "location": "ms-settings:display", "description": "", "type": "file"}
            self.assertIsNone(manager.validate_command({**page, "command_type": "app"}))
            self.assertEqual(manager.import_program_commands([page])["imported"], ["Display"])
            self.assertEqual(manager.import_program_commands([{**page, "name": "Screen"}])["skipped"], ["Screen"])
            with mock.patch("src.command_manager.os.startfile") as startfile:
                self.assertIsNone(manager.execute_command(None, "Display"))
        startfile.assert_called_once_with("ms-settings:display")


if __name__ == "__main__":
    unittest.main()
