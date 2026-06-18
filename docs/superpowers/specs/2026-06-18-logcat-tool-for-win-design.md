# Logcat Tool for Win Design

Date: 2026-06-18

## Summary

Build a Windows GUI Android `logcat` tool with an embedded `adb` distribution, packaged as a portable ZIP. The application targets developers who need a stable, long-running desktop viewer for Android logs with multi-device switching, TCP device connection, filtering, highlighting, export, and common `adb` actions.

The implementation will use Python with Tkinter and a `src/` package layout modeled after the local `NetworkSwitcher` reference project. The GUI must remain responsive during continuous log streaming by isolating all `adb` and `logcat` process work from the Tkinter main loop.

## Approved Decisions

- Platform focus: Windows
- GUI stack: Python + Tkinter
- Distribution: portable ZIP only
- `adb` strategy: bundle `platform-tools` with the app
- Device scope: USB devices, already-connected TCP devices, and GUI-driven `adb connect IP:port`
- Product priority: long-running stability over visual richness or minimal package size
- Multi-device behavior: one active realtime stream at a time, with fast switching between devices

## Goals

- Let a user unzip the application and start capturing Android logs without separate `adb` installation.
- Show and switch between multiple detected devices from one desktop window.
- Keep the GUI responsive during long-running log capture sessions.
- Provide practical filtering for log level, tag, keyword, and saved presets.
- Surface `adb` failures and device state clearly, with recovery actions.
- Export either the filtered visible logs or the raw captured logs.
- Build the Windows executable and portable ZIP from GitHub Actions.

## Non-Goals

- Native Windows installer
- Automatic updates
- Simultaneous realtime streaming from multiple devices
- Deep log analysis features such as stack trace folding, statistics, or structured parsing
- Device management beyond `adb` discovery, connection, and server maintenance

## Primary User Flows

### 1. Start and capture logs

1. User launches the app from the portable folder.
2. App validates the bundled `adb` binary and starts or verifies the `adb` server.
3. App lists available devices with state and transport information.
4. User selects a device and presses `Start`.
5. App begins a single realtime `logcat` stream and renders logs in the main text view.

### 2. Connect a TCP device

1. User enters `IP:port` in the connection field.
2. App validates format before invoking `adb connect`.
3. If successful, the device list refreshes and the new device becomes selectable.
4. If failed, the error is shown near the input and in the status area with a retry path.

### 3. Filter and review logs

1. User adjusts level, tag, keyword, auto-scroll, and match-only filters.
2. App applies `adb`-side filtering where possible and GUI-side filtering for fast live refinement.
3. Matching lines are color-highlighted by severity and optional user keyword rules.

### 4. Recover from interruption

1. Device disconnects or `logcat` exits unexpectedly.
2. App updates status with the reason and stops the active stream cleanly.
3. App attempts bounded automatic reconnect for the current device.
4. If reconnect does not succeed, the user can retry manually without restarting the app.

## UX Direction

The UI should read as a restrained developer tool rather than a consumer app. Use a dark theme with strong contrast and a single green emphasis color, avoiding gimmicky terminal effects. Layout and spacing should be clean and grid-driven, closer to Swiss/minimal utility tooling than to themed cyberpunk styling.

### Layout

- Top toolbar:
  - Device selector
  - Refresh devices
  - TCP connect input and action
  - Start/Stop streaming
  - Clear view
  - Clear device `logcat`
  - Export logs
  - Restart `adb` server
  - Compact run state indicator
- Left control panel:
  - Device details and connection controls
  - Filter controls
  - Preset save/load controls
  - Auto-scroll and match-only toggles
- Center log pane:
  - Read-only scrolling text area
  - Severity-based color tags
  - Optional custom keyword highlight tags
  - Small summary row for total lines, visible lines, and stream state
- Bottom status bar:
  - Resolved `adb` path
  - Current device serial
  - Queue depth
  - Last error or reconnect status

### Interaction Rules

