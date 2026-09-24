import ast
import re
import unittest
from pathlib import Path

from src.settings import Settings


class AssetPathResolutionTests(unittest.TestCase):
    def test_default_icon_path_is_resolved_from_project_root(self):
        original = Path.cwd()
        import os
        from tempfile import TemporaryDirectory

        try:
            with TemporaryDirectory() as tmp_dir:
                os.chdir(tmp_dir)
                settings = Settings()
                settings.normalize_resource_paths()

                resolved = Path(settings.paths.default_command_icon)
                self.assertTrue(resolved.is_absolute())
                self.assertTrue(resolved.exists())
                self.assertEqual(resolved.name, "icon.png")

                os.chdir(original)
        finally:
            os.chdir(original)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PackagingTests(unittest.TestCase):
    """The build files point at files that exist, so a rename does not only
    show up as a broken release build on the Windows runner."""

    def _spec_datas(self):
        tree = ast.parse((PROJECT_ROOT / "dash.spec").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "datas":
                return [ast.literal_eval(item)[0] for item in node.value.elts]
        self.fail("dash.spec has no datas list")

    def test_spec_bundles_the_files_dash_reads_at_run_time(self):
        datas = self._spec_datas()
        for source in datas:
            with self.subTest(source=source):
                self.assertTrue((PROJECT_ROOT / source).exists())
        for needed in ("style.qss", "config/settings.default.toml", "config/commands.default.toml", "build/installer/version.txt"):
            self.assertIn(needed, datas)

    def test_spec_collects_every_src_module(self):
        # src/__init__.py imports lazily, so the spec has to name the package.
        self.assertIn("collect_submodules('src')", (PROJECT_ROOT / "dash.spec").read_text(encoding="utf-8"))

    def test_installer_sources_from_the_repository_exist(self):
        script = (PROJECT_ROOT / "build" / "installer" / "installer.iss").read_text(encoding="utf-8")
        sources = re.findall(r'^Source: "\.\.\\\.\.\\([^"]+)"', script, flags=re.MULTILINE)
        self.assertTrue(sources)
        for source in sources:
            if source.startswith("dist\\"):
                continue  # produced by PyInstaller during the build
            path = PROJECT_ROOT / source.replace("\\", "/").split("*")[0]
            with self.subTest(source=source):
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
