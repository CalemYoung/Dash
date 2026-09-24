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
