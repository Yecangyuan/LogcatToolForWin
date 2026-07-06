# Logcat Tool for Win

Windows GUI Android `logcat` viewer with bundled `adb`, multi-device switching, filtering, export, and GitHub Actions packaging.

## Features

- Windows-first Simplified Chinese Tkinter desktop app with a single-click portable ZIP release
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

Packaged builds use a different path. When frozen with PyInstaller, the app first looks for bundled `platform-tools/adb.exe` inside the frozen app, then falls back to `platform-tools/adb.exe` next to the generated executable for legacy folder-style builds.

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
- `pyinstaller --noconfirm --clean logcat-tool-for-win.spec`: builds the self-contained Windows executable `dist/logcat-tool-for-win.exe`
- `python3.11 scripts/build_portable.py --dist-root dist --output-root artifacts`: assembles `artifacts/logcat-tool-for-win.zip` with the built executable and this README

## Portable Build Flow

To produce the same output locally that CI packages on Windows:

1. Download the official Android Windows platform-tools ZIP from Google.
2. Extract it into `src/logcat_tool_for_win/resources/` so that `adb.exe` ends up at `src/logcat_tool_for_win/resources/platform-tools/adb.exe`.
3. Run the PyInstaller build command so the executable embeds those tools.
4. Run `scripts/build_portable.py` to create `artifacts/logcat-tool-for-win.zip`.

## GitHub Actions

`.github/workflows/ci.yml` runs on pushes to `main`, pushes of tags matching `v*`, and pull requests targeting `main`.

- `test` runs on Ubuntu and performs install, lint, pytest, and `python -m build`.
- `build-windows` runs on Windows after `test` passes.
- The Windows job downloads the official Android platform-tools ZIP from Google, stages it under `src/logcat_tool_for_win/resources/platform-tools/`, builds the self-contained app with PyInstaller, packages the portable ZIP, and uploads `artifacts/logcat-tool-for-win.zip`.
- `build-windows-legacy` runs a best-effort legacy build on `windows-2022` with `Python 3.8`, embeds Android platform-tools `r28.0.2`, and publishes `artifacts-legacy/logcat-tool-for-win-legacy-win7.zip`.
- Pushing to `main` publishes both Windows ZIPs to a rolling `latest` GitHub Release, replacing the existing assets.
- Pushing a tag that matches `v*` such as `v0.1.0` publishes both Windows ZIPs to the matching versioned GitHub Release as assets.

This workflow uploads build artifacts for every qualifying run. For `main` pushes and `v*` tags, it also creates or updates the matching GitHub Release assets.

Current rolling release page:
- `https://github.com/Yecangyuan/LogcatToolForWin/releases/tag/latest`

## Troubleshooting

`adb` is reported as missing:
- Confirm `LOGCAT_TOOL_ADB` points to a real `adb.exe`, or confirm `src/logcat_tool_for_win/resources/platform-tools/adb.exe` exists when running from source.
- For packaged builds, use a current build created from this repository so `platform-tools` is embedded. Legacy folder-style builds still require `platform-tools/adb.exe` next to the executable.

No devices appear:
- Verify the device is visible in a normal `adb devices -l` session.
- Accept any device authorization prompt on the Android device.
- Use the app's `刷新` button after connecting USB or a TCP target.

TCP connect fails:
- Enter targets as `IP` or `IP:port`; when the port is omitted, the app uses `5555`.
- The `连接` button now tries direct TCP first. If the first direct connection fails, the app will automatically retry the same `IP:port` after enabling wireless ADB on the currently selected authorized USB device (`自动尝试为当前选中的 USB 设备开启无线 ADB 后再重连`).
- To prepare a USB device for wireless ADB explicitly, select the USB device and click `USB 开启无线`. The app runs `adb tcpip 5555`, tries to detect the device Wi-Fi IP, then connects to `IP:5555` and refreshes the device list.
- If the automatic USB fallback succeeds, the status text will mention `首次直连失败` and that it has automatically retried after enabling wireless ADB for the selected USB device.
- Confirm the device is already listening for TCP `adb` and reachable from the Windows machine.

PyInstaller build succeeds but portable ZIP creation fails:
- Confirm `dist/logcat-tool-for-win.exe` exists.
- Confirm the Windows platform-tools archive was extracted before running `scripts/build_portable.py`.

Tkinter import errors:
- Use a Python 3.11 installation that includes Tk support. Some minimal Python environments omit it.

`failed to load python dll python311.dll` at launch:
- Use the current portable build and extract the ZIP before running it.
- Windows 8.1 or newer is required for Python 3.11-based builds.

Running on Windows 7 or Windows 8.0:
- Use the `logcat-tool-for-win-legacy-win7.zip` asset built from the `Python 3.8` legacy workflow.
- The legacy executable embeds Android platform-tools `r28.0.2` instead of the latest `adb`.
- This legacy build is best-effort only until it is validated on a real Windows 7 or Windows 8.0 machine.
