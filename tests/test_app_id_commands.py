"""Commands that start a Store app by app id rather than by a path."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.command_manager import CommandManager
from src.icon_manager import is_package_icon
from src.settings import Settings

CLAUDE = r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"


class AppIdCommandTests(unittest.TestCase):
    def _manager(self, root: Path) -> CommandManager:
        commands_path = root / "commands.toml"
        commands_path.write_text("", encoding="utf-8")
        return CommandManager(commands_path, Settings())

    def test_an_app_id_is_a_valid_app_target_but_not_a_folder(self):
        with TemporaryDirectory() as tmp_dir:
            manager = self._manager(Path(tmp_dir))
            self.assertIsNone(manager.validate_target({"name": "Claude", "location": CLAUDE, "type": "file", "command_type": "app"}))
            self.assertEqual(
                manager.validate_target({"name": "Claude", "location": CLAUDE, "type": "file", "command_type": "folder"}),
                "Choose an existing folder.",
            )

    def test_an_app_id_command_is_started_through_the_shell(self):
        with TemporaryDirectory() as tmp_dir:
            manager = self._manager(Path(tmp_dir))
            with (
                mock.patch("src.command_manager.switch_to_running", return_value=False),
                mock.patch("src.command_manager.os.startfile") as startfile,
            ):
                manager._open_file(Path(CLAUDE))
        startfile.assert_called_once_with(CLAUDE)

    def test_a_stored_app_id_is_not_offered_again(self):
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manager = self._manager(root)
            first = manager.import_program_commands(
                [{"name": "Claude", "aliases": [], "location": CLAUDE, "description": "Opens Claude", "type": "file"}]
            )
            self.assertEqual(first["imported"], ["Claude"])
            self.assertIn(CLAUDE.casefold(), manager.existing_command_locations())
            again = manager.import_program_commands(
                [{"name": "Claude 2", "aliases": [], "location": CLAUDE.upper(), "description": "", "type": "file"}]
            )
        self.assertEqual(again["skipped"], ["Claude 2"])

    def test_editor_opens_an_app_id_command_as_an_app(self):
        from src.command_editor import CommandType

        self.assertEqual(CommandType.from_command({"location": CLAUDE, "type": "file"}), CommandType.APP)
        with TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir) / "Projects"
            folder.mkdir()
            extensionless = Path(tmp_dir) / "LICENSE"
            extensionless.write_text("x", encoding="utf-8")
            self.assertEqual(CommandType.from_command({"location": str(folder), "type": "file"}), CommandType.FOLDER)
            self.assertEqual(CommandType.from_command({"location": str(extensionless), "type": "file"}), CommandType.APP)

    def test_candidates_named_like_an_existing_command_are_left_out(self):
        from src.installed_programs import drop_known_names

        candidates = [
            {"name": "DocuFind", "location": r"\\server\share\crawler_gui.exe"},
            {"name": "Claude", "location": CLAUDE},
        ]
        with TemporaryDirectory() as tmp_dir:
            manager = self._manager(Path(tmp_dir))
            manager.import_program_commands(
                [{"name": "DocuFind", "aliases": ["df"], "location": __file__, "description": "", "type": "file"}]
            )
            kept = drop_known_names(candidates, set(manager._reserved_keywords()))
        self.assertEqual([c["name"] for c in kept], ["Claude"])

    def test_package_icons_are_recognised(self):
        self.assertTrue(is_package_icon(r"C:\Program Files\WindowsApps\Claude_1.0_x64__abc\Assets\Square44x44Logo.png"))
        self.assertFalse(is_package_icon(r"C:\Users\me\AppData\Roaming\Dash\assets\icons\claude.png"))
        self.assertFalse(is_package_icon(None))


if __name__ == "__main__":
    unittest.main()
