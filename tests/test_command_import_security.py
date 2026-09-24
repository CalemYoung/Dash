import base64
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from src.command_manager import CommandManager
from src.installed_programs import is_network_location, link_scheme
from src.settings import Settings


def _block(name: str, location: str, **extra) -> str:
    lines = [f"[[command]]", f"name = '{name}'", "aliases = []", f"location = '{location}'", "description = ''"]
    lines += [f"{key} = '{value}'" for key, value in extra.items()]
    return "\n".join(lines) + "\n"


class ImportSecurityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tool = self.root / "tool.exe"
        self.tool.write_bytes(b"")
        commands_path = self.root / "commands.toml"
        commands_path.write_text("", encoding="utf-8")
        self.manager = CommandManager(commands_path, Settings())
        self.shared = self.root / "shared.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def _parse(self, *blocks: str) -> dict[str, dict]:
        self.shared.write_text("\n".join(blocks), encoding="utf-8")
        return {candidate["name"]: candidate for candidate in self.manager.parse_import_candidates(self.shared)}

    def test_network_locations_are_refused_without_being_looked_up(self):
        looked_up: list[str] = []
        real_exists = Path.exists

        def exists(path, *args, **kwargs):
            looked_up.append(str(path))
            return real_exists(path, *args, **kwargs)

        with mock.patch.object(Path, "exists", exists):
            candidates = self._parse(
                _block("Share", r"\\server\share\tool.exe"),
                _block("Slashes", "//server/share/tool.exe"),
                _block("Folder", str(self.tool), working_folder=r"\\server\share"),
            )
            imported = self.manager.import_commands(list(candidates.values()))
        for name in ("Share", "Slashes", "Folder"):
            self.assertIn("network location", candidates[name]["_error"])
            self.assertFalse(candidates[name]["_conflict"])
        self.assertEqual(imported["imported"], [])
        self.assertFalse([path for path in looked_up if "server" in path], looked_up)

    def test_only_safe_link_schemes_are_imported(self):
        candidates = self._parse(
            _block("Display", "ms-settings:display"),
            _block("Startup", "shell:startup"),
            _block("Mail", "mailto:me@example.com"),
            _block("Troubleshooter", "ms-msdt:/id PCWDiagnostic"),
            _block("Search", "search-ms:query=x"),
            _block("File", "file:///C:/Windows/System32/calc.exe"),
        )
        for name in ("Display", "Startup", "Mail"):
            self.assertIsNone(candidates[name]["_error"], name)
        for name, scheme in (("Troubleshooter", "ms-msdt"), ("Search", "search-ms"), ("File", "file")):
            self.assertIn(f"'{scheme}:'", candidates[name]["_error"])
        summary = self.manager.import_commands(list(candidates.values()))
        self.assertEqual(sorted(summary["imported"]), ["Display", "Mail", "Startup"])

    def test_script_hosts_with_arguments_are_refused(self):
        candidates = self._parse(
            _block("Script", r"C:\Windows\System32\cmd.exe", arguments="/c del *"),
            _block("Tool", str(self.tool), arguments="--fast"),
        )
        self.assertIn("script host", candidates["Script"]["_error"])
        self.assertIsNone(candidates["Tool"]["_error"])
        self.assertEqual(candidates["Tool"]["arguments"], "--fast")

    def test_an_icon_file_named_by_the_export_is_not_imported(self):
        secret = self.root / "secret.png"
        secret.write_bytes(b"png")
        embedded = base64.b64encode(b"embedded").decode("ascii")
        candidates = self._parse(
            _block("Pointer", "https://a.example.com/", type="url", icon_source=str(secret)),
            _block("Embedded", "https://b.example.com/", type="url", icon_source_data=embedded),
        )
        self.assertNotIn("icon_source", candidates["Pointer"])
        with mock.patch("src.command_manager.command_source_icon_path", return_value=self.root / "stored.png"):
            self.manager.import_commands(list(candidates.values()))
        stored = {entry["name"]: entry for entry in self.manager._read_raw_commands()}
        self.assertNotIn("icon_source", stored["Pointer"])
        self.assertEqual(stored["Embedded"]["icon_source"], str(self.root / "stored.png"))
        self.assertEqual((self.root / "stored.png").read_bytes(), b"embedded")

    def test_commands_made_in_the_editor_are_not_restricted(self):
        page = {"name": "Troubleshooter", "aliases": [], "location": "ms-msdt:/id x", "type": "file"}
        self.assertIsNone(self.manager.validate_command(page))
        self.assertEqual(self.manager.check_import_candidate(page), (None, False))

    def test_launch_settings_travel_with_an_export(self):
        folder = self.root / "work"
        folder.mkdir()
        self.manager.save_command(
            {"name": "Tool", "aliases": [], "location": str(self.tool), "type": "file", "arguments": "--x", "working_folder": str(folder)}
        )
        exported = self.root / "export.toml"
        self.manager.export_commands(["Tool"], exported)
        entry = tomllib.loads(exported.read_text(encoding="utf-8"))["command"][0]
        self.assertEqual((entry["arguments"], entry["working_folder"]), ("--x", str(folder)))

        other_path = self.root / "other.toml"
        other_path.write_text("", encoding="utf-8")
        other = CommandManager(other_path, Settings())
        candidates = other.parse_import_candidates(exported)
        self.assertEqual(other.import_commands(candidates)["imported"], ["Tool"])
        self.assertEqual((other.commands["Tool"]["arguments"], other.commands["Tool"]["working_folder"]), ("--x", str(folder)))

    def test_link_helpers(self):
        self.assertTrue(is_network_location(r"\\server\share"))
        self.assertTrue(is_network_location("//server/share"))
        self.assertTrue(is_network_location(r"\\?\UNC\server\share"))
        self.assertFalse(is_network_location(r"C:\Tools"))
        self.assertEqual(link_scheme("MS-Settings:display"), "ms-settings")
        self.assertEqual(link_scheme(r"C:\Tools"), "")


if __name__ == "__main__":
    unittest.main()
