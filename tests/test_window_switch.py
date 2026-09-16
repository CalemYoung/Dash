import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src import window_switch
from src.command_manager import CommandManager
from src.settings import Settings

OUTLOOK = r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE"
CLAUDE = r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"


class TargetTests(unittest.TestCase):
    def test_only_programs_are_switched_to(self):
        self.assertEqual(window_switch._target_key(OUTLOOK), ("exe", os.path.normcase(OUTLOOK)))
        self.assertEqual(window_switch._target_key(CLAUDE), ("app", "claude_pzs8sxrjxfjjc!claude"))
        for other in (r"C:\Docs\report.docx", r"C:\Users\me", "ms-settings:display", r"C:\Tools\tool.lnk", ""):
            with self.subTest(other=other):
                self.assertIsNone(window_switch._target_key(other))


class FindWindowTests(unittest.TestCase):
    def _patched(self, windows, images=None, app_ids=None):
        return (
            mock.patch.object(window_switch, "top_level_windows", return_value=windows),
            mock.patch.object(window_switch, "process_image", side_effect=lambda pid: (images or {}).get(pid)),
            mock.patch.object(window_switch, "process_app_id", side_effect=lambda pid: (app_ids or {}).get(pid)),
        )

    def test_the_front_most_window_of_the_program_is_chosen(self):
        outlook = os.path.normcase(OUTLOOK)
        windows = [(10, 1), (20, 2), (30, 2), (40, 3)]
        top, image, app_id = self._patched(windows, images={1: r"c:\other.exe", 2: outlook, 3: outlook})
        with top, image as image_mock, app_id:
            self.assertEqual(window_switch.find_window(("exe", outlook)), 20)
        self.assertEqual(image_mock.call_count, 2, "each process is looked up once")

    def test_store_apps_match_by_app_id_and_dash_is_never_chosen(self):
        windows = [(10, os.getpid()), (20, 7)]
        top, image, app_id = self._patched(windows, app_ids={os.getpid(): "claude_x!claude", 7: "claude_x!claude"})
        with top, image, app_id:
            self.assertEqual(window_switch.find_window(("app", "claude_x!claude")), 20)

    def test_nothing_is_found_when_the_program_is_not_open(self):
        top, image, app_id = self._patched([(10, 1)], images={1: r"c:\other.exe"})
        with top, image, app_id, mock.patch.object(window_switch, "activate") as activate:
            self.assertFalse(window_switch.switch_to_running(OUTLOOK))
        activate.assert_not_called()

    def test_a_found_window_is_activated(self):
        top, image, app_id = self._patched([(10, 1)], images={1: os.path.normcase(OUTLOOK)})
        with top, image, app_id, mock.patch.object(window_switch, "activate", return_value=True) as activate:
            self.assertTrue(window_switch.switch_to_running(OUTLOOK))
        activate.assert_called_once_with(10)


class LaunchTests(unittest.TestCase):
    def _manager(self, root: Path, switch: bool) -> CommandManager:
        path = root / "commands.toml"
        path.write_text("", encoding="utf-8")
        settings = Settings()
        settings.general.switch_to_open_apps = switch
        return CommandManager(path, settings)

    def test_an_open_app_is_brought_forward_instead_of_started_again(self):
        with TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "tool.exe"
            exe.write_bytes(b"")
            manager = self._manager(Path(tmp_dir), switch=True)
            with (
                mock.patch("src.command_manager.switch_to_running", return_value=True) as switch,
                mock.patch("src.command_manager.os.startfile") as startfile,
            ):
                manager._open_file(exe)
        switch.assert_called_once_with(str(exe))
        startfile.assert_not_called()

    def test_with_the_setting_off_the_app_is_always_started(self):
        with TemporaryDirectory() as tmp_dir:
            exe = Path(tmp_dir) / "tool.exe"
            exe.write_bytes(b"")
            manager = self._manager(Path(tmp_dir), switch=False)
            with (
                mock.patch("src.command_manager.switch_to_running", return_value=True) as switch,
                mock.patch("src.command_manager.os.startfile") as startfile,
            ):
                manager._open_file(exe)
        switch.assert_not_called()
        startfile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
