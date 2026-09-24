"""Download, verify and launch a Dash installer from a GitHub release.

The release workflow attaches `DashSetup-<version>.exe` and a matching
`.sha256` file. Nothing is executed unless the download matches that
checksum, so a release without one can only be opened in the browser.
"""
import hashlib
import logging
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .child_process import clean_dll_search

log = logging.getLogger(__name__)

INSTALLER_PREFIX = "DashSetup-"
CHECKSUM_SUFFIX = ".sha256"
# Inno Setup switches: quiet install with a small progress window, close the
# running Dash via the Restart Manager, never trigger a Windows restart, and
# relaunch Dash afterwards (silent installs otherwise stay silent, so package
# managers do not get an app popping up mid-install).
INSTALLER_ARGS = ("/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART", "/RELAUNCH=1")

# Qt's transfer timeout fires when no data arrives for this long, so a slow
# but moving download is never cut off.
CHECKSUM_TIMEOUT_MS = 10_000
DOWNLOAD_TIMEOUT_MS = 30_000

# Plain-language messages for the `failed` signal.
NO_INSTALLER_MESSAGE = "This release has no installer Dash can check, so it can't be installed automatically."
CHECKSUM_FETCH_MESSAGE = "Dash couldn't download the update. Check your internet connection and try again."
CHECKSUM_INVALID_MESSAGE = "The update couldn't be checked, so it wasn't installed. Try again later."
WRITE_MESSAGE = "Dash couldn't save the update. Make sure your drive has free space and try again."
DOWNLOAD_MESSAGE = "The update download didn't finish. Check your internet connection and try again."
INCOMPLETE_MESSAGE = "The update download was incomplete, so it was discarded. Try again."
MISMATCH_MESSAGE = "The downloaded update didn't pass its safety check, so it was discarded."


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    page_url: str
    installer_url: str | None
    installer_name: str | None
    installer_size: int
    checksum_url: str | None

    @property
    def installable(self) -> bool:
        return bool(self.installer_url and self.checksum_url)


def is_installed_build() -> bool:
    """True when running the packaged exe rather than from source."""
    return hasattr(sys, "_MEIPASS")


def parse_release(payload) -> ReleaseInfo | None:
    """Pick the installer and its checksum out of a GitHub release payload.

    Returns None for anything that is not a usable release (not a JSON
    object, no tag or page), so a malformed response never raises here.
    Malformed assets are skipped.
    """
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name", "") or "").strip()
    page_url = str(payload.get("html_url", "") or "").strip()
    if not tag or not page_url:
        return None

    installer_url = installer_name = checksum_url = None
    installer_size = 0
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "") or "")
        url = str(asset.get("browser_download_url", "") or "")
        if not name.startswith(INSTALLER_PREFIX) or not url.startswith("https://"):
            continue
        # The name becomes a file name in the download folder.
        if "/" in name or "\\" in name or ".." in name:
            continue
        if name.endswith(".exe"):
            installer_url, installer_name = url, name
            try:
                installer_size = max(0, int(asset.get("size", 0) or 0))
            except (TypeError, ValueError):
                installer_size = 0
        elif name.endswith(".exe" + CHECKSUM_SUFFIX):
            checksum_url = url

    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        page_url=page_url,
        installer_url=installer_url,
        installer_name=installer_name,
        installer_size=installer_size,
        checksum_url=checksum_url,
    )


def release_from_page_url(url: str) -> ReleaseInfo | None:
    """The release that github.com/<repo>/releases/latest redirects to.

    Used when the GitHub API refuses the check because of its rate limit
    (60 an hour per address, easily used up on a shared office network);
    the web redirect has no such limit. It gives only the tag, so the
    installer and checksum addresses follow GitHub's fixed pattern for the
    files the release workflow uploads. The size is unknown (0, so it is not
    checked); the checksum still is.
    """
    match = re.fullmatch(r"(https://github\.com/[^/]+/[^/]+)/releases/tag/([^/?#]+)/?", str(url or "").strip())
    if match is None:
        return None
    base, tag = match.group(1), unquote(match.group(2))
    version = tag.lstrip("vV")
    if not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
        return None
    name = f"{INSTALLER_PREFIX}{version}.exe"
    installer_url = f"{base}/releases/download/{tag}/{name}"
    return ReleaseInfo(
        version=version,
        tag=tag,
        page_url=f"{base}/releases/tag/{tag}",
        installer_url=installer_url,
        installer_name=name,
        installer_size=0,
        checksum_url=installer_url + CHECKSUM_SUFFIX,
    )


def download_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "Dash" / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_stale_downloads(directory: Path | None = None, keep: Path | None = None) -> int:
    """Delete installers left from earlier update attempts; returns how many.

    Only Dash's own installer files are touched. A file that cannot be
    deleted (still open, say) is left for next time.
    """
    try:
        directory = directory or download_dir()
        candidates = list(directory.glob(f"{INSTALLER_PREFIX}*"))
    except OSError:
        return 0
    removed = 0
    for path in candidates:
        if keep is not None and path == keep:
            continue
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            log.debug("Could not remove old update file %s", path, exc_info=True)
    return removed


def _request(url: str, timeout_ms: int = DOWNLOAD_TIMEOUT_MS) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setTransferTimeout(timeout_ms)
    # Release assets redirect from github.com to a CDN host.
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    request.setRawHeader(b"User-Agent", b"Dash-Updater")
    return request


