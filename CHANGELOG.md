# Changelog

All notable changes to Dash are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Dash uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- Light, dark and "follow Windows" themes.
- A choice of web search engine for the "search the web" offer.
- A "Download website icons" setting, to stop Dash fetching favicons.
- A list in Settings for managing all of your commands in one place.
- Actions on a result (right-click, the Menu key or Shift+F10): run as
  administrator, open the containing folder, copy the path or address, and
  open a new copy of an app that is already running.
- Arguments and "Start in" fields for app commands.
- Drag and drop a file, folder, shortcut or link onto Dash, or paste a file
  copied in Explorer, to add it as a command.
- When nothing matches, Dash offers to add the text as a command or to
  search the web for it.
- Size presets (Small, Medium, Large) in Settings.
- "Open Dash", "Settings..." and "Open Log Folder" in the tray menu.
- Recommended websites use your local Wikipedia and Amazon store.
- Recently used programs start ticked in the recommended-commands scan.
- Alias suggestions for programs found by the scan.
- A log file at `%APPDATA%\Dash\logs\dash.log`, and a message with the
  details when Dash hits an unexpected error instead of closing silently.
- Accessible names for controls, so screen readers can announce them.

### Changed

- The default hotkey is now `Alt+Space`. Dash registers it with Windows
  (`RegisterHotKey`), so the key press no longer reaches the app that had
  focus. Existing settings keep the hotkey you already chose.
- The launcher hides when it loses focus.
- Calculator results are friendlier (rounding, large and small numbers), and
  expressions are evaluated more safely.
- Matching also finds commands by the start of any word in their name, not
  only the first.
- Start Menu shortcuts keep their arguments when imported.
- Imported command files are checked before anything is added.
- US English spelling throughout, and dates follow your Windows locale.
- The installer no longer adds "Edit Settings" and "Edit Commands" Start
  Menu shortcuts that opened the raw files in Notepad; both are edited in
  Dash.

### Fixed

- A command whose name starts with everything you typed now comes before a
  site search that happens to share its first word, so "google maps" opens
  Google Maps rather than searching Google for "maps".
- "Most used first" sorting now orders results by how often you open them.
- A damaged settings or commands file no longer stops Dash from starting.
- Settings, commands and usage counts are saved atomically, so a crash or
  power loss during a save cannot leave a half-written file.
- Uninstalling: silent uninstalls (such as `winget uninstall`) no longer
  show a prompt and always keep your data; the prompt now asks "Also delete
  your Dash settings and commands?" with No as the default; nothing is
  deleted until the uninstall has finished; and installers downloaded by
  the updater are removed.

## [2.10.0] and earlier

See [GitHub Releases](https://github.com/CalemYoung/Dash/releases) for the
notes on each earlier version.

[Unreleased]: https://github.com/CalemYoung/Dash/compare/v2.10.0...HEAD
[2.10.0]: https://github.com/CalemYoung/Dash/releases/tag/v2.10.0
