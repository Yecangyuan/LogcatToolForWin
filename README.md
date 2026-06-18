# Logcat Tool for Win

Windows GUI Android `logcat` viewer with bundled `adb`, multi-device switching, filtering, export, and GitHub Actions packaging.

## Features

- Windows-first Tkinter desktop app with a single-click portable ZIP release
- Bundled `adb` support for packaged builds
- USB device discovery plus TCP `adb connect` support
- One active realtime log stream with fast device switching
- `logcat` level filtering plus tag and keyword filtering
- Highlight rules, auto-scroll control, and match-only view mode
- Export visible or raw logs to text files
- Session-state and preset persistence between launches

## Run From Source

Install development dependencies:

```bash
python3.11 -m pip install --upgrade pip
python3.11 -m pip install -e .[dev]
```

Launch the app from source:

```bash
python3.11 -m logcat_tool_for_win
```

The source build resolves `adb.exe` in this order:

1. `LOGCAT_TOOL_ADB`
2. `src/logcat_tool_for_win/resources/platform-tools/adb.exe`

If you want to run with a bundled local copy, place the official Android platform-tools directory here:

```text
src/logcat_tool_for_win/resources/platform-tools/
```

If you already have `adb.exe` elsewhere, point the app at it explicitly:

```bash
LOGCAT_TOOL_ADB="C:\\Android\\platform-tools\\adb.exe" python3.11 -m logcat_tool_for_win
```

Packaged builds use a different path: when frozen with PyInstaller, the app looks for `platform-tools/adb.exe` next to the generated executable. The portable ZIP builder copies that directory into the final release layout automatically.

## Development

Core development commands:

```bash
python3.11 -m pip install -e .[dev]
pytest -q
ruff check .
python3.11 -m build
pyinstaller --noconfirm --clean logcat-tool-for-win.spec
python3.11 scripts/build_portable.py --dist-root dist --output-root artifacts
```

What each command does:

- `pytest -q`: runs the automated test suite
- `ruff check .`: runs linting
- `python3.11 -m build`: produces the sdist and wheel in `dist/`
- `pyinstaller --noconfirm --clean logcat-tool-for-win.spec`: builds the Windows app directory in `dist/logcat-tool-for-win/`
- `python3.11 scripts/build_portable.py --dist-root dist --output-root artifacts`: assembles `artifacts/logcat-tool-for-win.zip` with the built app, `platform-tools`, and this README

## Portable Build Flow

To produce the same output locally that CI packages on Windows:

1. Download the official Android Windows platform-tools ZIP from Google.
2. Extract it into `src/logcat_tool_for_win/resources/` so that `adb.exe` ends up at `src/logcat_tool_for_win/resources/platform-tools/adb.exe`.
3. Run the PyInstaller build command.
4. Run `scripts/build_portable.py` to create `artifacts/logcat-tool-for-win.zip`.

## GitHub Actions

`.github/workflows/ci.yml` runs on `push` and `pull_request` targeting `main`.

- `test` runs on Ubuntu and performs install, lint, pytest, and `python -m build`.
- `build-windows` runs on Windows after `test` passes.
- The Windows job downloads the official Android platform-tools ZIP from Google, stages it under `src/logcat_tool_for_win/resources/platform-tools/`, builds the app with PyInstaller, packages the portable ZIP, and uploads `artifacts/logcat-tool-for-win.zip`.

This workflow uploads a build artifact for every qualifying run. It does not create a GitHub Release by itself.

## Troubleshooting

`adb` is reported as missing:
- Confirm `LOGCAT_TOOL_ADB` points to a real `adb.exe`, or confirm `src/logcat_tool_for_win/resources/platform-tools/adb.exe` exists when running from source.
- For packaged builds, confirm `platform-tools/adb.exe` sits next to the built executable directory contents.

No devices appear:
- Verify the device is visible in a normal `adb devices -l` session.
- Accept any device authorization prompt on the Android device.
- Use the app's `Refresh` button after connecting USB or a TCP target.

TCP connect fails:
- Enter targets as `IP:port`.
- Confirm the device is already listening for TCP `adb` and reachable from the Windows machine.

PyInstaller build succeeds but portable ZIP creation fails:
- Confirm `dist/logcat-tool-for-win/` exists.
- Confirm the Windows platform-tools archive was extracted before running `scripts/build_portable.py`.

Tkinter import errors:
- Use a Python 3.11 installation that includes Tk support. Some minimal Python environments omit it.