class UpdateDownloader(QObject):
    """Fetch the checksum, then stream the installer to disk and verify it."""

    progress = pyqtSignal(int, int)  # bytes received, bytes total (-1 if unknown)
    finished = pyqtSignal(str)  # verified installer path
    failed = pyqtSignal(str)

    def __init__(self, network: QNetworkAccessManager, release: ReleaseInfo, parent=None):
        super().__init__(parent)
        self._network = network
        self._release = release
        self._reply: QNetworkReply | None = None
        self._expected_hash = ""
        self._hasher = hashlib.sha256()
        self._file = None
        self._target = download_dir() / (release.installer_name or f"{INSTALLER_PREFIX}{release.version}.exe")
        self._cancelled = False
        self._received = 0
        self._error: str | None = None

    @property
    def target_path(self) -> Path:
        return self._target

    def start(self):
        if not self._release.installable:
            self.failed.emit(NO_INSTALLER_MESSAGE)
            return
        self._reply = self._network.get(_request(self._release.checksum_url, CHECKSUM_TIMEOUT_MS))
        self._reply.finished.connect(self._on_checksum_finished)

    def cancel(self):
        self._cancelled = True
        if self._reply is not None:
            self._reply.abort()

    # -- checksum ------------------------------------------------------------

    def _on_checksum_finished(self):
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        reply.deleteLater()
        if self._cancelled:
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            log.warning("Update checksum download failed: %s", reply.errorString())
            self.failed.emit(CHECKSUM_FETCH_MESSAGE)
            return
        text = bytes(reply.readAll()).decode("utf-8", errors="replace").strip()
        digest = text.split()[0].lower() if text else ""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            self.failed.emit(CHECKSUM_INVALID_MESSAGE)
            return
        self._expected_hash = digest
        self._start_download()

    # -- installer -----------------------------------------------------------

    def _start_download(self):
        # Installers from earlier attempts are only taking up space.
        remove_stale_downloads(self._target.parent)
        try:
            self._file = open(self._target, "wb")
        except OSError as error:
            log.warning("Could not create %s: %s", self._target, error)
            self.failed.emit(WRITE_MESSAGE)
            return
        self._received = 0
        self._reply = self._network.get(_request(self._release.installer_url, DOWNLOAD_TIMEOUT_MS))
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.downloadProgress.connect(self.progress.emit)
        self._reply.finished.connect(self._on_download_finished)

    def _on_ready_read(self):
        if self._reply is None:
            return
        self._consume(self._reply)

    def _consume(self, reply):
        """Write what has arrived to disk. A failed write (a full disk, say)
        stops the download instead of raising inside a Qt slot."""
        chunk = bytes(reply.readAll())
        if not chunk or self._file is None or self._error is not None:
            return
        try:
            self._file.write(chunk)
        except OSError as error:
            log.warning("Could not write the update to %s: %s", self._target, error)
            self._error = WRITE_MESSAGE
            self._close_file()
            reply.abort()
            return
        self._hasher.update(chunk)
        self._received += len(chunk)
        expected = self._release.installer_size
        if expected and self._received > expected:
            self._error = INCOMPLETE_MESSAGE
            self._close_file()
            reply.abort()

    def _close_file(self):
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError as error:
            # Closing flushes; a full disk can surface here too.
            log.warning("Could not finish writing %s: %s", self._target, error)
            if self._error is None:
                self._error = WRITE_MESSAGE
        self._file = None

    def _on_download_finished(self):
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        # Drain whatever arrived between the last readyRead and finished.
        self._consume(reply)
        reply.deleteLater()
        self._close_file()

        if self._cancelled:
            self._discard()
            return
        if self._error is not None:
            self._discard()
            self.failed.emit(self._error)
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            log.warning("Update download failed: %s", reply.errorString())
            self._discard()
            self.failed.emit(DOWNLOAD_MESSAGE)
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status is not None and int(status) >= 400:
            log.warning("Update download failed with HTTP %s", status)
            self._discard()
            self.failed.emit(DOWNLOAD_MESSAGE)
            return
        expected = self._release.installer_size
        if expected and self._received != expected:
            log.warning("Update download size %d, expected %d", self._received, expected)
            self._discard()
            self.failed.emit(INCOMPLETE_MESSAGE)
            return
        if self._hasher.hexdigest() != self._expected_hash:
            self._discard()
            self.failed.emit(MISMATCH_MESSAGE)
            return
        # TODO: verify the installer's Authenticode signature here (for
        # example with WinVerifyTrust) once releases are code signed. The
        # SHA-256 check above is the only integrity check until then.
        self.finished.emit(str(self._target))

    def _discard(self):
        # A freshly written .exe is often held open briefly by antivirus
        # scanning; give the delete a few tries before giving up.
        for _ in range(5):
            try:
                self._target.unlink(missing_ok=True)
                return
            except OSError:
                time.sleep(0.2)


def launch_installer(installer_path: str) -> bool:
    """Start the silent upgrade. The caller should quit right after."""
    try:
        with clean_dll_search():
            subprocess.Popen(
                [installer_path, *INSTALLER_ARGS],
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        return True
    except OSError:
        return False
