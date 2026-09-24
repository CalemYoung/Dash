# Contributing to Dash

Thanks for helping. Bug reports, ideas and pull requests are all welcome.
For anything larger than a small fix, please open an issue first so we can
agree on the approach before you spend time on it.

## Development setup

Dash runs on Windows with Python 3.11 or later.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python dash.py
```

`requirements.txt` holds what Dash needs to run. `requirements-dev.txt`
adds the tools for testing and building (pytest and PyInstaller) and
includes `requirements.txt`.

When run from source, Dash reads and writes `config\settings.toml` and
`config\commands.toml` in the repository. Both are ignored by git; the
shipped defaults are the `*.default.toml` files next to them.

## Running the tests

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
```

`QT_QPA_PLATFORM=offscreen` lets the widget tests run without opening
windows. The tests use `unittest` classes, and pytest runs them. They must
not depend on your own `config\commands.toml` or `%APPDATA%\Dash`: copy
`config\commands.default.toml` into a temporary folder instead (see
`tests\test_feature_ui.py`).

There is no CI on pull requests, so please run the tests before you open
one.

## Code style

- Follow the style of the code around your change: PEP 8, four-space
  indents, type hints on new functions, and `pathlib.Path` for paths.
- Docstrings and comments explain why, in plain sentences.
- Windows-only modules (`winreg`, `win32*`, `ctypes.windll`) are imported
  inside functions or behind a `sys.platform == "win32"` check, so the
  pure-Python parts stay importable and testable elsewhere.
- `src/__init__.py` imports its modules lazily. PyInstaller finds them
  through `collect_submodules('src')` in `dash.spec`, so a new module under
  `src/` is bundled without changes to the spec. A new data file does need
  adding to `datas` in `dash.spec` (and, if it belongs outside the bundle, to
  `build\installer\installer.iss`).
- Write user-facing text in US English. Name controls with
  `setAccessibleName` where the visible label is not enough.
- Test names read as sentences that describe the behavior, for example
  `test_a_late_favicon_does_not_take_over_an_icon_of_your_own`.
- Add an entry under "Unreleased" in `CHANGELOG.md` for any change a user
  would notice.

## Building the installer

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then run:

```powershell
python build\scripts\build_installer.py
```

The build reads its version from `build\installer\version.txt` and writes
`dist\DashSetup-<version>.exe`.

## Release process

1. Move the "Unreleased" entries in `CHANGELOG.md` under a heading for the
   new version.
2. Set `build\installer\version.txt` to the new version and commit.
3. Tag the commit `v<version>` and push the tag. The tag must match
   `version.txt`.
4. The [Release workflow](.github/workflows/release.yml) builds the
   installer on a Windows runner, writes its `.sha256` checksum (the in-app
   updater refuses an installer without one), and publishes both to a
   GitHub Release.
5. The same workflow then submits the new version to winget with
   [winget-releaser](https://github.com/vedantmgoyal9/winget-releaser), if
   the `WINGET_TOKEN` secret is set. It leaves a warning on the run when the
   secret is missing.

### winget

winget-releaser generates each new version's manifests from the GitHub
Release and the previous version in
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs), so they
are not maintained in this repository. It can only update a package that is
already there.

`packaging\winget\manifests` holds the manifests for the first submission of
`CalemYoung.Dash`, with `InstallerSha256` already filled in from the
release's `.sha256` file. To submit them (or a newer version, after updating
the version and hash to match its release), check them with
`winget validate --manifest <folder>` and `winget install --manifest
<folder>`, and open a pull request to winget-pkgs. Once that version is
merged, later versions are handled by the workflow and the folder can be
deleted.
