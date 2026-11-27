"""Keep every test out of the real Dash folder.

Dash keeps its icon store, logs and markers under %APPDATA%\\Dash. Tests that
build a CommandManager or MainWindow resolve and save command icons there, so
without this a test command named like a real one ("TestTrack") overwrote
that command's icon. Pointing APPDATA at a throwaway folder before any test
module is imported makes that impossible. Tests that need a particular
folder still patch it themselves.
"""
import atexit
import os
import shutil
import tempfile

_appdata = tempfile.mkdtemp(prefix="dash-tests-appdata-")
os.environ["APPDATA"] = _appdata
atexit.register(shutil.rmtree, _appdata, ignore_errors=True)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_scans(monkeypatch):
    """A miss in the launcher starts a background scan of this PC for the
    fallback. Tests never run it: it is slow, reads the real machine, and a
    window closed mid-scan must not leave a thread behind. Tests that want
    the scan's result hand candidates to the fallback index themselves."""
    from src import launcher_gui

    monkeypatch.setattr(launcher_gui.ProgramDiscoveryThread, "start", lambda self, *args: None)


@pytest.fixture(autouse=True)
def _fresh_hotkey_recording(monkeypatch):
    """Each launcher window connects to the one signal that says a hotkey
    field is recording, and pauses its hotkey listener on it. In the app
    there is one window for the life of the process; across tests, windows
    and their listeners are torn down while still connected. Each test gets
    its own signal, so recording in one never reaches another's listener."""
    from src import settings_editor

    monkeypatch.setattr(settings_editor, "_hotkey_recording", None)


@pytest.fixture(autouse=True)
def _flush_deleted_widgets():
    """Windows that tests close with deleteLater() are only destroyed when an
    event loop next handles deferred deletes, which between tests never
    happens. Left half torn down, a later test that moves focus would poke
    them. Destroy them as each test ends."""
    yield
    from PyQt6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
