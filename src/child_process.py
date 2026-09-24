"""Start other programs without handing them Dash's DLL folder.

The PyInstaller bootloader calls SetDllDirectory with Dash's _internal
folder, and Windows copies that setting into every process Dash starts.
Programs opened from Dash (VS Code, Nx, a browser) then load DLLs such as
the C runtime from Dash's install folder and keep them open, so the next
Dash installer finds those files in use and asks to close the programs.

Clearing the setting permanently could break DLLs Dash itself loads later
(Qt loads OpenSSL by name on the first HTTPS request), so it is cleared
only while a program is being started and restored straight after.
"""
import ctypes
import sys
from contextlib import contextmanager


@contextmanager
def clean_dll_search():
    """Clear the inherited DLL directory for the programs started inside."""
    if sys.platform != "win32" or not hasattr(sys, "_MEIPASS"):
        yield
        return
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetDllDirectoryW(len(buffer), buffer)
    previous = buffer.value if length else None
    kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        kernel32.SetDllDirectoryW(previous)
