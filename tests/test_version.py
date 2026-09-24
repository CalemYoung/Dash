import unittest
from unittest import mock

from src import version
from src.version import is_newer_version, version_key


class VersionKeyTests(unittest.TestCase):
    def test_versions_become_padded_tuples(self):
        self.assertEqual(version_key("v2.10.1"), (2, 10, 1))
        self.assertEqual(version_key("V1.2"), (1, 2, 0))
        self.assertEqual(version_key("3"), (3, 0, 0))
        self.assertEqual(version_key(" 1.2.3 "), (1, 2, 3))
        self.assertEqual(version_key("1.2.3.4"), (1, 2, 3, 4))

    def test_prerelease_and_build_suffixes_are_ignored(self):
        self.assertEqual(version_key("1.2.3-beta.1"), (1, 2, 3))
        self.assertEqual(version_key("1.2.3+build.7"), (1, 2, 3))

    def test_things_that_are_not_versions(self):
        for value in ("", "v", "latest", "1..2", "1.x", "1.2.3a"):
            with self.subTest(value=value):
                self.assertIsNone(version_key(value))


class IsNewerVersionTests(unittest.TestCase):
    def test_compares_numerically(self):
        self.assertTrue(is_newer_version("v2.10.0", "2.9.9"))
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertTrue(is_newer_version("2", "1.9"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.1"))

    def test_different_lengths_are_padded(self):
        self.assertFalse(is_newer_version("1.2", "1.2.0"))
        self.assertFalse(is_newer_version("1.2.0.0", "1.2"))
        self.assertTrue(is_newer_version("1.2.0.1", "1.2"))

    def test_unreadable_versions_are_never_newer(self):
        self.assertFalse(is_newer_version("nightly", "1.0.0"))
        self.assertFalse(is_newer_version("2.0.0", "unknown"))


class CurrentVersionTests(unittest.TestCase):
    def test_missing_file_reads_as_zero(self):
        with mock.patch.object(version, "_version_root", return_value=version.Path("/nonexistent/dash")):
            self.assertEqual(version.current_version(), "0.0.0")

    def test_reads_the_committed_version_file(self):
        self.assertIsNotNone(version_key(version.current_version()))


if __name__ == "__main__":
    unittest.main()
