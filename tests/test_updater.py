import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply

from src import updater
from src.updater import ReleaseInfo, UpdateDownloader, parse_release, release_from_page_url, remove_stale_downloads

INSTALLER = b"MZ" + b"installer bytes" * 100
DIGEST = hashlib.sha256(INSTALLER).hexdigest()


def release_payload(**overrides):
    payload = {
        "tag_name": "v1.2.3",
        "html_url": "https://github.com/CalemYoung/Dash/releases/tag/v1.2.3",
        "assets": [
            {"name": "DashSetup-1.2.3.exe", "browser_download_url": "https://example.com/DashSetup-1.2.3.exe", "size": 42},
            {"name": "DashSetup-1.2.3.exe.sha256", "browser_download_url": "https://example.com/DashSetup-1.2.3.exe.sha256"},
        ],
    }
    payload.update(overrides)
    return payload


class ParseReleaseTests(unittest.TestCase):
    def test_reads_installer_and_checksum(self):
        release = parse_release(release_payload())
        self.assertEqual(release.version, "1.2.3")
        self.assertEqual(release.installer_size, 42)
        self.assertTrue(release.installable)

    def test_malformed_payloads_give_none(self):
        for payload in (None, [], "text", 3, {}, {"tag_name": "v1"}, {"html_url": "https://x"}):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_release(payload))

    def test_malformed_assets_are_skipped(self):
        release = parse_release(release_payload(assets=["junk", None, {"name": 5}]))
        self.assertFalse(release.installable)
        release = parse_release(release_payload(assets="nope"))
        self.assertFalse(release.installable)
        release = parse_release(release_payload(assets=None))
        self.assertFalse(release.installable)

    def test_bad_size_and_unsafe_names(self):
        payload = release_payload()
        payload["assets"][0]["size"] = "big"
        self.assertEqual(parse_release(payload).installer_size, 0)
        payload = release_payload()
        payload["assets"][0]["name"] = "DashSetup-..\\..\\evil.exe"
        self.assertIsNone(parse_release(payload).installer_url)
        payload = release_payload()
        payload["assets"][0]["browser_download_url"] = "http://example.com/DashSetup-1.2.3.exe"
        self.assertIsNone(parse_release(payload).installer_url)


class ReleasePageTests(unittest.TestCase):
    """The fallback when the GitHub API is rate limited: the tag from the
    page /releases/latest redirects to, and the workflow's asset names."""

    def test_the_redirect_gives_the_release_and_its_files(self):
        release = release_from_page_url("https://github.com/CalemYoung/Dash/releases/tag/v2.10.1")
        self.assertEqual((release.version, release.tag), ("2.10.1", "v2.10.1"))
        self.assertEqual(release.page_url, "https://github.com/CalemYoung/Dash/releases/tag/v2.10.1")
        self.assertEqual(release.installer_url, "https://github.com/CalemYoung/Dash/releases/download/v2.10.1/DashSetup-2.10.1.exe")
        self.assertEqual(release.checksum_url, release.installer_url + ".sha256")
        self.assertEqual(release.installer_name, "DashSetup-2.10.1.exe")
        self.assertEqual(release.installer_size, 0, "unknown, so the size is not checked; the checksum still is")
        self.assertTrue(release.installable)

    def test_anything_else_gives_none(self):
        for url in (
            "",
            "https://github.com/CalemYoung/Dash/releases",
            "https://github.com/CalemYoung/Dash/releases/latest",
            "http://github.com/CalemYoung/Dash/releases/tag/v1.0.0",
            "https://example.com/CalemYoung/Dash/releases/tag/v1.0.0",
            "https://github.com/CalemYoung/Dash/releases/tag/v1.0.0%2F..%2Fevil",
        ):
            with self.subTest(url=url):
                self.assertIsNone(release_from_page_url(url))


class StaleDownloadTests(unittest.TestCase):
    def test_old_installers_are_removed_and_other_files_kept(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "DashSetup-1.0.0.exe").write_bytes(b"old")
            (folder / "DashSetup-1.1.0.exe").write_bytes(b"old")
            keep = folder / "DashSetup-1.2.0.exe"
            keep.write_bytes(b"new")
            (folder / "notes.txt").write_text("mine")
            self.assertEqual(remove_stale_downloads(folder, keep=keep), 2)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["DashSetup-1.2.0.exe", "notes.txt"])


