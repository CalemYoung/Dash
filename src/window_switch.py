"""Bring an app that is already open to the front instead of starting it again.

A command's target is matched against the windows on screen by the program
behind them: an executable by its full path, a Store app by its app id. Only
windows a person would switch to count: visible, not owned by another window,
titled, not tool windows, and not cloaked (Windows cloaks the windows of
suspended Store apps and of other virtual desktops).
"""
import ctypes
import os
import re
import sys
from ctypes import wintypes
from pathlib import Path

from .installed_programs import is_app_id_location

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_SW_RESTORE = 9


def switch_to_running(location: str) -> bool:
    """Focus the front-most window of the program `location` starts.
    True if a window was found and brought forward; False to launch as usual.

    For a shortcut that passes arguments, pass the program it runs (the
    command's "process_path"), not the shortcut: a .lnk is never matched.
    """
    target = _target_key(location)
    if target is None:
        return False
    hwnd = find_window(target)
    return hwnd is not None and activate(hwnd)


def _target_key(location: str) -> tuple[str, str] | None:
    """("exe", path key) or ("app", app id), or None for targets that are
    not programs: documents, folders, shortcuts, Settings pages."""
    text = str(location or "").strip()
    if is_app_id_location(text):
        return "app", text.split("\\", 1)[1].casefold()
    if Path(text).suffix.casefold() == ".exe":
        return "exe", _without_version_folder(os.path.normcase(os.path.abspath(text)))
    return None


# Squirrel installs (Discord, Slack) keep the exe in "app-<version>", a new
# folder on every update, so a stored path goes stale; any version matches.
_VERSION_FOLDER = re.compile(r"([\\/])app-[0-9][^\\/]*([\\/][^\\/]+)$", re.IGNORECASE)


def _without_version_folder(path_key: str) -> str:
    return _VERSION_FOLDER.sub(r"\1app-*\2", path_key)


def find_window(target: tuple[str, str]) -> int | None:
    """The front-most switchable window of the target program, or None."""
    kind, wanted = target
    own_pid = os.getpid()
    seen: dict[int, str | None] = {}
    for hwnd, pid in top_level_windows():
        if pid == own_pid:
            continue
        if pid not in seen:
            if kind == "app":
                seen[pid] = process_app_id(pid)
            else:
                image = process_image(pid)
                seen[pid] = _without_version_folder(image) if image else None
        if seen[pid] is not None and seen[pid] == wanted:
            return hwnd
    return None


# ------------------------------------------------------------ Windows calls
# Kept small and separate so the matching above can be tested without them.

_API_READY = False


def _declare_api():
    """Give every call its real argument and result types: handles are
    pointer-sized, and ctypes would otherwise cut them to 32 bits."""
    global _API_READY
    if _API_READY:
        return
    user32, kernel32, dwmapi = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.dwmapi
    HWND, HANDLE, DWORD, BOOL, UINT = wintypes.HWND, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.UINT
    for function, argtypes, restype in (
        (user32.EnumWindows, [ctypes.c_void_p, wintypes.LPARAM], BOOL),
        (user32.IsWindowVisible, [HWND], BOOL),
        (user32.IsWindow, [HWND], BOOL),
        (user32.IsIconic, [HWND], BOOL),
        (user32.GetWindow, [HWND, UINT], HWND),
        (user32.GetWindowTextLengthW, [HWND], ctypes.c_int),
        (user32.GetWindowLongW, [HWND, ctypes.c_int], ctypes.c_long),
        (user32.GetWindowThreadProcessId, [HWND, ctypes.POINTER(DWORD)], DWORD),
        (user32.ShowWindow, [HWND, ctypes.c_int], BOOL),
        (user32.SetForegroundWindow, [HWND], BOOL),
        (user32.GetForegroundWindow, [], HWND),
        (user32.BringWindowToTop, [HWND], BOOL),
        (user32.AttachThreadInput, [DWORD, DWORD, BOOL], BOOL),
        (kernel32.GetCurrentThreadId, [], DWORD),
        (kernel32.OpenProcess, [DWORD, BOOL, DWORD], HANDLE),
        (kernel32.CloseHandle, [HANDLE], BOOL),
        (kernel32.QueryFullProcessImageNameW, [HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)], BOOL),
        (dwmapi.DwmGetWindowAttribute, [HWND, DWORD, ctypes.c_void_p, UINT], ctypes.c_long),
    ):
        function.argtypes = argtypes
        function.restype = restype
    try:
        kernel32.GetApplicationUserModelId.argtypes = [HANDLE, ctypes.POINTER(UINT), wintypes.LPWSTR]
        kernel32.GetApplicationUserModelId.restype = ctypes.c_long
    except AttributeError:
        pass
    _API_READY = True


def top_level_windows() -> list[tuple[int, int]]:
    """(window, process id) for switchable top-level windows, front to back."""
    if sys.platform != "win32":
        return []
    _declare_api()
    user32 = ctypes.windll.user32
    found: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _lparam):
        if hwnd and _is_switchable(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((int(hwnd), int(pid.value)))
        return True

    user32.EnumWindows(ctypes.cast(collect, ctypes.c_void_p), 0)
    return found


def _is_switchable(hwnd) -> bool:
    user32 = ctypes.windll.user32
    if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, _GW_OWNER):
        return False
    if user32.GetWindowTextLengthW(hwnd) == 0:
        return False
    if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
        return False
    cloaked = wintypes.DWORD()
    try:
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except OSError:
        return True
    return result != 0 or not cloaked.value  # unreadable counts as not cloaked


def _open_process(pid: int):
    _declare_api()
    return ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)


def process_image(pid: int) -> str | None:
    """The normalised full path of the program a process is running."""
    kernel32 = ctypes.windll.kernel32
    handle = _open_process(pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.normcase(os.path.abspath(buffer.value))
    finally:
        kernel32.CloseHandle(handle)


def process_app_id(pid: int) -> str | None:
    """The casefolded app id of a packaged process, or None for other programs."""
    kernel32 = ctypes.windll.kernel32
    handle = _open_process(pid)
    if not handle:
        return None
    try:
        length = wintypes.UINT(512)
        buffer = ctypes.create_unicode_buffer(length.value)
        if kernel32.GetApplicationUserModelId(handle, ctypes.byref(length), buffer) != 0:
            return None
        return buffer.value.casefold() or None
    except AttributeError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def activate(hwnd: int) -> bool:
    """Restore the window if minimised and bring it to the front.

    Windows only lets the foreground app hand focus on. Dash is in front
    while its launcher is open, so this must run before the launcher hides.
    If focus is still refused, the input queues are joined briefly, which
    Windows allows.
    """
    _declare_api()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
        return True
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    own_thread = kernel32.GetCurrentThreadId()
    attached = foreground_thread and foreground_thread != own_thread and user32.AttachThreadInput(own_thread, foreground_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(own_thread, foreground_thread, False)
    return bool(user32.IsWindow(hwnd))