- Do not auto-start a stream when the device selection changes.
- Disable or gate invalid actions instead of letting them fail silently.
- Show validation and connection errors near the related control when possible.
- Keep keyboard shortcuts for common actions:
  - `Ctrl+F`: focus keyword filter
  - `Ctrl+L`: clear visible log area
  - `F5`: refresh devices
  - `Ctrl+E`: export logs

## Architecture

The application will follow a small-module desktop architecture. The Tkinter window owns only presentation and user interaction. All `adb` invocation, device parsing, log streaming, filtering, export, and persistence live in focused modules with explicit data structures.

### Package Layout

```text
src/logcat_tool_for_win/
├── __init__.py
├── __main__.py
├── adb.py
├── devices.py
├── export.py
├── filters.py
├── gui.py
├── highlight.py
├── log_stream.py
├── models.py
├── presets.py
└── resources/
    └── platform-tools/
        └── bundled Android SDK Platform Tools distribution
```

### Module Responsibilities

- `gui.py`
  - Build Tkinter UI
  - Bind commands and shortcuts
  - Poll stream queue with `root.after`
  - Reflect status changes and errors
- `adb.py`
  - Locate bundled `adb`
  - Execute general `adb` commands with timeout handling
  - Expose helpers for `devices`, `connect`, `logcat -c`, and server restart
- `devices.py`
  - Parse `adb devices -l`
  - Normalize transport, state, display label, and serial information
- `log_stream.py`
  - Start and stop one active `adb logcat` subprocess
  - Read stdout and stderr in background threads
  - Publish parsed log lines and status events through a thread-safe queue
- `filters.py`
  - Build `adb logcat` filter specs from selected levels and tags
  - Apply keyword and match-only filtering locally
- `highlight.py`
  - Define default severity colors
  - Apply user-defined keyword highlight rules
- `presets.py`
  - Save and load filter presets from JSON
  - Restore last-used filter state if available
- `export.py`
  - Export visible filtered lines or raw buffered lines to text files
- `models.py`
  - Dataclasses for devices, filters, log entries, presets, and status events

## Threading and Process Model

- Tkinter main loop runs on the main thread only.
- There is at most one active log stream session at a time.
- Starting a stream spawns one `adb logcat` subprocess for the selected device.
- Background reader threads consume subprocess stdout and stderr.
- Reader threads place parsed events into a `queue.Queue`.
- The GUI drains the queue on a timer and updates the visible log widget in batches.

This design avoids blocking the GUI on subprocess IO and reduces redraw churn by rendering in batches rather than line-by-line.

## Data Model

### DeviceInfo

- `serial`
- `display_name`
- `transport`
- `state`
- `model`
- `product`
- `raw_descriptor`

### FilterState

- `minimum_level`
- `tag_filters`
- `keyword`
- `match_only`
- `auto_scroll`

### LogEntry

- `timestamp_text`
- `level`
- `tag`
- `message`
- `raw_line`
- `matches_filters`
- `highlight_keys`

### HighlightRule

- `name`
- `pattern`
- `foreground`
- `background`
- `case_sensitive`

### AppStatus

- `adb_ready`
- `active_device_serial`
- `stream_state`
- `queue_depth`
- `last_error`
- `reconnect_attempt`

## Streaming and Buffering Strategy

The design prioritizes stability during long runs and high log volume.

- Maintain a raw ring buffer of the last `N` log lines.
- Maintain a separately rendered visible buffer for current filter results.
- Recompute only the visible slice needed for GUI refresh instead of rebuilding from scratch on every incoming line where possible.
- Batch text widget inserts to reduce UI overhead.
- Cap raw buffer growth to prevent unbounded memory usage.

Initial defaults:

- Raw ring buffer: 20,000 lines
- Visible rendered cap: 5,000 most recent matching lines
- GUI queue drain interval: 100 ms

These limits should be centralized in one settings object or constants module.

## Filtering and Highlighting

### `adb`-side filtering

Use `adb logcat` arguments for filtering that reduce source output volume:

- Severity threshold
- Exact tag filters, joined into a standard `logcat` filter spec

### GUI-side filtering

