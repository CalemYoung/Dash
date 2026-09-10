import sys
from pathlib import Path
import shutil
import os
import traceback


def get_app_data_dir():
    """Get AppData directory for user configs"""
    app_name = "Dash"

    if sys.platform == "win32":
        app_data = Path(os.environ.get("APPDATA", "")) / app_name
    else:
        # Fallback for development
        app_data = Path.home() / f".{app_name}"

    app_data.mkdir(parents=True, exist_ok=True)
    return app_data


def _log_fatal_error():
    """Write the current exception to a log file and show a native error box.

    Runs before any GUI toolkit is guaranteed to be importable, and the exe is
    built with console=False, so print()/input() are invisible - without this,
    startup failures (e.g. missing DLLs/modules) crash completely silently.
    """
    log_path = get_app_data_dir() / "crash.log"
    log_path.write_text(traceback.format_exc(), encoding="utf-8")

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"Dash failed to start.\n\nDetails were saved to:\n{log_path}",
            "Dash - Startup Error",
            0x10,  # MB_ICONERROR
        )


try:
    # Import the submodules directly rather than through the package's lazy
    # __getattr__: PyInstaller only bundles what it can see in static imports.
    from src.command_manager import CommandManager
    from src.launcher_gui import MainWindow
    from src.launcher_hotkey import HotkeyListener
    from src.settings import Settings
    from src.single_instance import SHOW_MESSAGE, SingleInstanceServer, notify_running_instance
    from src.version import current_version
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
except Exception:
    _log_fatal_error()
    sys.exit(1)

# The version is read from build/installer/version.txt, the single source of
# truth that the build also stamps into the installer and exe metadata.
__version__ = current_version()


def get_resource_path(relative_path):
    """Get absolute path to resource"""

    # In development, use files directly from project
    if not hasattr(sys, "_MEIPASS"):
        return Path(__file__).parent / relative_path

    # --- Installed app only below ---

    # User-editable files go to AppData
    if relative_path in ["config/settings.toml", "config/commands.toml"]:
        app_data = get_app_data_dir()
        resource_path = app_data / relative_path

        # Copy default config files on first run
        if relative_path in ["config/settings.toml", "config/commands.toml"]:
            if not resource_path.exists():
                resource_path.parent.mkdir(parents=True, exist_ok=True)
                default_relative_path = (
                    "config/commands.default.toml"
                    if relative_path == "config/commands.toml"
                    else "config/settings.default.toml"
                )
                default_file = Path(sys._MEIPASS) / default_relative_path  # type: ignore

                if default_file.exists():
                    shutil.copy(default_file, resource_path)
                    print(f"Created default {relative_path} in AppData")

        return resource_path

    # Bundled resources (icons, exe)
    return Path(sys._MEIPASS) / relative_path  # type: ignore


# Set working directory to exe location (important for Windows startup)
if hasattr(sys, "_MEIPASS"):
    os.chdir(Path(sys.executable).parent)
else:
    os.chdir(Path(__file__).parent)

# Passed by the Windows startup entry the installer writes. Every other way of
# starting Dash is a person clicking something, and they get the search bar.
STARTUP_FLAG = "--startup"

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        started_by_windows = STARTUP_FLAG in sys.argv[1:]

        # Already running? Ask that copy to show itself and bow out, so a
        # click on the icon never produces a second process and hotkey hook.
        if notify_running_instance(SHOW_MESSAGE):
            sys.exit(0)

        style_path = get_resource_path("style.qss")
        if style_path.exists():
            app.setStyleSheet(style_path.read_text(encoding="utf-8"))

        # Load settings from AppData (creates default if needed)
        settings_path = get_resource_path("config/settings.toml")
        settings = Settings.load_or_create_default(settings_path)

        # Load commands from AppData (creates default if needed)
        commands_path = get_resource_path("config/commands.toml")
        cmd_manager = CommandManager(commands_path, settings=settings)

        # Create main window (starts hidden)
        window = MainWindow(cmd_manager, settings, settings_path=settings_path)

        # Register global hotkey
        listener = HotkeyListener(hotkey=settings.general.hotkey)
        listener.triggered.connect(window.activate_launcher)
        window.set_hotkey_listener(listener)

        # Later launches (Start Menu, desktop shortcut) land here as "show".
        instance_server = SingleInstanceServer(
            lambda message: window.activate_launcher() if message == SHOW_MESSAGE else None,
            parent=app,
        )

        # Started by a person rather than by Windows at sign-in: show the
        # search bar so the click visibly did something.
        if not started_by_windows:
            QTimer.singleShot(0, window.activate_launcher)

        sys.exit(app.exec())
    except Exception:
        _log_fatal_error()
        sys.exit(1)
