# src/__init__.py
#
# Submodules are imported lazily. The GUI modules pull in PyQt6 and pywin32,
# and PyHotKey installs a keyboard hook the moment it is imported, so the
# pure-Python parts (trie, calculator, settings, command manager) must be
# importable without dragging those in.

__all__ = ["CommandTrie", "CommandManager", "Settings", "MainWindow", "HotkeyListener", "eval_expression", "IconManager"]

_EXPORTS = {
    "CommandTrie": ".command_trie",
    "CommandManager": ".command_manager",
    "Settings": ".settings",
    "MainWindow": ".launcher_gui",
    "HotkeyListener": ".launcher_hotkey",
    "eval_expression": ".calculator",
    "IconManager": ".icon_manager",
}


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name, __name__), name)
