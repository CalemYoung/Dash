import os
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.command_manager import CommandManager
from src.settings import Settings


class LaunchOptionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tool = self.root / "tool.exe"
        self.tool.write_bytes(b"")
        self.work = self.root / "work"
        self.work.mkdir()
        self.path = self.root / "commands.toml"
        self.path.write_text(
            f"""[[command]]
name = "Tool"
aliases = []
location = '{self.tool}'
description = ""

[[command]]
name = "Work"
aliases = []
location = '{self.work}'
description = ""

[[command]]
name = "Search"
aliases = []
location = 'https://example.com/?q={{query}}'
description = ""
type = "url"

[[command]]
name = "Display"
aliases = []
location = 'ms-settings:display'
description = ""
""",
            encoding="utf-8",
        )
        self.manager = CommandManager(self.path, Settings())

    def tearDown(self):
        self._tmp.cleanup()

    def test_arguments_and_working_folder_are_stored_and_passed(self):
        self.manager.save_command(
            {"name": "Tool", "aliases": [], "location": str(self.tool), "type": "file", "arguments": '--open "a b"', "working_folder": str(self.work)},
            original_name="Tool",
        )
        stored = tomllib.loads(self.path.read_text(encoding="utf-8"))["command"][0]
        self.assertEqual((stored["arguments"], stored["working_folder"]), ('--open "a b"', str(self.work)))
        with (
            mock.patch("src.command_manager.switch_to_running", return_value=True) as switch,
            mock.patch("src.command_manager.os.startfile", create=True) as startfile,
        ):
            self.assertIsNone(self.manager.execute_command(None, "Tool"))
        switch.assert_not_called()  # arguments ask for a fresh start
        startfile.assert_called_once_with(str(self.tool), "open", arguments='--open "a b"', cwd=str(self.work))

    def test_saving_without_mentioning_launch_settings_keeps_them(self):
        self.manager.save_command({"name": "Tool", "aliases": [], "location": str(self.tool), "type": "file", "arguments": "-x"}, original_name="Tool")
        self.manager.save_command({"name": "Tool", "aliases": ["t"], "location": str(self.tool), "type": "file"}, original_name="Tool")
        self.assertEqual(self.manager.commands["Tool"]["arguments"], "-x")
        self.manager.save_command({"name": "Tool", "aliases": ["t"], "location": str(self.tool), "type": "file", "arguments": ""}, original_name="Tool")
        self.assertNotIn("arguments", self.manager.commands["Tool"])

    def test_a_missing_working_folder_is_refused(self):
        error = self.manager.validate_command(
            {"name": "Other", "aliases": [], "location": str(self.tool), "type": "file", "working_folder": str(self.root / "gone")}
        )
        self.assertIn("working folder", error)

    def test_a_new_copy_skips_switching(self):
        with (
            mock.patch("src.command_manager.switch_to_running", return_value=True) as switch,
            mock.patch("src.command_manager.os.startfile", create=True) as startfile,
        ):
            self.assertIsNone(self.manager.execute_command(None, "Tool", new_instance=True))
        switch.assert_not_called()
        startfile.assert_called_once_with(str(self.tool), cwd=str(self.root))

    def test_a_shortcut_is_started_as_itself_and_switches_by_its_program(self):
        shortcut = self.root / "Discord.lnk"
        shortcut.write_bytes(b"")
        program = self.root / "app-1.0" / "Discord.exe"
        self.manager.import_program_commands(
            [{"name": "Discord", "aliases": [], "location": str(shortcut), "type": "file", "process_path": str(program)}]
        )
        self.assertEqual(self.manager.commands["Discord"]["process_path"], str(program))
        with (
            mock.patch("src.command_manager.switch_to_running", return_value=False) as switch,
            mock.patch("src.command_manager.os.startfile", create=True) as startfile,
        ):
            self.assertIsNone(self.manager.execute_command(None, "Discord"))
        switch.assert_called_once_with(str(program))
        startfile.assert_called_once_with(str(shortcut), cwd=None)

    def test_run_as_administrator(self):
        self.assertTrue(self.manager.can_run_as_administrator("Tool"))
        for name in ("Work", "Search", "Display", "Dash Settings"):
            self.assertFalse(self.manager.can_run_as_administrator(name), name)
            self.assertTrue(self.manager.run_as_administrator(name))
        with mock.patch("src.command_manager.os.startfile", create=True) as startfile:
            self.assertIsNone(self.manager.run_as_administrator("Tool"))
        startfile.assert_called_once_with(str(self.tool), "runas", arguments="", cwd=str(self.root))
        self.assertEqual(self.manager.commands["Tool"]["times_executed"], 1)

        cancelled = OSError("cancelled")
        cancelled.winerror = 1223
        with mock.patch("src.command_manager.os.startfile", create=True, side_effect=cancelled):
            self.assertIsNone(self.manager.run_as_administrator("Tool"))
        self.assertEqual(self.manager.commands["Tool"]["times_executed"], 1, "a refused prompt is not counted")

    def test_open_containing_folder(self):
        with (
            mock.patch("src.command_manager.subprocess.Popen") as popen,
            mock.patch("src.command_manager.os.startfile", create=True) as startfile,
        ):
            self.assertIsNone(self.manager.open_containing_folder("Tool"))
            self.assertIsNone(self.manager.open_containing_folder("Work"))
            self.assertTrue(self.manager.open_containing_folder("Search"))
        command_line = popen.call_args.args[0]
        self.assertIsInstance(command_line, str)
        self.assertTrue(command_line.endswith(f'/select,"{self.tool}"'))
        self.assertNotIn("shell", popen.call_args.kwargs)
        startfile.assert_called_once_with(str(self.work))
        self.assertEqual(self.manager.containing_folder("Tool"), self.root)
        self.assertEqual(self.manager.containing_folder("Work"), self.work)
        self.assertIsNone(self.manager.containing_folder("Display"))

    def test_copy_text(self):
        self.assertEqual(self.manager.copy_text("Tool"), str(self.tool))
        self.assertEqual(self.manager.copy_text("Display"), "ms-settings:display")
        self.assertEqual(self.manager.copy_text("Search"), "https://example.com/")
        self.assertEqual(self.manager.copy_text("Search", query="a b"), "https://example.com/?q=a+b")
        self.assertIsNone(self.manager.copy_text("Dash Settings"))


