"""Small file helpers shared by everything that saves user data."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Replace `path` with `text` so a reader only ever sees the old or the new file.

    Writing in place means a crash, a full disk or a power cut part-way
    through leaves a truncated file, and a truncated settings or commands
    file stops Dash from starting. The text goes to a temporary file in the
    same folder first and is then swapped in with os.replace, which is atomic
    on the same volume.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="") as temp_file:
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def quarantine_file(path: Path) -> Path | None:
    """Move an unreadable file aside as `<name>.bad` (numbered if one exists).

    Used when a settings or commands file cannot be parsed: Dash starts with
    defaults instead of refusing to run, and the original is kept so nothing
    the person wrote is lost. Returns where it went, or None if it could not
    be moved.
    """
    path = Path(path)
    target = path.with_name(path.name + ".bad")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bad{counter}")
        counter += 1
    try:
        os.replace(path, target)
    except OSError:
        return None
    return target