class FakeReply(QObject):
    finished = pyqtSignal()
    readyRead = pyqtSignal()
    downloadProgress = pyqtSignal("qint64", "qint64")

    def __init__(self, data=b"", error=QNetworkReply.NetworkError.NoError):
        super().__init__()
        self._data = data
        self._error = error
        self.aborted = False

    def readAll(self):
        data, self._data = self._data, b""
        return data

    def error(self):
        return self._error

    def errorString(self):
        return "boom"

    def attribute(self, _attribute):
        return 200

    def abort(self):
        self.aborted = True
        self._error = QNetworkReply.NetworkError.OperationCanceledError
        self.finished.emit()


class FakeNetwork:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return self.replies.pop(0)


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.folder = Path(self._dir.name)
        patcher = mock.patch.object(updater, "download_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def release(self, size=len(INSTALLER)):
        return ReleaseInfo("1.2.3", "v1.2.3", "https://page", "https://i/DashSetup-1.2.3.exe",
                           "DashSetup-1.2.3.exe", size, "https://i/DashSetup-1.2.3.exe.sha256")

    def run_download(self, installer_reply, release=None):
        checksum = FakeReply(f"{DIGEST}  DashSetup-1.2.3.exe".encode())
        network = FakeNetwork([checksum, installer_reply])
        downloader = UpdateDownloader(network, release or self.release())
        results = {"finished": [], "failed": []}
        downloader.finished.connect(results["finished"].append)
        downloader.failed.connect(results["failed"].append)
        downloader.start()
        checksum.finished.emit()
        return downloader, network, results

    def test_a_good_download_is_verified(self):
        (self.folder / "DashSetup-0.9.0.exe").write_bytes(b"stale")
        reply = FakeReply(INSTALLER)
        downloader, network, results = self.run_download(reply)
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertEqual(results, {"finished": [str(downloader.target_path)], "failed": []})
        self.assertFalse((self.folder / "DashSetup-0.9.0.exe").exists())
        self.assertEqual([r.transferTimeout() for r in network.requests],
                         [updater.CHECKSUM_TIMEOUT_MS, updater.DOWNLOAD_TIMEOUT_MS])

    def test_a_short_download_is_discarded(self):
        reply = FakeReply(INSTALLER)
        downloader, _, results = self.run_download(reply, self.release(size=len(INSTALLER) + 10))
        reply.finished.emit()
        self.assertEqual(results["failed"], [updater.INCOMPLETE_MESSAGE])
        self.assertFalse(downloader.target_path.exists())

    def test_a_checksum_mismatch_is_discarded(self):
        reply = FakeReply(INSTALLER + b"tampered")
        downloader, _, results = self.run_download(reply, self.release(size=0))
        reply.finished.emit()
        self.assertEqual(results["failed"], [updater.MISMATCH_MESSAGE])
        self.assertFalse(downloader.target_path.exists())

    def test_a_full_disk_stops_the_download_cleanly(self):
        reply = FakeReply(INSTALLER)
        downloader, _, results = self.run_download(reply)
        broken = mock.Mock()
        broken.write.side_effect = OSError(28, "No space left on device")
        downloader._file.close()
        downloader._file = broken
        reply.readyRead.emit()  # must not raise
        self.assertTrue(reply.aborted)
        self.assertEqual(results["failed"], [updater.WRITE_MESSAGE])
        self.assertEqual(results["finished"], [])

    def test_network_errors_are_plain(self):
        reply = FakeReply(b"", error=QNetworkReply.NetworkError.TimeoutError)
        downloader, _, results = self.run_download(reply)
        reply.finished.emit()
        self.assertFalse(downloader.target_path.exists())
        self.assertEqual(results["failed"], [updater.DOWNLOAD_MESSAGE])

    def test_no_installer_is_reported(self):
        release = ReleaseInfo("1", "v1", "https://page", None, None, 0, None)
        downloader = UpdateDownloader(FakeNetwork([]), release)
        failed = []
        downloader.failed.connect(failed.append)
        downloader.start()
        self.assertEqual(failed, [updater.NO_INSTALLER_MESSAGE])


if __name__ == "__main__":
    unittest.main()
