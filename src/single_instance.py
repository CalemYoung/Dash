"""One running Dash per user.

Dash lives in the tray with a global hotkey, so a second copy is never what
the user wants: it would register a second hook and fight the first for
focus. When Dash starts and finds a copy already running, it asks that copy
to show the launcher and exits, so clicking the Start Menu icon always ends
with the search bar on screen and never with two processes.

Built on QLocalServer, which is a named pipe on Windows.
"""
import getpass

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

SHOW_MESSAGE = b"show"
CONNECT_TIMEOUT_MS = 500
HANDOFF_TIMEOUT_MS = 2000


def server_name() -> str:
    # Per user, so two accounts on one machine do not collide.
    return f"Dash-launcher-{getpass.getuser()}"


def notify_running_instance(message: bytes = SHOW_MESSAGE) -> bool:
    """Send `message` to an already running Dash. False if there is none."""
    socket = QLocalSocket()
    socket.connectToServer(server_name())
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    socket.write(message)
    socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
    # Stay connected until the running copy has read the message and hung
    # up; a client that disconnects first can take its bytes with it.
    socket.waitForDisconnected(HANDOFF_TIMEOUT_MS)
    return True


class SingleInstanceServer(QObject):
    """Listens for messages from later launches and hands them to a callback."""

    def __init__(self, on_message, parent=None):
        super().__init__(parent)
        self._on_message = on_message
        self._server = QLocalServer(self)
        # A previous Dash that crashed can leave the pipe name registered.
        QLocalServer.removeServer(server_name())
        self._server.newConnection.connect(self._accept)
        self.listening = self._server.listen(server_name())
        if not self.listening:
            print(f"Single-instance server unavailable: {self._server.errorString()}")

    def _accept(self):
        while True:
            connection = self._server.nextPendingConnection()
            if connection is None:
                return
            connection.waitForReadyRead(CONNECT_TIMEOUT_MS)
            message = bytes(connection.readAll()).strip()
            connection.disconnectFromServer()
            connection.deleteLater()
            if message:
                self._on_message(message)
