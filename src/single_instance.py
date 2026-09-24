"""One running Dash per user.

Dash lives in the tray with a global hotkey, so a second copy is never what
the user wants: it would register a second hook and fight the first for
focus. When Dash starts and finds a copy already running, it asks that copy
to show the launcher and exits, so clicking the Start Menu icon always ends
with the search bar on screen and never with two processes.

Who is first is decided by a lock, not by the pipe: a named mutex on Windows
(released by Windows when the process ends, even after a crash) and a
QLockFile elsewhere. Checking for the pipe alone raced, because two copies
started together could both find no pipe and both become the server. The
QLocalServer (a named pipe on Windows) only carries messages: "show", and
"add:<path>" from File Explorer's "Add to Dash".
"""
import getpass
import logging
import sys
import time

from PyQt6.QtCore import QDir, QLockFile, QObject, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

SHOW_MESSAGE = b"show"
# "Add to Dash" in File Explorer: the path follows the prefix, as UTF-8.
ADD_MESSAGE_PREFIX = b"add:"


def add_message(path: str) -> bytes:
    return ADD_MESSAGE_PREFIX + path.encode("utf-8")


def path_from_add_message(message: bytes) -> str | None:
    if not message.startswith(ADD_MESSAGE_PREFIX):
        return None
    path = message[len(ADD_MESSAGE_PREFIX) :].decode("utf-8", errors="replace").strip()
    return path or None
CONNECT_TIMEOUT_MS = 500
HANDOFF_TIMEOUT_MS = 2000
# The first copy creates its server only after loading settings and building
# its window, so a second copy started at the same moment keeps trying for a
# while before giving up.
STARTUP_WAIT_MS = 5000
RETRY_INTERVAL_MS = 100

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5


def server_name() -> str:
    # Per user, so two accounts on one machine do not collide.
    return f"Dash-launcher-{getpass.getuser()}"


class InstanceLock:
    """Held by the running Dash for its whole life."""

    def __init__(self, release):
        self._release = release

    def release(self):
        if self._release is not None:
            release, self._release = self._release, None
            release()


def _acquire_windows_mutex(name: str) -> InstanceLock | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    # "Local\" keeps the mutex to this sign-in session.
    handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
    error = ctypes.get_last_error()
    already_exists = error == ERROR_ALREADY_EXISTS
    if not handle:
        # A copy running as administrator owns a mutex a normal copy isn't
        # allowed to open: that still means Dash is already running.
        if error == ERROR_ACCESS_DENIED:
            return None
        raise OSError(error, "CreateMutexW failed")
    if already_exists:
        kernel32.CloseHandle(handle)
        return None
    return InstanceLock(lambda: kernel32.CloseHandle(handle))


def lock_file_path(name: str | None = None) -> str:
    return QDir(QDir.tempPath()).filePath(f"{name or server_name()}.lock")


def _acquire_lock_file(path: str) -> InstanceLock | None:
    lock = QLockFile(path)
    # Never stale by age; a lock left by a process that has ended is still
    # recognised (by its process ID) and taken over.
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return None
    return InstanceLock(lock.unlock)


def acquire_instance_lock(name: str | None = None) -> InstanceLock | None:
    """Claim the right to be the running Dash; None if another copy has it.

    If the lock cannot be created at all, the claim succeeds (a Dash that
    runs twice is better than one that never starts).
    """
    name = name or server_name()
    try:
        if sys.platform == "win32":
            return _acquire_windows_mutex(name)
        return _acquire_lock_file(lock_file_path(name))
    except Exception:
        log.warning("Single-instance lock unavailable", exc_info=True)
        return InstanceLock(None)


_instance_lock: InstanceLock | None = None


def _allow_running_copy_to_come_forward():
    # This copy was started by a click, so Windows lets it bring a window to
    # the front; pass that on to the running Dash, which shows the launcher.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            log.debug("AllowSetForegroundWindow failed", exc_info=True)


def _send(message: bytes) -> bool:
    _allow_running_copy_to_come_forward()
    socket = QLocalSocket()
    socket.connectToServer(server_name())
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    socket.write(message)
    socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
    # Stay connected until the running copy has read the message and hung
    # up; a client that disconnects first can take its bytes with it.
    if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
        socket.waitForDisconnected(HANDOFF_TIMEOUT_MS)
    return True


def notify_running_instance(message: bytes = SHOW_MESSAGE) -> bool:
    """Send `message` to an already running Dash. False if there is none.

    Returning False means this process is now the running Dash: it holds
    the single-instance lock until it exits, and should create a
    :class:`SingleInstanceServer`. True means another copy holds the lock
    (and was told, if it answered in time); this process should exit.
    """
    global _instance_lock
    if _instance_lock is not None:
        return False
    lock = acquire_instance_lock()
    if lock is not None:
        _instance_lock = lock
        return False

    deadline = time.monotonic() + STARTUP_WAIT_MS / 1000
    while True:
        if _send(message):
            return True
        if time.monotonic() >= deadline:
            log.warning("Dash is already running but did not answer; exiting anyway.")
            return True
        time.sleep(RETRY_INTERVAL_MS / 1000)


def release_instance_lock():
    """Give up the single-instance lock (on exit; tests)."""
    global _instance_lock
    if _instance_lock is not None:
        _instance_lock.release()
        _instance_lock = None


class SingleInstanceServer(QObject):
    """Listens for messages from later launches and hands them to a callback."""

    def __init__(self, on_message, parent=None):
        super().__init__(parent)
        self._on_message = on_message
        self._server = QLocalServer(self)
        # A previous Dash that crashed can leave the pipe name registered.
        # Only the lock holder gets here, so the name is not in use.
        QLocalServer.removeServer(server_name())
        self._server.newConnection.connect(self._accept)
        self.listening = self._server.listen(server_name())
        if not self.listening:
            log.warning("Single-instance server unavailable: %s", self._server.errorString())

    def _accept(self):
        while True:
            connection = self._server.nextPendingConnection()
            if connection is None:
                return
            connection.readyRead.connect(lambda c=connection: self._read(c))
            connection.disconnected.connect(lambda c=connection: self._read(c))
            # Drop a client that connects and never says anything.
            timer = QTimer(connection)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda c=connection: self._close(c))
            timer.start(HANDOFF_TIMEOUT_MS)
            if connection.bytesAvailable():
                self._read(connection)

    def _read(self, connection):
        if connection.property("dash_handled"):
            return
        message = bytes(connection.readAll()).strip()
        if not message:
            return
        connection.setProperty("dash_handled", True)
        self._close(connection)
        self._on_message(message)

    def _close(self, connection):
        try:
            connection.setProperty("dash_handled", True)
            connection.disconnectFromServer()
            connection.deleteLater()
        except RuntimeError:
            pass  # already deleted
