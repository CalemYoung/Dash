import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.personal_places import _read_chromium_bar, _read_firefox_bar

# 2026-09-01 00:00:00 UTC, as POSIX seconds and as Chromium microseconds since 1601.
_MOMENT = 1_788_220_800
_CHROME_MOMENT = (_MOMENT + 11_644_473_600) * 1_000_000


def _bookmarks(*children) -> dict:
    return {"roots": {"bookmark_bar": {"type": "folder", "name": "Bookmarks bar", "children": list(children)}}}


class ChromiumBookmarkUsageTests(unittest.TestCase):
    def test_visit_counts_come_from_history_and_last_used_from_the_bookmark(self):
        with TemporaryDirectory() as tmp_dir:
            profile = Path(tmp_dir)
            store = profile / "Bookmarks"
            store.write_text(
                json.dumps(
                    _bookmarks(
                        {"type": "url", "name": "Repo", "url": "https://github.com/x/y", "date_last_used": "0"},
                        {"type": "url", "name": "Docs", "url": "https://docs.example.com/", "date_last_used": str(_CHROME_MOMENT)},
                        {"type": "url", "name": "Never", "url": "https://never.example.com/", "date_last_used": "0"},
                    )
                ),
                encoding="utf-8",
            )
            connection = sqlite3.connect(profile / "History")
            connection.execute("CREATE TABLE urls (url TEXT, visit_count INTEGER, last_visit_time INTEGER)")
            connection.execute("INSERT INTO urls VALUES ('https://github.com/x/y/', 41, ?)", (_CHROME_MOMENT - 86_400_000_000,))
            connection.commit()
            connection.close()

            entries = {entry["name"]: entry for entry in _read_chromium_bar(store, "Edge")}

        self.assertEqual(entries["Repo"]["opened"], 41)  # trailing slash does not make a different page
        self.assertEqual(entries["Repo"]["last_opened"], _MOMENT - 86_400)
        self.assertNotIn("opened", entries["Docs"])
        self.assertEqual(entries["Docs"]["last_opened"], _MOMENT)
        self.assertNotIn("opened", entries["Never"])
        self.assertNotIn("last_opened", entries["Never"])

    def test_bookmarks_still_read_without_a_history_database(self):
        with TemporaryDirectory() as tmp_dir:
            store = Path(tmp_dir) / "Bookmarks"
            store.write_text(
                json.dumps(_bookmarks({"type": "url", "name": "Repo", "url": "https://github.com/x/y"})), encoding="utf-8"
            )
            entries = _read_chromium_bar(store, "Chrome")
        self.assertEqual([entry["name"] for entry in entries], ["Repo"])
        self.assertNotIn("last_opened", entries[0])


class FirefoxBookmarkUsageTests(unittest.TestCase):
    def test_visits_come_from_places(self):
        with TemporaryDirectory() as tmp_dir:
            store = Path(tmp_dir) / "places.sqlite"
            connection = sqlite3.connect(store)
            connection.executescript(
                """
                CREATE TABLE moz_places (id INTEGER, url TEXT, visit_count INTEGER, last_visit_date INTEGER);
                CREATE TABLE moz_bookmarks (id INTEGER, type INTEGER, parent INTEGER, fk INTEGER, position INTEGER, title TEXT, guid TEXT);
                INSERT INTO moz_places VALUES (1, 'https://example.com/', 7, 1788220800000000);
                INSERT INTO moz_places VALUES (2, 'https://quiet.example.com/', 0, NULL);
                INSERT INTO moz_bookmarks VALUES (1, 2, 0, NULL, 0, 'toolbar', 'toolbar_____');
                INSERT INTO moz_bookmarks VALUES (2, 1, 1, 1, 0, 'Example', 'aaaaaaaaaaaa');
                INSERT INTO moz_bookmarks VALUES (3, 1, 1, 2, 1, 'Quiet', 'bbbbbbbbbbbb');
                """
            )
            connection.commit()
            connection.close()
            entries = {entry["name"]: entry for entry in _read_firefox_bar(store)}

        self.assertEqual(entries["Example"]["opened"], 7)
        self.assertEqual(entries["Example"]["last_opened"], _MOMENT)
        self.assertNotIn("opened", entries["Quiet"])
        self.assertNotIn("last_opened", entries["Quiet"])


if __name__ == "__main__":
    unittest.main()
