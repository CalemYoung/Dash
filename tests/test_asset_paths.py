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


if __name__ == "__main__":
    unittest.main()
