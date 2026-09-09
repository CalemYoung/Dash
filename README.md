<h1 align="center">
   <img src="assets/banner.png" alt="Dash" width="320">
</h1>

<p align="center">
   <strong>A fast, keyboard-first launcher for Windows.</strong>
</p>

<p align="center">
   Open apps, files, folders, and websites without leaving your keyboard.
</p>

<p align="center">
   <a href="https://github.com/CalemYoung/Dash/releases"><img src="https://img.shields.io/badge/download-Releases-2EA44F?style=flat-square&amp;logo=github" alt="Download from GitHub Releases"></a>
   <img src="https://img.shields.io/badge/platform-Windows-0078D4?style=flat-square" alt="Windows">
   <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.11 or later">
   <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2EA44F?style=flat-square" alt="MIT License"></a>
</p>

---

## Features

- **Instant access:** Open Dash from anywhere with the global `Alt+F` hotkey.
- **Flexible commands:** Launch applications, files, folders, and URLs from one search box.
- **Fast matching:** Find commands by name or add aliases for the terms you naturally type.
- **Quick calculations:** Evaluate mathematical expressions without opening another app.
- **Command tree:** Optionally show a side panel that maps how each letter you type narrows the matches. Turn it on under Settings.
- **Made to fit:** Customize commands, icons, settings, and startup behavior.

## See Dash in action

### Search and launch

Type a few letters and the name completes inline. Aliases work too, and
calculations can also be done.

<p align="left">
   <img src="assets/launcher-search.gif" alt="Typing into Dash: matches narrow as you type, an alias opens GitHub, and 12*8 shows a calculator result" width="600">
</p>

### Manage commands

Press Ctrl+Enter on a result to edit it in place: name, description, type,
target, aliases and icon.

<p align="left">
   <img src="assets/command-editor.gif" alt="Opening the GitHub command in the editor and adding an alias" width="420">
</p>

### Command tree

An optional side panel that shows how each letter you type narrows the
matches. Turn it on under Settings.

<p align="left">
   <img src="assets/command-tree.gif" alt="The command tree panel narrowing from s to spotify" width="780">
</p>

## Install

1. Download `DashSetup-<version>.exe` from the
   [Releases page](https://github.com/CalemYoung/Dash/releases).
2. Run the installer and choose whether Dash should create a desktop shortcut
    or start when you sign in.
3. Press `Alt+F` to open Dash.

Use the system tray menu to open settings or manage commands. Dash stores your
configuration in `%APPDATA%\Dash`, so upgrades keep your settings and commands.

Dash checks GitHub for new releases when it starts. By default it downloads
the installer in the background, verifies it against the release's SHA-256
checksum, and installs it while the launcher is hidden, restarting itself
afterwards. Turn off "Install updates automatically" in Settings to be asked
first, or use "Check for Updates..." in the tray menu at any time.

## Development

Dash requires Windows and Python 3.11 or later. To run it from source:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python dash.py
```

<details>
<summary><strong>Build the Windows installer</strong></summary>

<br>

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then run:

```powershell
.\.venv\Scripts\python.exe build\scripts\build_installer.py
```

The build reads its version from `build/installer/version.txt` and writes the
installer to `dist/DashSetup-<version>.exe`.

</details>

<details>
<summary><strong>Publish a GitHub release</strong></summary>

<br>

Releases are built on GitHub's Windows runner. The workflow compiles the
PyInstaller bundle with Inno Setup, verifies the installer, and attaches it to
the GitHub Release. `build/installer/version.txt` is the only
place the version is stored; the build stamps it into the exe and installer.

1. Update `build/installer/version.txt` to the intended version, such as
   `2.1.0`, and commit it.
2. Create and push a matching version tag:

   ```powershell
   git tag v2.1.0
   git push origin v2.1.0
   ```

3. Wait for the **Release** workflow to complete. The resulting
   `DashSetup-2.1.0.exe` is available under GitHub Releases to share with
   others.

The tag must match `v` plus the version in `build/installer/version.txt`.
Each run also produces a 14-day Actions artifact to help diagnose a failed
release build.

</details>

## License

Dash is licensed under the [MIT License](LICENSE).
