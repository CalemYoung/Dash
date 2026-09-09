"""Download, verify and launch a Dash installer from a GitHub release.

The release workflow attaches `DashSetup-<version>.exe` and a matching
`.sha256` file. Nothing is executed unless the download matches that
checksum, so a release without one can only be opened in the browser.
"""
import hashlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

INSTALLER_PREFIX = "DashSetup-"
CHECKSUM_SUFFIX = ".sha256"
# Inno Setup switches: quiet install with a small progress window, close the
# running Dash via the Restart Manager, never trigger a Windows restart.
INSTALLER_ARGS = ("/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART")


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


def parse_release(payload: dict) -> ReleaseInfo | None:
    """Pick the installer and its checksum out of a GitHub release payload."""
    tag = str(payload.get("tag_name", "")).strip()
    page_url = str(payload.get("html_url", "")).strip()
    if not tag or not page_url:
        return None

    installer_url = installer_name = checksum_url = None
    installer_size = 0
    for asset in payload.get("assets", []) or []:
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if not name.startswith(INSTALLER_PREFIX) or not url:
            continue
        if name.endswith(".exe"):
            installer_url, installer_name = url, name
            installer_size = int(asset.get("size", 0) or 0)
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


def download_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "Dash" / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _request(url: str) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
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

    @property
    def target_path(self) -> Path:
        return self._target

    def start(self):
        if not self._release.installable:
            self.failed.emit("This release has no verifiable installer.")
            return
        self._reply = self._network.get(_request(self._release.checksum_url))
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
            self.failed.emit(f"Could not fetch the update checksum: {reply.errorString()}")
            return
        text = bytes(reply.readAll()).decode("utf-8", errors="replace").strip()
        digest = text.split()[0].lower() if text else ""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            self.failed.emit("The update checksum file is not valid.")
            return
        self._expected_hash = digest
        self._start_download()

    # -- installer -----------------------------------------------------------

    def _start_download(self):
        try:
            self._file = open(self._target, "wb")
        except OSError as error:
            self.failed.emit(f"Could not write the installer: {error}")
            return
        self._reply = self._network.get(_request(self._release.installer_url))
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.downloadProgress.connect(self.progress.emit)
        self._reply.finished.connect(self._on_download_finished)

    def _on_ready_read(self):
        if self._reply is None or self._file is None:
            return
        chunk = bytes(self._reply.readAll())
        if chunk:
            self._file.write(chunk)
            self._hasher.update(chunk)

    def _on_download_finished(self):
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        self._on_ready_read_from(reply)
        reply.deleteLater()
        if self._file is not None:
            self._file.close()
            self._file = None

        if self._cancelled:
            self._discard()
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._discard()
            self.failed.emit(f"Download failed: {reply.errorString()}")
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status is not None and int(status) >= 400:
            self._discard()
            self.failed.emit(f"Download failed with HTTP {status}.")
            return
        if self._hasher.hexdigest() != self._expected_hash:
            self._discard()
            self.failed.emit("The downloaded installer did not match its checksum and was discarded.")
            return
        self.finished.emit(str(self._target))

    def _on_ready_read_from(self, reply):
        # Drain whatever arrived between the last readyRead and finished.
        chunk = bytes(reply.readAll())
        if chunk and self._file is not None:
            self._file.write(chunk)
            self._hasher.update(chunk)

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
        subprocess.Popen(
            [installer_path, *INSTALLER_ARGS],
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return True
    except OSError:
        return False