class GroupHelperTests(unittest.TestCase):
    def test_summary_and_groups_containing(self):
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "commands.toml"
            names = ["Mail", "Calendar", "Slack", "Spotify", "Notes"]
            blocks = [f"[[command]]\nname = '{name}'\naliases = []\nlocation = 'https://{name.lower()}.example.com/'\ntype = 'url'\n" for name in names]
            blocks.append("[[command]]\nname = 'Morning'\naliases = []\nlocation = ''\ntype = 'group'\ntargets = ['Mail', 'Calendar', 'Slack', 'Spotify', 'Notes']\n")
            blocks.append("[[command]]\nname = 'Short'\naliases = []\nlocation = ''\ntype = 'group'\ntargets = ['mail']\n")
            path.write_text("\n".join(blocks), encoding="utf-8")
            manager = CommandManager(path, Settings())
            self.assertEqual(manager.group_summary("Short"), "Opens: Mail")
            self.assertEqual(manager.group_summary("Morning"), "Opens: Mail, Calendar, Slack, Spotify, Notes")
            self.assertEqual(manager.group_summary("Morning", max_length=32), "Opens: Mail, Calendar and 3 more")
            self.assertEqual(manager.group_summary("Morning", max_length=31), "Opens: Mail and 4 more")
            self.assertEqual(manager.group_summary("Mail"), "")
            self.assertEqual(manager.groups_containing("MAIL"), ["Morning", "Short"])
            self.assertEqual(manager.groups_containing("Notes"), ["Morning"])
            self.assertEqual(manager.groups_containing("Morning"), [])


if __name__ == "__main__":
    unittest.main()
