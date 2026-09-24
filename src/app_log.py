"""Logging for a windowed app: everything goes to a file people can send in.

The installed exe has no console, so print() output disappears. Dash logs to
%APPDATA%\\Dash\\logs\\dash.log (rotated so it never grows without bound),
and uncaught exceptions are logged and shown instead of silently ending the
process.
"""

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

LOG_FILE_NAME = "dash.log"
_log_dir: Path | None = None
_error_reporter = None


def log_dir() -> Path | None:
    """The folder the log file is in, once logging has been set up."""
    return _log_dir


def set_error_reporter(reporter) -> None:
    """Register a callable(summary: str) that shows an unexpected error to the person.

    The GUI sets this once it exists; before then errors are only logged.
    """
    global _error_reporter
    _error_reporter = reporter


def setup_logging(app_data_dir: Path) -> Path:
    """Send logging to a rotating file under `app_data_dir`/logs and install exception hooks."""
    global _log_dir
    directory = Path(app_data_dir) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    _log_dir = directory

    handler = logging.handlers.RotatingFileHandler(directory / LOG_FILE_NAME, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        # Running from source: keep messages visible in the terminal too.
        root.addHandler(logging.StreamHandler())

    sys.excepthook = _handle_exception
    threading.excepthook = lambda args: _handle_exception(args.exc_type, args.exc_value, args.exc_traceback)
    return directory


def _handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.getLogger("dash").error("Unexpected error", exc_info=(exc_type, exc_value, exc_traceback))
    reporter = _error_reporter
    if reporter is not None:
        try:
            reporter(f"{exc_type.__name__}: {exc_value}")
        except Exception:
            logging.getLogger("dash").exception("Could not show the error to the user")
