import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.command_manager import CommandManager
from src.settings import Settings


class GroupCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tool = self.root / "tool.exe"
        self.tool.write_bytes(b"")
        self.commands_path = self.root / "commands.toml"
        self.commands_path.write_text(
            f"""[[command]]
name = "Tool"
aliases = []
location = '{self.tool}'
description = ""

[[command]]
name = "Site"
aliases = []
location = 'https://example.com/'
description = ""
type = "url"
""",
            encoding="utf-8",
        )
        self.manager = CommandManager(self.commands_path, Settings())

    def tearDown(self):
        self._tmp.cleanup()

    def _group(self, name="Start day", targets=("Tool", "Site")):
        return {"name": name, "aliases": [], "location": "", "description": "", "type": "group", "targets": list(targets)}

    def test_a_group_is_saved_and_loaded_with_its_targets_in_order(self):
        self.manager.save_command(self._group(targets=("Site", "Tool")))
        stored = tomllib.loads(self.commands_path.read_text(encoding="utf-8"))["command"][-1]
        self.assertEqual((stored["type"], stored["targets"], stored["location"]), ("group", ["Site", "Tool"], ""))
        self.assertEqual(self.manager.commands["Start day"]["targets"], ["Site", "Tool"])

    def test_a_group_needs_real_targets_and_cannot_contain_itself(self):
        self.assertEqual(self.manager.validate_command(self._group(targets=())), "Add at least one command for the group to open.")
        self.assertEqual(self.manager.validate_command(self._group(targets=("Nope",))), "'Nope' is not one of your commands.")
        self.assertEqual(self.manager.validate_command(self._group(targets=("Start day",))), "A group can't open itself.")
        self.assertIsNone(self.manager.validate_command(self._group(targets=("tool", "Dash Settings"))))

    def test_opening_a_group_opens_each_command_and_reports_every_failure(self):
        self.manager.save_command(self._group(targets=("Tool", "Site", "Tool")))
        self.tool.unlink()
        with (
            mock.patch("src.command_manager.switch_to_running", return_value=False),
            mock.patch("src.command_manager.os.startfile"),
            mock.patch.object(self.manager, "_open_url") as open_url,
        ):
            error = self.manager.execute_command(None, "Start day")
        open_url.assert_called_once_with("https://example.com/", "default")
        self.assertEqual(error.count("Tool: The target no longer exists"), 2)
        self.assertEqual(self.manager.run_counts.get("Start day"), 1)
        self.assertEqual(self.manager.run_counts.get("Site"), 1)

    def test_groups_inside_each_other_stop_instead_of_looping(self):
        self.manager.save_command(self._group("A", targets=("Tool",)))
        self.manager.save_command(self._group("B", targets=("A", "Site")))
        self.manager.save_command(self._group("A", targets=("B",)), original_name="A")
        with mock.patch.object(self.manager, "_open_url") as open_url:
            error = self.manager.execute_command(None, "A")
        open_url.assert_called_once()
        self.assertEqual(error, "A: skipped, it contains this group.")

    def test_renaming_a_command_updates_the_groups_that_open_it(self):
        self.manager.save_command(self._group(targets=("Tool", "Site")))
        tool = {"name": "Toolbox", "aliases": [], "location": str(self.tool), "description": "", "type": "file"}
        self.manager.save_command(tool, original_name="Tool")
        self.assertEqual(self.manager.commands["Start day"]["targets"], ["Toolbox", "Site"])

    def test_export_and_import_carry_the_targets(self):
        self.manager.save_command(self._group())
        exported = self.root / "export.toml"
        self.manager.export_commands(["Start day"], exported)
        other_path = self.root / "other.toml"
        other_path.write_text(self.commands_path.read_text(encoding="utf-8").split("[[command]]\nname = 'Start day'")[0], encoding="utf-8")
        other = CommandManager(other_path, Settings())
        candidates = other.parse_import_candidates(exported)
        self.assertEqual(candidates[0]["targets"], ["Tool", "Site"])
        self.assertEqual((candidates[0]["_error"], candidates[0]["_conflict"]), (None, False))
        self.assertEqual(other.import_commands(candidates)["imported"], ["Start day"])
        self.assertEqual(other.commands["Start day"]["targets"], ["Tool", "Site"])


if __name__ == "__main__":
    unittest.main()
