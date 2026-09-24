import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from src import icon_manager
from src.icon_manager import MAX_FAVICON_BYTES, IconManager, command_icon_stem, legacy_command_icon_stem

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_settings(download_favicons=True):
    return SimpleNamespace(
        general=SimpleNamespace(download_favicons=download_favicons),
        paths=SimpleNamespace(
            url_command_icon="url.png",
            default_command_icon="default.png",
            folder_icon="folder.png",
            file_icon="file.png",
            settings_command_icons="settings.png",
        ),
    )


class FakeResponse:
    def __init__(self, data, headers=None):
        self.data = data
        self.headers = headers or {}
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.data if size < 0 else self.data[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class IconManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = Path(self._dir.name)
        patcher = mock.patch.object(icon_manager, "get_user_icon_dir", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        # No background threads: tests look at the queue directly.
        worker = mock.patch.object(IconManager, "_start_download_worker")
        worker.start()
        self.addCleanup(worker.stop)

    def manager(self, **kwargs):
        return IconManager(make_settings(**kwargs))


class ImportTests(unittest.TestCase):
    def test_module_imports_without_pywin32(self):
        code = (
            "import sys\n"
            "for name in ('win32gui', 'win32ui', 'win32con', 'win32api'):\n"
            "    sys.modules[name] = None\n"
            "import src.icon_manager\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class IconStemTests(IconManagerTestCase):
    def test_similar_names_get_their_own_files(self):
        stems = {command_icon_stem(name) for name in ("a b", "a_b", "a/b", "A B")}
        self.assertEqual(len(stems), 4)

    def test_stem_is_stable_and_readable(self):
        self.assertEqual(command_icon_stem("My App"), command_icon_stem("My App"))
        self.assertTrue(command_icon_stem("My App").startswith("My_App-"))
        self.assertTrue(command_icon_stem("???").startswith("command-"))
        self.assertEqual(legacy_command_icon_stem("a b"), "a_b")

    def test_icons_saved_by_earlier_versions_are_still_found(self):
        manager = self.manager()
        legacy = self.store / "My_App.png"
        legacy.write_bytes(b"png")
        self.assertEqual(manager.existing_command_icon_path("My App"), legacy)
        # New icons are written under the new stem, which then wins.
        new = manager.command_icon_path("My App")
        self.assertNotEqual(new, legacy)
        new.write_bytes(b"png")
        self.assertEqual(manager.existing_command_icon_path("My App"), new)

    def test_legacy_source_artwork_is_found(self):
        legacy = self.store / "My_App.source.png"
        legacy.write_bytes(b"png")
        self.assertEqual(icon_manager.existing_command_source_icon_path("My App"), legacy)
        self.assertNotEqual(icon_manager.command_source_icon_path("My App"), legacy)


class FaviconDownloadTests(IconManagerTestCase):
    def test_turned_off_means_no_network_and_the_default_icon(self):
        manager = self.manager(download_favicons=False)
        with mock.patch("urllib.request.urlopen") as urlopen:
            manager._queue_favicon_download("https://example.com")
            self.assertIsNone(manager._download_icon("https://example.com/favicon.ico"))
            path = manager.get_icon_path({"name": "ex", "location": "https://example.com", "type": "url"})
        urlopen.assert_not_called()
        self.assertTrue(manager.download_queue.empty())
        self.assertEqual(path, "url.png")

    def test_turned_on_queues_the_download(self):
        manager = self.manager(download_favicons=True)
        manager._queue_favicon_download("https://example.com")
        self.assertFalse(manager.download_queue.empty())

    def test_setting_is_read_live(self):
        manager = self.manager(download_favicons=True)
        manager.settings = make_settings(download_favicons=False)
        self.assertFalse(manager.favicons_enabled)

    def test_oversized_download_is_rejected(self):
        manager = self.manager()
        response = FakeResponse(b"x" * (MAX_FAVICON_BYTES + 10))
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertIsNone(manager._download_icon("https://example.com/favicon.ico"))
        self.assertEqual(response.read_sizes, [MAX_FAVICON_BYTES + 1])

    def test_declared_oversized_download_is_not_read(self):
        manager = self.manager()
        response = FakeResponse(b"x", headers={"Content-Length": str(MAX_FAVICON_BYTES * 5)})
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertIsNone(manager._download_icon("https://example.com/favicon.ico"))
        self.assertEqual(response.read_sizes, [])


class ShortcutIconTests(IconManagerTestCase):
    """A shortcut that knows the program it runs shows that program's icon."""

    def setUp(self):
        super().setUp()
        self.shortcut = self.store / "Discord.lnk"
        self.shortcut.write_bytes(b"lnk")
        self.program = self.store / "Discord.exe"
        self.program.write_bytes(b"exe")
        self.extracted = self.store / "auto_exe_discord.png"

    def command(self, **extra):
        return {"name": "Discord", "location": str(self.shortcut), "type": "file", **extra}

    def test_the_icon_comes_from_the_program_behind_the_shortcut(self):
        manager = self.manager()
        with mock.patch.object(IconManager, "_extract_exe_icon", return_value=str(self.extracted)) as extract:
            path = manager.get_icon_path(self.command(process_path=str(self.program)))
        extract.assert_called_once_with(str(self.program))
        self.assertEqual(path, str(self.extracted))

    def test_resolving_the_qicon_uses_the_program_too(self):
        from PyQt6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])  # QIcon needs one
        manager = self.manager()
        with mock.patch.object(IconManager, "_extract_exe_icon", return_value=str(self.extracted)) as extract:
            manager.resolve_command_icon(self.command(process_path=str(self.program)))
        extract.assert_called_once_with(str(self.program))

    def test_without_a_program_the_shortcut_is_used(self):
        manager = self.manager()
        with mock.patch.object(IconManager, "_extract_exe_icon") as extract:
            self.assertEqual(manager.get_icon_path(self.command()), "file.png")
            missing = str(self.store / "gone.exe")
            self.assertEqual(manager.get_icon_path(self.command(process_path=missing)), "file.png")
        extract.assert_not_called()

    def test_a_failed_extraction_falls_back_to_the_shortcut(self):
        manager = self.manager()
        with mock.patch.object(IconManager, "_extract_exe_icon", return_value=None):
            self.assertEqual(manager.get_icon_path(self.command(process_path=str(self.program))), "file.png")

    def test_only_shortcuts_look_at_the_program(self):
        manager = self.manager()
        other = self.store / "notes.txt"
        other.write_bytes(b"text")
        command = {"name": "Notes", "location": str(other), "type": "file", "process_path": str(self.program)}
        with mock.patch.object(IconManager, "_extract_exe_icon") as extract:
            manager.get_icon_path(command)
        extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
