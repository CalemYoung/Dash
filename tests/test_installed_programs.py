import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


def load_installed_programs_module():
    module_path = Path(__file__).parents[1] / "src" / "installed_programs.py"
    spec = importlib.util.spec_from_file_location("installed_programs_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class InstalledProgramDiscoveryTests(unittest.TestCase):
    def test_discovers_start_menu_and_app_paths_without_recent_use_filter(self):
        installed_programs = load_installed_programs_module()

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            shortcut = root / "Word.lnk"
            word = root / "WINWORD.EXE"
            outlook = root / "OUTLOOK.EXE"
            word.touch()
            outlook.touch()

            with (
                mock.patch.object(installed_programs.sys, "platform", "win32"),
                mock.patch.object(installed_programs, "_start_menu_shortcuts", return_value=[shortcut]),
                mock.patch.object(installed_programs, "_shortcut_target", return_value=word),
                mock.patch.object(installed_programs, "_app_paths_registry_targets", return_value=[("OUTLOOK.EXE", outlook)]),
            ):
                commands = installed_programs.discover_recent_program_commands()

        self.assertEqual([command["name"] for command in commands], ["Outlook", "Word"])
        self.assertEqual([Path(command["location"]).name for command in commands], ["OUTLOOK.EXE", "WINWORD.EXE"])

    def test_filters_existing_locations_from_candidates(self):
        installed_programs = load_installed_programs_module()

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            existing = root / "Existing.exe"
            new = root / "New.exe"
            existing.touch()
            new.touch()
            candidates = [
                {"name": "Existing", "location": str(existing)},
                {"name": "New", "location": str(new)},
            ]
            existing_locations = {installed_programs._path_key(existing)}

            filtered = installed_programs.filter_new_program_commands(candidates, existing_locations)

        self.assertEqual(filtered, [{"name": "New", "location": str(new)}])


class ProgramUsageTests(unittest.TestCase):
    """UserAssist values become (path key, times opened, last opened)."""

    @staticmethod
    def _value(count: int, ticks: int) -> bytes:
        data = bytearray(72)
        data[4:8] = count.to_bytes(4, "little")
        data[60:68] = ticks.to_bytes(8, "little")
        return bytes(data)

    def test_reads_count_and_time_and_resolves_known_folder(self):
        installed_programs = load_installed_programs_module()
        import codecs

        with TemporaryDirectory() as tmp_dir:
            programs = Path(tmp_dir)
            guid = "{6D809377-6AF0-444B-8957-A3773F02200E}"
            name = codecs.encode(guid + r"\Tool\tool.exe", "rot13")
            # 2026-09-01 00:00:00 UTC as 100 ns ticks since 1601.
            ticks = 116_444_736_000_000_000 + 1_788_220_800 * 10_000_000
            folders: dict = {}
            with mock.patch.object(installed_programs, "_known_folder_path", return_value=programs) as resolve:
                entry = installed_programs._usage_entry(name, self._value(12, ticks), folders)
                again = installed_programs._usage_entry(name, self._value(1, 0), folders)

        self.assertEqual(entry, (installed_programs._path_key(programs / "Tool" / "tool.exe"), 12, 1_788_220_800.0))
        self.assertEqual(again[1:], (1, None))
        resolve.assert_called_once_with(guid)  # the folder is resolved once, then cached

    def test_skips_what_is_not_a_path_or_has_no_record(self):
        installed_programs = load_installed_programs_module()
        import codecs

        folders: dict = {}
        self.assertIsNone(installed_programs._usage_entry(codecs.encode("UEME_CTLSESSION", "rot13"), bytes(1612), folders))
        self.assertIsNone(installed_programs._usage_entry(codecs.encode("Microsoft.Office.EXCEL.EXE.15", "rot13"), self._value(3, 0), folders))
        self.assertIsNone(installed_programs._usage_entry(codecs.encode(r"C:\Tools\never.exe", "rot13"), self._value(0, 0), folders))
        self.assertIsNone(installed_programs._usage_entry(codecs.encode(r"C:\Tools\short.exe", "rot13"), b"\x00" * 16, folders))

    def test_program_command_carries_usage_from_shortcut_or_target(self):
        installed_programs = load_installed_programs_module()

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            shortcut = root / "Word.lnk"
            word = root / "WINWORD.EXE"
            usage = {
                installed_programs._path_key(shortcut): (0, 1_000.0),
                installed_programs._path_key(word): (4, 900.0),
            }
            commands: list = []
            installed_programs._add_program_command(
                commands, set(), set(), "Word", word, usage=usage, shortcut_path=shortcut
            )
            unused: list = []
            installed_programs._add_program_command(unused, set(), set(), "Other", root / "other.exe", usage=usage)

        self.assertEqual(commands[0]["opened"], 4)
        self.assertEqual(commands[0]["last_opened"], 1_000.0)
        self.assertNotIn("opened", unused[0])
        self.assertNotIn("last_opened", unused[0])

    def test_store_app_records_are_keyed_by_app_id(self):
        installed_programs = load_installed_programs_module()
        import codecs

        ticks = 116_444_736_000_000_000 + 1_788_220_800 * 10_000_000
        entry = installed_programs._usage_entry(codecs.encode("Claude_pzs8sxrjxfjjc!Claude", "rot13"), self._value(0, ticks), {})
        self.assertEqual(entry, ("claude_pzs8sxrjxfjjc!claude", 0, 1_788_220_800.0))


class _FakeShellItem:
    def __init__(self, name, path, properties):
        self.Name = name
        self.Path = path
        self._properties = properties

    def ExtendedProperty(self, key):
        return self._properties.get(key)


class PackagedAppTests(unittest.TestCase):
    """Store apps come from the shell's Applications folder, by app id."""

    def test_discovers_store_apps_and_leaves_desktop_apps_and_windows_parts_out(self):
        installed_programs = load_installed_programs_module()

        with TemporaryDirectory() as tmp_dir:
            package = Path(tmp_dir) / "Claude_1.0.0.0_x64__pzs8sxrjxfjjc"
            (package / "Assets").mkdir(parents=True)
            for variant in ("Square44x44Logo.png", "Square44x44Logo.scale-200.png", "Square44x44Logo.targetsize-24_altform-unplated.png"):
                (package / "Assets" / variant).write_bytes(b"png")
            items = [
                _FakeShellItem(
                    "Claude",
                    "Claude_pzs8sxrjxfjjc!Claude",
                    {
                        "System.AppUserModel.PackageFullName": package.name,
                        "System.AppUserModel.PackageInstallPath": str(package),
                        "System.Tile.SmallLogoPath": r"Assets\Square44x44Logo.png",
                    },
                ),
                _FakeShellItem(
                    "Notepad++",
                    r"C:\Program Files\Notepad++\notepad++.exe",
                    {"System.Link.TargetParsingPath": r"C:\Program Files\Notepad++\notepad++.exe"},
                ),
                _FakeShellItem(
                    "Get Started",
                    "MicrosoftWindows.Client.CBS_cw5n1h2txyewy!WebExperienceHost",
                    {"System.AppUserModel.PackageFullName": "MicrosoftWindows.Client.CBS_1.0_x64__cw5n1h2txyewy"},
                ),
            ]
            shell = mock.MagicMock()
            shell.NameSpace.return_value.Items.return_value = items
            usage = {"claude_pzs8sxrjxfjjc!claude": (0, 1_788_220_800.0)}
            with (
                mock.patch.object(installed_programs.sys, "platform", "win32"),
                mock.patch("win32com.client.Dispatch", return_value=shell),
                mock.patch.object(installed_programs, "program_usage", return_value=usage),
            ):
                commands = installed_programs.discover_packaged_apps()

            self.assertEqual(len(commands), 1)
            claude = commands[0]
            self.assertEqual(claude["name"], "Claude")
            self.assertEqual(claude["location"], r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude")
            self.assertEqual(claude["description"], "Opens Claude")
            self.assertEqual(claude["icon"], str(package / "Assets" / "Square44x44Logo.scale-200.png"))
            self.assertEqual(claude["last_opened"], 1_788_220_800.0)

    def test_logo_prefers_the_largest_then_the_unplated_variant(self):
        installed_programs = load_installed_programs_module()

        with TemporaryDirectory() as tmp_dir:
            assets = Path(tmp_dir) / "Assets"
            assets.mkdir()
            for variant in (
                "AppList.targetsize-256.png",
                "AppList.targetsize-256_altform-unplated.png",
                "AppList.altform-unplated_targetsize-48.png",
                "AppList.scale-400.png",
                "Other.targetsize-512.png",
            ):
                (assets / variant).write_bytes(b"png")
            best = installed_programs.packaged_app_logo(Path(tmp_dir), r"Assets\AppList.png")
            self.assertEqual(best.name, "AppList.targetsize-256_altform-unplated.png")
            self.assertIsNone(installed_programs.packaged_app_logo(Path(tmp_dir), "ms-resource:AppLogo"))
            self.assertIsNone(installed_programs.packaged_app_logo(Path(tmp_dir), r"Assets\Missing.png"))

    def test_app_id_locations_have_their_own_keys_and_skip_the_path_scan(self):
        installed_programs = load_installed_programs_module()

        self.assertTrue(installed_programs.is_app_id_location(r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"))
        self.assertFalse(installed_programs.is_app_id_location(r"C:\Tools\tool.exe"))
        self.assertEqual(
            installed_programs._path_key(Path(r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude")),
            r"shell:appsfolder\claude_pzs8sxrjxfjjc!claude",
        )
        # A Store app's executable is not offered by path: the app id scan has it.
        commands: list = []
        installed_programs._add_program_command(
            commands, set(), set(), "Teams", Path(r"C:\Program Files\WindowsApps\MSTeams_1.0_x64__8wekyb3d8bbwe\ms-teams.exe")
        )
        self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()