Apply the following locally for flexibility and rapid updates:

- Keyword search
- Match-only mode
- Highlight rule matching

### Highlight rules

Built-in severity colors:

- Verbose
- Debug
- Info
- Warn
- Error
- Fatal

User rules should support keyword-based matching first. Regex-based custom rules are explicitly deferred to keep first-version complexity under control.

## Error Handling

### Bundled `adb` missing or invalid

- Fail startup checks early
- Show a blocking explanation with the expected bundled path
- Do not enter a half-working main workflow

### Device states

- Show `device`, `offline`, and `unauthorized` directly in the device list
- Prevent starting a stream on unsupported states

### TCP connect

- Validate `IP:port` format before executing `adb connect`
- Display failures near the input and in the status area

### Stream termination

- Detect subprocess exit and stderr failures
- Stop readers cleanly
- Update state to disconnected or failed
- Attempt bounded automatic reconnect with 3 retries at a 2-second interval

### Export failures

- Report whether the failure came from empty data, invalid path, or write failure

## Persistence

Store local app settings in a JSON-backed configuration file inside a writable per-user app data location on Windows. Persist:

- Recent TCP target
- Last selected filter values
- Saved presets
- User-defined highlight rules

The app must tolerate missing or malformed config files by rebuilding defaults instead of crashing.

## Testing Strategy

Focus tests on behavior that is easy to regress and valuable to automate.

### Unit tests

- `adb devices -l` parsing
- `adb` command argument generation
- TCP target validation
- GUI-side keyword filtering behavior
- Severity and keyword highlight rule matching
- Preset serialization and deserialization
- Export output formatting

### Light integration tests

- Use sample `adb` output fixtures for devices and logs
- Verify stream parser events and queue publication
- Verify failure events trigger expected status transitions

### Manual verification targets

- Start/stop stream repeatedly
- Switch devices while a stream is active
- Disconnect and reconnect a device
- Connect a TCP device through the GUI
- Export filtered and raw logs
- Run the app for an extended session with sustained log output

## Packaging and Repository Structure

Repository setup:

- Standard git repository on branch `main`
- Python `src/` layout
- `README.md` with usage and troubleshooting
- `.github/workflows/` for CI and Windows packaging

Portable release contents:

- GUI executable
- Bundled `platform-tools`
- README or quick-start instructions

## GitHub Actions Plan

Two-job baseline workflow:

### `test`

- Run on `ubuntu-latest`
- Set up Python
- Install project and test dependencies
- Run `ruff`
- Run `pytest`

### `build-windows`

- Run on `windows-latest`
- Install dependencies including PyInstaller
- Acquire or stage bundled Android `platform-tools`
- Build the GUI executable
- Assemble the portable distribution folder
- Compress it as a ZIP artifact
- Upload the ZIP from the workflow run

The workflow must validate that the final packaged output includes both the application executable and the embedded `adb` directory.

## Security and Safety Considerations

- Do not execute arbitrary shell input outside explicit `adb` operations the UI exposes.
- Treat device serials and TCP targets as data, not shell fragments.
- Use `subprocess` argument lists rather than shell-joined command strings.
- Do not silently delete user files during export or log clearing.

## Acceptance Criteria

- App launches on Windows from a portable folder without external `adb` installation.
- App detects USB and TCP devices visible to `adb`.
- App can connect to a new TCP target from the GUI.
- App can start and stop one active realtime stream without freezing the UI.
- App can switch active devices cleanly.
- App supports level, tag, and keyword filtering plus saved presets.
- App applies built-in severity highlighting and user keyword highlight rules.
- App surfaces disconnects and retries reconnect with clear status.
- App exports visible logs and raw logs.
- GitHub Actions builds and uploads a Windows portable ZIP artifact.

## Implementation Notes for Planning

- Prefer conservative first-version scope over extra features that reduce stability.
- Keep Tkinter widget logic thin; avoid embedding business logic directly in callbacks.
- Use sample `adb` output fixtures early so parsing and filter tests can be written before implementation code.
