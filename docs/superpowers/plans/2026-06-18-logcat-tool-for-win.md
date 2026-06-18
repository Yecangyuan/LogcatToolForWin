# Logcat Tool for Win Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable Windows Tkinter `logcat` GUI with bundled `adb`, portable ZIP packaging, and GitHub Actions artifacts.

**Architecture:** A thin Tkinter shell coordinates focused modules for config, `adb`, device parsing, stream management, filtering, highlighting, persistence, and export. A single active `adb logcat` subprocess feeds a queue drained by the GUI in batches so the main loop stays responsive during long sessions.

**Tech Stack:** Python 3.11+, Tkinter, pytest, ruff, PyInstaller, GitHub Actions, GitHub CLI

---

### Task 1: Scaffold The Python Project

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/logcat_tool_for_win/__init__.py`
- Create: `src/logcat_tool_for_win/__main__.py`
- Create: `tests/test_package_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

```python
from logcat_tool_for_win import __version__
from logcat_tool_for_win.__main__ import main


def test_package_smoke() -> None:
    assert __version__ == "0.1.0"
    assert callable(main)
```

- [ ] **Step 2: Run the smoke test to verify it fails**

Run: `rtk pytest tests/test_package_smoke.py -v`
Expected: `ModuleNotFoundError: No module named 'logcat_tool_for_win'`

- [ ] **Step 3: Create the minimal package and project metadata**

`.gitignore`
```gitignore
__pycache__/
.pytest_cache/
.ruff_cache/
build/
dist/
*.egg-info/
artifacts/
src/logcat_tool_for_win/resources/platform-tools/*
!src/logcat_tool_for_win/resources/platform-tools/.gitkeep
```

`pyproject.toml`
```toml
[build-system]
requires = ["setuptools>=69.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "logcat-tool-for-win"
version = "0.1.0"
description = "Windows GUI Android logcat viewer with bundled adb."
readme = "README.md"
requires-python = ">=3.11"
license = {text = "MIT"}
authors = [{name = "Simley"}]
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "ruff>=0.5.0",
  "build>=1.2.2",
  "pyinstaller>=6.10",
]

[project.scripts]
logcat-tool-for-win = "logcat_tool_for_win.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

`README.md`
````markdown
# Logcat Tool for Win

Windows GUI Android `logcat` viewer with bundled `adb`, multi-device switching, filtering, export, and GitHub Actions packaging.
```

`src/logcat_tool_for_win/__init__.py`
```python
__version__ = "0.1.0"
```

`src/logcat_tool_for_win/__main__.py`
```python
from __future__ import annotations


def main() -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the smoke test to verify it passes**

Run: `rtk pytest tests/test_package_smoke.py -v`
Expected: `1 passed`

- [ ] **Step 5: Commit the scaffold**

```bash
rtk git add .gitignore pyproject.toml README.md src/logcat_tool_for_win/__init__.py src/logcat_tool_for_win/__main__.py tests/test_package_smoke.py
rtk git commit -m "chore: scaffold python project"
```

### Task 2: Add Shared Models And Local Config Paths

**Files:**
- Create: `src/logcat_tool_for_win/config.py`
- Create: `src/logcat_tool_for_win/models.py`
- Create: `tests/test_models_and_config.py`

- [ ] **Step 1: Write the failing config and dataclass tests**

```python
from pathlib import Path

from logcat_tool_for_win.config import (
    APP_DIRNAME,
    QUEUE_DRAIN_MS,
    RAW_LOG_CAP,
    VISIBLE_LOG_CAP,
    get_config_dir,
    get_state_file,
)
from logcat_tool_for_win.models import AppStatus, DeviceInfo, FilterState


def test_get_config_dir_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = get_config_dir()
    assert path == tmp_path / APP_DIRNAME
    assert path.exists()


def test_default_caps_match_design() -> None:
    assert RAW_LOG_CAP == 20_000
    assert VISIBLE_LOG_CAP == 5_000
    assert QUEUE_DRAIN_MS == 100


def test_models_have_stable_defaults() -> None:
    device = DeviceInfo(
        serial="R58M12345",
        display_name="Pixel 8",
        transport="usb",
        state="device",
        model="Pixel 8",
        product="shiba",
        raw_descriptor="sample",
    )
    filters = FilterState()
    status = AppStatus()

    assert device.transport == "usb"
    assert filters.minimum_level == "V"
    assert filters.auto_scroll is True
    assert status.stream_state == "idle"
    assert get_state_file().name == "state.json"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_models_and_config.py -v`
Expected: import failure for `logcat_tool_for_win.config` and `logcat_tool_for_win.models`

- [ ] **Step 3: Implement shared models and config helpers**

`src/logcat_tool_for_win/config.py`
```python
from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = "LogcatToolForWin"
RAW_LOG_CAP = 20_000
VISIBLE_LOG_CAP = 5_000
QUEUE_DRAIN_MS = 100


def get_config_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    path = root / APP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_state_file() -> Path:
    return get_config_dir() / "state.json"


def get_presets_file() -> Path:
    return get_config_dir() / "presets.json"
```

`src/logcat_tool_for_win/models.py`
```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DeviceInfo:
    serial: str
    display_name: str
    transport: str
    state: str
    model: str
    product: str
    raw_descriptor: str


@dataclass(slots=True)
class FilterState:
    minimum_level: str = "V"
    tag_filters: tuple[str, ...] = ()
    keyword: str = ""
    match_only: bool = False
    auto_scroll: bool = True


@dataclass(slots=True)
class HighlightRule:
    name: str
    pattern: str
    foreground: str
    background: str = ""
    case_sensitive: bool = False


@dataclass(slots=True)
class LogEntry:
    timestamp_text: str
    level: str
    tag: str
    message: str
    raw_line: str
    matches_filters: bool = True
    highlight_keys: tuple[str, ...] = ()


@dataclass(slots=True)
class AppStatus:
    adb_ready: bool = False
    active_device_serial: str = ""
    stream_state: str = "idle"
    queue_depth: int = 0
    last_error: str = ""
    reconnect_attempt: int = 0


@dataclass(slots=True)
class StreamEvent:
    kind: str
    entry: LogEntry | None = None
    message: str = ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk pytest tests/test_models_and_config.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit the shared foundations**

```bash
rtk git add src/logcat_tool_for_win/config.py src/logcat_tool_for_win/models.py tests/test_models_and_config.py
rtk git commit -m "feat: add shared models and config helpers"
```

### Task 3: Parse `adb devices -l` Output

**Files:**
- Create: `src/logcat_tool_for_win/devices.py`
- Create: `tests/test_devices.py`

- [ ] **Step 1: Write the failing device parsing tests**

```python
from logcat_tool_for_win.devices import parse_devices_output


def test_parse_devices_output_handles_usb_tcp_and_bad_states() -> None:
    output = """List of devices attached
R58M12345\tdevice usb:1-1 product:shiba model:Pixel_8 device:shiba transport_id:5
192.168.0.15:5555\tdevice product:husky model:Pixel_8_Pro transport_id:7
emulator-5554\toffline transport_id:9
ZX1G22ABC\tunauthorized usb:1-2 transport_id:11
"""

    devices = parse_devices_output(output)

    assert [device.serial for device in devices] == [
        "R58M12345",
        "192.168.0.15:5555",
        "emulator-5554",
        "ZX1G22ABC",
    ]
    assert devices[0].transport == "usb"
    assert devices[1].transport == "tcp"
    assert devices[2].state == "offline"
    assert devices[3].display_name == "ZX1G22ABC"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_devices.py -v`
Expected: import failure for `logcat_tool_for_win.devices`

- [ ] **Step 3: Implement device parsing**

`src/logcat_tool_for_win/devices.py`
```python
from __future__ import annotations

from logcat_tool_for_win.models import DeviceInfo


def parse_devices_output(output: str) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached"):
            continue

        parts = line.split()
        serial = parts[0]
        state = parts[1]
        attrs: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                attrs[key] = value

        model = attrs.get("model", "")
        product = attrs.get("product", "")
        display_name = model or product or serial
        transport = "tcp" if ":" in serial else "usb"

        devices.append(
            DeviceInfo(
                serial=serial,
                display_name=display_name,
                transport=transport,
                state=state,
                model=model,
                product=product,
                raw_descriptor=line,
            )
        )

    return devices


def device_label(device: DeviceInfo) -> str:
    return f"{device.display_name} [{device.transport}]"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk pytest tests/test_devices.py -v`
Expected: `1 passed`

- [ ] **Step 5: Commit device parsing**

```bash
rtk git add src/logcat_tool_for_win/devices.py tests/test_devices.py
rtk git commit -m "feat: parse adb device output"
```

### Task 4: Add Filtering And Highlight Logic

**Files:**
- Create: `src/logcat_tool_for_win/filters.py`
- Create: `src/logcat_tool_for_win/highlight.py`
- Create: `tests/test_filters_highlight.py`

- [ ] **Step 1: Write the failing filter and highlight tests**

```python
from logcat_tool_for_win.filters import build_logcat_filter_spec, entry_matches, normalize_tag_filters
from logcat_tool_for_win.highlight import DEFAULT_LEVEL_COLORS, match_highlight_rules
from logcat_tool_for_win.models import FilterState, HighlightRule, LogEntry


def test_build_logcat_filter_spec_uses_exact_tag_filters() -> None:
    assert build_logcat_filter_spec("I", ("ActivityManager", "MyApp")) == [
        "ActivityManager:I",
        "MyApp:I",
        "*:S",
    ]


def test_entry_matches_applies_keyword_and_level() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(minimum_level="W", tag_filters=("MyApp",), keyword="crash", match_only=True)
    assert entry_matches(entry, state) is True


def test_match_highlight_rules_returns_matching_names() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="ANR detected",
        raw_line="ANR detected",
    )
    rules = [HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00")]
    assert "ANR" in match_highlight_rules(entry, rules)
    assert DEFAULT_LEVEL_COLORS["E"] == "#ff6b6b"


def test_normalize_tag_filters_removes_blanks_and_duplicates() -> None:
    assert normalize_tag_filters("MyApp, ActivityManager, MyApp , ") == (
        "ActivityManager",
        "MyApp",
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_filters_highlight.py -v`
Expected: import failure for `logcat_tool_for_win.filters` and `logcat_tool_for_win.highlight`

- [ ] **Step 3: Implement filtering and highlight helpers**

`src/logcat_tool_for_win/filters.py`
```python
from __future__ import annotations

from logcat_tool_for_win.models import FilterState, LogEntry

LEVEL_ORDER = ("V", "D", "I", "W", "E", "F")


def normalize_tag_filters(raw: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in raw.split(",") if item.strip()}))


def build_logcat_filter_spec(minimum_level: str, tag_filters: tuple[str, ...]) -> list[str]:
    level = minimum_level.upper()
    if tag_filters:
        return [f"{tag}:{level}" for tag in tag_filters] + ["*:S"]
    return [f"*:{level}"]


def entry_matches(entry: LogEntry, state: FilterState) -> bool:
    level_ok = LEVEL_ORDER.index(entry.level) >= LEVEL_ORDER.index(state.minimum_level)
    tag_ok = not state.tag_filters or entry.tag in state.tag_filters
    if not state.keyword:
        keyword_ok = True
    else:
        haystack = " ".join([entry.tag, entry.message, entry.raw_line]).lower()
        keyword_ok = state.keyword.lower() in haystack
    return level_ok and tag_ok and keyword_ok
```

`src/logcat_tool_for_win/highlight.py`
```python
from __future__ import annotations

from logcat_tool_for_win.models import HighlightRule, LogEntry

DEFAULT_LEVEL_COLORS = {
    "V": "#94a3b8",
    "D": "#60a5fa",
    "I": "#22c55e",
    "W": "#facc15",
    "E": "#ff6b6b",
    "F": "#ff3b30",
}


def match_highlight_rules(entry: LogEntry, rules: list[HighlightRule]) -> tuple[str, ...]:
    matches: list[str] = []
    for rule in rules:
        source = entry.raw_line if rule.case_sensitive else entry.raw_line.lower()
        pattern = rule.pattern if rule.case_sensitive else rule.pattern.lower()
        if pattern and pattern in source:
            matches.append(rule.name)
    return tuple(matches)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk pytest tests/test_filters_highlight.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit filtering support**

```bash
rtk git add src/logcat_tool_for_win/filters.py src/logcat_tool_for_win/highlight.py tests/test_filters_highlight.py
rtk git commit -m "feat: add filter and highlight helpers"
```

### Task 5: Save Session State, Named Presets, And Export Logs

**Files:**
- Create: `src/logcat_tool_for_win/export.py`
- Create: `src/logcat_tool_for_win/presets.py`
- Create: `tests/test_presets_export.py`

- [ ] **Step 1: Write the failing preset and export tests**

```python
from pathlib import Path

from logcat_tool_for_win.export import export_lines
from logcat_tool_for_win.models import FilterState, HighlightRule
from logcat_tool_for_win.presets import load_presets, load_state, save_preset, save_state


def test_save_and_load_state_round_trip(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    filters = FilterState(minimum_level="W", tag_filters=("MyApp",), keyword="crash", match_only=True)
    rules = [HighlightRule(name="Crash", pattern="crash", foreground="#ff6b6b")]

    save_state(state_file, filters, rules, "10.0.0.5:5555")
    loaded_filters, loaded_rules, recent_target = load_state(state_file)

    assert loaded_filters.minimum_level == "W"
    assert loaded_filters.tag_filters == ("MyApp",)
    assert loaded_rules[0].name == "Crash"
    assert recent_target == "10.0.0.5:5555"


def test_save_and_load_named_presets_round_trip(tmp_path: Path) -> None:
    presets_file = tmp_path / "presets.json"
    save_preset(presets_file, "Errors", FilterState(minimum_level="E", tag_filters=("MyApp",)))
    save_preset(presets_file, "Warnings", FilterState(minimum_level="W", keyword="slow"))

    presets = load_presets(presets_file)

    assert sorted(presets) == ["Errors", "Warnings"]
    assert presets["Errors"].minimum_level == "E"
    assert presets["Warnings"].keyword == "slow"


def test_export_lines_writes_text_file(tmp_path: Path) -> None:
    output = tmp_path / "logs.txt"
    export_lines(output, ["line one", "line two"])
    assert output.read_text(encoding="utf-8").splitlines() == ["line one", "line two"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_presets_export.py -v`
Expected: import failure for `logcat_tool_for_win.presets` and `logcat_tool_for_win.export`

- [ ] **Step 3: Implement state persistence and export**

`src/logcat_tool_for_win/presets.py`
```python
from __future__ import annotations

import json
from pathlib import Path

from logcat_tool_for_win.models import FilterState, HighlightRule


def _filters_to_payload(filters: FilterState) -> dict[str, object]:
    return {
        "minimum_level": filters.minimum_level,
        "tag_filters": list(filters.tag_filters),
        "keyword": filters.keyword,
        "match_only": filters.match_only,
        "auto_scroll": filters.auto_scroll,
    }


def _filters_from_payload(payload: dict[str, object]) -> FilterState:
    return FilterState(
        minimum_level=str(payload.get("minimum_level", "V")),
        tag_filters=tuple(payload.get("tag_filters", [])),
        keyword=str(payload.get("keyword", "")),
        match_only=bool(payload.get("match_only", False)),
        auto_scroll=bool(payload.get("auto_scroll", True)),
    )


def save_preset(path: Path, name: str, filters: FilterState) -> None:
    presets = load_presets(path)
    presets[name] = filters
    payload = {preset_name: _filters_to_payload(state) for preset_name, state in presets.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_presets(path: Path) -> dict[str, FilterState]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {name: _filters_from_payload(state) for name, state in payload.items()}


def save_state(
    path: Path,
    filters: FilterState,
    rules: list[HighlightRule],
    recent_target: str,
) -> None:
    payload = {
        "filters": _filters_to_payload(filters),
        "highlight_rules": [
            {
                "name": rule.name,
                "pattern": rule.pattern,
                "foreground": rule.foreground,
                "background": rule.background,
                "case_sensitive": rule.case_sensitive,
            }
            for rule in rules
        ],
        "recent_target": recent_target,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_state(path: Path) -> tuple[FilterState, list[HighlightRule], str]:
    if not path.exists():
        return FilterState(), [], ""

    payload = json.loads(path.read_text(encoding="utf-8"))
    filters = _filters_from_payload(payload.get("filters", {}))
    rules = [HighlightRule(**item) for item in payload.get("highlight_rules", [])]
    recent_target = payload.get("recent_target", "")
    return filters, rules, recent_target
```

`src/logcat_tool_for_win/export.py`
```python
from __future__ import annotations

from pathlib import Path


def export_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        raise ValueError("No log lines available to export.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk pytest tests/test_presets_export.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit preset persistence**

```bash
rtk git add src/logcat_tool_for_win/presets.py src/logcat_tool_for_win/export.py tests/test_presets_export.py
rtk git commit -m "feat: add preset persistence and export helpers"
```

### Task 6: Wrap `adb` Execution And Command Construction

**Files:**
- Create: `src/logcat_tool_for_win/adb.py`
- Create: `src/logcat_tool_for_win/resources/platform-tools/.gitkeep`
- Create: `tests/test_adb.py`

- [ ] **Step 1: Write the failing `adb` wrapper tests**

```python
from pathlib import Path

import pytest

from logcat_tool_for_win.adb import build_logcat_command, resolve_adb_path, validate_tcp_target
from logcat_tool_for_win.models import FilterState


def test_validate_tcp_target_accepts_ipv4_with_port() -> None:
    assert validate_tcp_target("192.168.0.8:5555") == "192.168.0.8:5555"


def test_validate_tcp_target_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        validate_tcp_target("missing-port")


def test_build_logcat_command_uses_threadtime_and_filter_spec(monkeypatch, tmp_path: Path) -> None:
    adb_path = tmp_path / "adb.exe"
    adb_path.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("LOGCAT_TOOL_ADB", str(adb_path))

    command = build_logcat_command(
        "R58M12345",
        FilterState(minimum_level="I", tag_filters=("MyApp",), keyword="", match_only=False),
    )

    assert command[:5] == [str(adb_path), "-s", "R58M12345", "logcat", "-v"]
    assert "threadtime" in command
    assert command[-2:] == ["MyApp:I", "*:S"]


def test_resolve_adb_path_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    adb_path = tmp_path / "adb.exe"
    adb_path.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("LOGCAT_TOOL_ADB", str(adb_path))
    assert resolve_adb_path() == adb_path
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_adb.py -v`
Expected: import failure for `logcat_tool_for_win.adb`

- [ ] **Step 3: Implement the `adb` wrapper**

`src/logcat_tool_for_win/adb.py`
```python
from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from logcat_tool_for_win.devices import parse_devices_output
from logcat_tool_for_win.filters import build_logcat_filter_spec
from logcat_tool_for_win.models import DeviceInfo, FilterState


class ADBCommandError(RuntimeError):
    pass


def resolve_adb_path() -> Path:
    override = os.environ.get("LOGCAT_TOOL_ADB")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "platform-tools" / "adb.exe"
    return Path(__file__).resolve().parent / "resources" / "platform-tools" / "adb.exe"


def validate_tcp_target(target: str) -> str:
    if ":" not in target:
        raise ValueError("Expected target in IP:port format.")
    host, port_text = target.split(":", 1)
    ipaddress.ip_address(host)
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return f"{host}:{port}"


def run_adb(args: Sequence[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    command = [str(resolve_adb_path()), *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ADBCommandError(result.stderr.strip() or result.stdout.strip() or "adb command failed")
    return result


def list_devices() -> list[DeviceInfo]:
    result = run_adb(["devices", "-l"])
    return parse_devices_output(result.stdout)


def connect_device(target: str) -> str:
    clean = validate_tcp_target(target)
    result = run_adb(["connect", clean])
    return result.stdout.strip()


def restart_server() -> None:
    run_adb(["kill-server"])
    run_adb(["start-server"])


def clear_logcat(serial: str) -> None:
    run_adb(["-s", serial, "logcat", "-c"])


def build_logcat_command(serial: str, filter_state: FilterState) -> list[str]:
    return [
        str(resolve_adb_path()),
        "-s",
        serial,
        "logcat",
        "-v",
        "threadtime",
        *build_logcat_filter_spec(filter_state.minimum_level, filter_state.tag_filters),
    ]
```

`src/logcat_tool_for_win/resources/platform-tools/.gitkeep`
```text

```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk pytest tests/test_adb.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit the `adb` wrapper**

```bash
rtk git add src/logcat_tool_for_win/adb.py src/logcat_tool_for_win/resources/platform-tools/.gitkeep tests/test_adb.py
rtk git commit -m "feat: add adb command wrapper"
```

### Task 7: Stream `logcat` Output Through A Queue

**Files:**
- Create: `src/logcat_tool_for_win/log_stream.py`
- Create: `tests/test_log_stream.py`

- [ ] **Step 1: Write the failing stream tests**

```python
import io
import queue

from logcat_tool_for_win.log_stream import LogcatSession, parse_threadtime_line


class FakePopen:
    def __init__(self) -> None:
        self.stdout = io.StringIO("06-18 12:00:00.000  1234  1235 I MyApp: boot complete\n")
        self.stderr = io.StringIO("device offline")
        self.returncode = 0

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def test_parse_threadtime_line_extracts_fields() -> None:
    entry = parse_threadtime_line("06-18 12:00:00.000  1234  1235 E MyApp: crash")
    assert entry.level == "E"
    assert entry.tag == "MyApp"
    assert entry.message == "crash"


def test_session_emits_started_line_and_stderr_events() -> None:
    events: queue.Queue = queue.Queue()
    session = LogcatSession(["adb", "logcat"], events, lambda *args, **kwargs: FakePopen())
    session.start()
    session.join()
    kinds = []
    while not events.empty():
        kinds.append(events.get().kind)
    assert kinds[:3] == ["started", "line", "stderr"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_log_stream.py -v`
Expected: import failure for `logcat_tool_for_win.log_stream`

- [ ] **Step 3: Implement stream parsing and session management**

`src/logcat_tool_for_win/log_stream.py`
```python
from __future__ import annotations

import queue
import re
import subprocess
import threading
from typing import Callable

from logcat_tool_for_win.models import LogEntry, StreamEvent

THREADTIME_RE = re.compile(
    r"^(?P<stamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+\d+\s+\d+\s+(?P<level>[VDIWEF])\s+(?P<tag>[^:]+):\s(?P<message>.*)$"
)


def parse_threadtime_line(line: str) -> LogEntry:
    match = THREADTIME_RE.match(line.strip())
    if not match:
        return LogEntry(timestamp_text="", level="I", tag="raw", message=line.rstrip(), raw_line=line.rstrip())
    return LogEntry(
        timestamp_text=match.group("stamp"),
        level=match.group("level"),
        tag=match.group("tag").strip(),
        message=match.group("message"),
        raw_line=line.rstrip(),
    )


class LogcatSession:
    def __init__(
        self,
        command: list[str],
        events: queue.Queue[StreamEvent],
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.command = command
        self.events = events
        self.popen_factory = popen_factory
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.events.put(StreamEvent(kind="started"))
        self.process = self.popen_factory(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.events.put(StreamEvent(kind="line", entry=parse_threadtime_line(line)))
        if self.process.stderr is not None:
            stderr_text = self.process.stderr.read().strip()
            if stderr_text:
                self.events.put(StreamEvent(kind="stderr", message=stderr_text))
        self.events.put(StreamEvent(kind="stopped"))

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=5)

    def join(self) -> None:
        if self.thread is not None:
            self.thread.join(timeout=2)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk pytest tests/test_log_stream.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit stream management**

```bash
rtk git add src/logcat_tool_for_win/log_stream.py tests/test_log_stream.py
rtk git commit -m "feat: add logcat stream session"
```

### Task 8: Build The Tkinter GUI Shell

**Files:**
- Create: `src/logcat_tool_for_win/gui.py`
- Modify: `src/logcat_tool_for_win/__main__.py`
- Create: `tests/test_gui_helpers.py`

- [ ] **Step 1: Write the failing GUI helper tests**

```python
from logcat_tool_for_win.gui import build_highlight_rules, build_summary_text, format_status_text
from logcat_tool_for_win.models import AppStatus


def test_build_summary_text_reports_total_and_visible_counts() -> None:
    assert build_summary_text(120, 24, "streaming") == "Lines: 120 | Visible: 24 | State: streaming"


def test_format_status_text_includes_reconnect_attempt() -> None:
    status = AppStatus(
        adb_ready=True,
        active_device_serial="R58M12345",
        stream_state="reconnecting",
        queue_depth=9,
        last_error="device offline",
        reconnect_attempt=2,
    )
    text = format_status_text(status)
    assert "R58M12345" in text
    assert "attempt 2" in text


def test_build_highlight_rules_creates_rules_from_csv_text() -> None:
    rules = build_highlight_rules("ANR, crash , ")
    assert [rule.name for rule in rules] == ["ANR", "crash"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk pytest tests/test_gui_helpers.py -v`
Expected: import failure for `logcat_tool_for_win.gui`

- [ ] **Step 3: Implement the GUI shell and wire it to the backend modules**

`src/logcat_tool_for_win/gui.py`
```python
from __future__ import annotations

from collections import deque
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from logcat_tool_for_win.adb import build_logcat_command, clear_logcat, connect_device, list_devices, restart_server
from logcat_tool_for_win.config import (
    QUEUE_DRAIN_MS,
    RAW_LOG_CAP,
    VISIBLE_LOG_CAP,
    get_presets_file,
    get_state_file,
)
from logcat_tool_for_win.devices import device_label
from logcat_tool_for_win.export import export_lines
from logcat_tool_for_win.filters import entry_matches, normalize_tag_filters
from logcat_tool_for_win.highlight import DEFAULT_LEVEL_COLORS, match_highlight_rules
from logcat_tool_for_win.log_stream import LogcatSession
from logcat_tool_for_win.models import AppStatus, DeviceInfo, FilterState, HighlightRule, LogEntry
from logcat_tool_for_win.presets import load_presets, load_state, save_preset, save_state

MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_MS = 2_000


def build_summary_text(total_lines: int, visible_lines: int, stream_state: str) -> str:
    return f"Lines: {total_lines} | Visible: {visible_lines} | State: {stream_state}"


def build_highlight_rules(raw: str) -> list[HighlightRule]:
    rules: list[HighlightRule] = []
    for item in raw.split(","):
        pattern = item.strip()
        if pattern:
            rules.append(HighlightRule(name=pattern, pattern=pattern, foreground="#fb923c"))
    return rules


def format_status_text(status: AppStatus) -> str:
    base = (
        f"ADB: {'ready' if status.adb_ready else 'missing'} | "
        f"Device: {status.active_device_serial or '-'} | "
        f"State: {status.stream_state} | "
        f"Queue: {status.queue_depth}"
    )
    if status.reconnect_attempt:
        base += f" | Reconnect attempt {status.reconnect_attempt}"
    if status.last_error:
        base += f" | {status.last_error}"
    return base


class LogcatToolGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Logcat Tool for Win")
        self.root.geometry("1280x780")
        self.root.configure(bg="#0f172a")

        self.devices: list[DeviceInfo] = []
        self.session: LogcatSession | None = None
        self.events: queue.Queue = queue.Queue()
        self.raw_lines: deque[LogEntry] = deque(maxlen=RAW_LOG_CAP)
        self.visible_lines: deque[LogEntry] = deque(maxlen=VISIBLE_LOG_CAP)
        self.filters, self.highlight_rules, recent_target = load_state(get_state_file())
        self.named_presets = load_presets(get_presets_file())
        self.status = AppStatus()
        self.manual_stop = False

        self.device_var = tk.StringVar()
        self.connect_var = tk.StringVar(value=recent_target)
        self.level_var = tk.StringVar(value=self.filters.minimum_level)
        self.tag_var = tk.StringVar(value=", ".join(self.filters.tag_filters))
        self.keyword_var = tk.StringVar(value=self.filters.keyword)
        self.highlight_var = tk.StringVar(value=", ".join(rule.pattern for rule in self.highlight_rules))
        self.preset_var = tk.StringVar(value=(next(iter(self.named_presets)) if self.named_presets else ""))
        self.summary_var = tk.StringVar(value=build_summary_text(0, 0, "idle"))
        self.status_var = tk.StringVar(value=format_status_text(self.status))
        self.auto_scroll_var = tk.BooleanVar(value=self.filters.auto_scroll)
        self.match_only_var = tk.BooleanVar(value=self.filters.match_only)

        self._build_ui()
        self._bind_shortcuts()
        self.refresh_devices()
        self.root.after(QUEUE_DRAIN_MS, self._poll_stream)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=tk.X)

        self.device_combo = ttk.Combobox(toolbar, textvariable=self.device_var, state="readonly", width=32)
        self.device_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Refresh", command=self.refresh_devices).pack(side=tk.LEFT, padx=4)
        ttk.Entry(toolbar, textvariable=self.connect_var, width=18).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Connect", command=self.connect_tcp).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Start", command=self.start_stream).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Stop", command=self.stop_stream).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Clear View", command=self.clear_view).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Clear Device Logcat", command=self.clear_device_logcat).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Export Visible", command=self.export_visible).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Export Raw", command=self.export_raw).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Restart ADB", command=self.restart_adb).pack(side=tk.LEFT, padx=4)

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(body, padding=10)
        body.add(controls, weight=1)

        ttk.Label(controls, text="Level").pack(anchor=tk.W)
        ttk.Combobox(controls, textvariable=self.level_var, state="readonly", values=("V", "D", "I", "W", "E", "F")).pack(fill=tk.X, pady=4)
        ttk.Label(controls, text="Tags (comma separated)").pack(anchor=tk.W)
        ttk.Entry(controls, textvariable=self.tag_var).pack(fill=tk.X, pady=4)
        ttk.Label(controls, text="Keyword").pack(anchor=tk.W)
        self.keyword_entry = ttk.Entry(controls, textvariable=self.keyword_var)
        self.keyword_entry.pack(fill=tk.X, pady=4)
        ttk.Label(controls, text="Highlight Keywords").pack(anchor=tk.W)
        ttk.Entry(controls, textvariable=self.highlight_var).pack(fill=tk.X, pady=4)
        ttk.Checkbutton(controls, text="Auto Scroll", variable=self.auto_scroll_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(controls, text="Match Only", variable=self.match_only_var).pack(anchor=tk.W, pady=2)
        ttk.Label(controls, text="Preset Name").pack(anchor=tk.W, pady=(8, 0))
        ttk.Entry(controls, textvariable=self.preset_var).pack(fill=tk.X, pady=4)
        ttk.Button(controls, text="Save Preset", command=self.save_named_preset).pack(fill=tk.X, pady=2)
        ttk.Button(controls, text="Load Preset", command=self.load_named_preset).pack(fill=tk.X, pady=2)
        ttk.Button(controls, text="Save Session State", command=self.save_session_state).pack(fill=tk.X, pady=(8, 0))

        viewer = ttk.Frame(body, padding=10)
        body.add(viewer, weight=4)

        ttk.Label(viewer, textvariable=self.summary_var).pack(anchor=tk.W)
        self.text = tk.Text(viewer, wrap="none", bg="#020617", fg="#f8fafc", insertbackground="#f8fafc")
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.configure(state=tk.DISABLED)
        for level, color in DEFAULT_LEVEL_COLORS.items():
            self.text.tag_config(level, foreground=color)

        ttk.Label(self.root, textvariable=self.status_var, padding=8).pack(fill=tk.X)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-f>", lambda _event: self.focus_keyword())
        self.root.bind("<Control-l>", lambda _event: self.clear_view())
        self.root.bind("<F5>", lambda _event: self.refresh_devices())
        self.root.bind("<Control-e>", lambda _event: self.export_visible())
        self.root.bind("<Control-Shift-E>", lambda _event: self.export_raw())

    def focus_keyword(self) -> None:
        self.keyword_entry.focus_set()

    def refresh_devices(self) -> None:
        try:
            self.devices = list_devices()
            labels = [device_label(device) for device in self.devices]
            self.device_combo["values"] = labels
            if labels and self.device_var.get() not in labels:
                self.device_var.set(labels[0])
            self.status.adb_ready = True
            self.status.last_error = ""
        except Exception as exc:
            self.status.adb_ready = False
            self.status.last_error = str(exc)
        self._update_status()

    def connect_tcp(self) -> None:
        try:
            target = self.connect_var.get().strip()
            message = connect_device(target)
            self.status.last_error = message
            self.refresh_devices()
        except Exception as exc:
            messagebox.showerror("Connect Failed", str(exc))
            self.status.last_error = str(exc)
            self._update_status()

    def _current_device(self) -> DeviceInfo:
        for device in self.devices:
            if device_label(device) == self.device_var.get():
                return device
        raise ValueError("No device selected.")

    def _current_filters(self) -> FilterState:
        return FilterState(
            minimum_level=self.level_var.get(),
            tag_filters=normalize_tag_filters(self.tag_var.get()),
            keyword=self.keyword_var.get().strip(),
            match_only=self.match_only_var.get(),
            auto_scroll=self.auto_scroll_var.get(),
        )

    def _current_highlight_rules(self) -> list[HighlightRule]:
        return build_highlight_rules(self.highlight_var.get())

    def start_stream(self) -> None:
        device = self._current_device()
        if device.state != "device":
            messagebox.showwarning("Device Not Ready", f"Selected device is {device.state}.")
            return
        self.manual_stop = False
        self.filters = self._current_filters()
        self.highlight_rules = self._current_highlight_rules()
        retrying = self.status.stream_state == "reconnecting"
        self.status.active_device_serial = device.serial
        self.status.stream_state = "streaming"
        if not retrying:
            self.status.reconnect_attempt = 0
        if self.session is not None:
            self.session.stop()
        self.session = LogcatSession(build_logcat_command(device.serial, self.filters), self.events)
        self.session.start()
        self._update_status()

    def stop_stream(self) -> None:
        self.manual_stop = True
        if self.session is not None:
            self.session.stop()
        self.status.stream_state = "idle"
        self.status.reconnect_attempt = 0
        self._update_status()

    def clear_view(self) -> None:
        self.raw_lines.clear()
        self.visible_lines.clear()
        self._render_visible()

    def clear_device_logcat(self) -> None:
        device = self._current_device()
        clear_logcat(device.serial)

    def restart_adb(self) -> None:
        restart_server()
        self.refresh_devices()

    def save_named_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showwarning("Preset Name Required", "Enter a preset name before saving.")
            return
        filters = self._current_filters()
        save_preset(get_presets_file(), name, filters)
        self.named_presets[name] = filters

    def load_named_preset(self) -> None:
        name = self.preset_var.get().strip()
        preset = self.named_presets.get(name)
        if preset is None:
            messagebox.showwarning("Preset Missing", f"No preset named '{name}' was found.")
            return
        self.level_var.set(preset.minimum_level)
        self.tag_var.set(", ".join(preset.tag_filters))
        self.keyword_var.set(preset.keyword)
        self.auto_scroll_var.set(preset.auto_scroll)
        self.match_only_var.set(preset.match_only)

    def save_session_state(self) -> None:
        save_state(
            get_state_file(),
            self._current_filters(),
            self._current_highlight_rules(),
            self.connect_var.get().strip(),
        )

    def export_visible(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".txt")
        if not path:
            return
        export_lines(Path(path), [entry.raw_line for entry in self.visible_lines])

    def export_raw(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".txt")
        if not path:
            return
        export_lines(Path(path), [entry.raw_line for entry in self.raw_lines])

    def _schedule_reconnect(self) -> None:
        if self.manual_stop:
            return
        if self.status.reconnect_attempt >= MAX_RECONNECT_ATTEMPTS:
            self.status.stream_state = "failed"
            self.status.last_error = "Reconnect attempts exhausted."
            self._update_status()
            return
        self.status.reconnect_attempt += 1
        self.status.stream_state = "reconnecting"
        self.status.last_error = "Stream stopped unexpectedly."
        self._update_status()
        self.root.after(RECONNECT_DELAY_MS, self._retry_stream)

    def _retry_stream(self) -> None:
        if self.status.active_device_serial and not self.manual_stop:
            for device in self.devices:
                if device.serial == self.status.active_device_serial and device.state == "device":
                    self.device_var.set(device_label(device))
                    self.start_stream()
                    return
            self.status.last_error = "Device not available for reconnect."
            self.status.stream_state = "failed"
            self._update_status()

    def _poll_stream(self) -> None:
        updated = False
        while not self.events.empty():
            event = self.events.get()
            if event.kind == "line" and event.entry is not None:
                updated = True
                if self.status.reconnect_attempt:
                    self.status.reconnect_attempt = 0
                    self.status.last_error = ""
                self.raw_lines.append(event.entry)
                entry = event.entry
                if entry_matches(entry, self._current_filters()):
                    entry.highlight_keys = match_highlight_rules(entry, self.highlight_rules)
                    self.visible_lines.append(entry)
            elif event.kind == "stderr":
                self.status.last_error = event.message
            elif event.kind == "stopped":
                if self.status.stream_state == "streaming":
                    self._schedule_reconnect()
        if updated:
            self._render_visible()
        self.status.queue_depth = self.events.qsize()
        self._update_status()
        self.root.after(QUEUE_DRAIN_MS, self._poll_stream)

    def _render_visible(self) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        rule_map = {rule.name: rule for rule in self.highlight_rules}
        for entry in self.visible_lines:
            start = self.text.index(tk.END)
            self.text.insert(tk.END, entry.raw_line + "\n", entry.level)
            end = self.text.index(tk.END)
            for rule_name in entry.highlight_keys:
                rule = rule_map[rule_name]
                self.text.tag_config(rule_name, foreground=rule.foreground)
                self.text.tag_add(rule_name, start, end)
        self.text.configure(state=tk.DISABLED)
        self.summary_var.set(build_summary_text(len(self.raw_lines), len(self.visible_lines), self.status.stream_state))
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def _update_status(self) -> None:
        self.status_var.set(format_status_text(self.status))


def main() -> int:
    root = tk.Tk()
    LogcatToolGUI(root)
    root.mainloop()
    return 0
```

`src/logcat_tool_for_win/__main__.py`
```python
from __future__ import annotations

from logcat_tool_for_win.gui import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the GUI helper tests to verify they pass**

Run: `rtk pytest tests/test_gui_helpers.py -v`
Expected: `3 passed`

- [ ] **Step 5: Manually smoke-test the window**

Run: `rtk python -m logcat_tool_for_win`
Expected: the desktop window opens, the left panel includes preset and highlight controls, both export buttons are visible, and the app closes cleanly when dismissed.

- [ ] **Step 6: Commit the GUI shell**

```bash
rtk git add src/logcat_tool_for_win/gui.py src/logcat_tool_for_win/__main__.py tests/test_gui_helpers.py
rtk git commit -m "feat: add tkinter logcat gui"
```

### Task 9: Build The Portable Release As A ZIP

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/build_portable.py`
- Create: `logcat-tool-for-win.spec`
- Create: `tests/test_build_portable.py`

- [ ] **Step 1: Write the failing portable-build test**

```python
import zipfile
from pathlib import Path

from scripts.build_portable import build_portable


def test_build_portable_creates_zip_with_exe_and_platform_tools(tmp_path: Path) -> None:
    app_dir = tmp_path / "dist" / "logcat-tool-for-win"
    app_dir.mkdir(parents=True)
    (app_dir / "logcat-tool-for-win.exe").write_text("exe", encoding="utf-8")

    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()
    (platform_tools / "adb.exe").write_text("adb", encoding="utf-8")

    readme = tmp_path / "README.md"
    readme.write_text("# Portable", encoding="utf-8")

    zip_path = build_portable(tmp_path / "dist", platform_tools, readme, tmp_path / "artifacts")

    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "logcat-tool-for-win/logcat-tool-for-win.exe" in names
    assert "logcat-tool-for-win/platform-tools/adb.exe" in names
    assert "logcat-tool-for-win/README.md" in names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rtk pytest tests/test_build_portable.py -v`
Expected: import failure for `scripts.build_portable`

- [ ] **Step 3: Implement the portable assembly script and PyInstaller spec**

`scripts/__init__.py`
```python
# Package marker for build helper imports in tests.
```

`scripts/build_portable.py`
```python
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


def build_portable(
    dist_root: Path,
    platform_tools_dir: Path,
    readme_path: Path,
    output_root: Path,
) -> Path:
    app_dir = dist_root / "logcat-tool-for-win"
    if not app_dir.exists():
        raise FileNotFoundError(f"Missing built app directory: {app_dir}")
    if not platform_tools_dir.exists():
        raise FileNotFoundError(f"Missing platform-tools directory: {platform_tools_dir}")

    release_dir = output_root / "logcat-tool-for-win"
    if release_dir.exists():
        shutil.rmtree(release_dir)
    shutil.copytree(app_dir, release_dir)
    shutil.copytree(platform_tools_dir, release_dir / "platform-tools")
    shutil.copy2(readme_path, release_dir / "README.md")

    zip_path = output_root / "logcat-tool-for-win.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in release_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(output_root))
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-root", type=Path, default=Path("dist"))
    parser.add_argument(
        "--platform-tools-dir",
        type=Path,
        default=Path("src/logcat_tool_for_win/resources/platform-tools"),
    )
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts"))
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    build_portable(args.dist_root, args.platform_tools_dir, args.readme, args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`logcat-tool-for-win.spec`
```python
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["src/logcat_tool_for_win/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="logcat-tool-for-win",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="logcat-tool-for-win",
)
```

- [ ] **Step 4: Run the packaging test to verify it passes**

Run: `rtk pytest tests/test_build_portable.py -v`
Expected: `1 passed`

- [ ] **Step 5: Commit portable packaging support**

```bash
rtk git add scripts/__init__.py scripts/build_portable.py logcat-tool-for-win.spec tests/test_build_portable.py
rtk git commit -m "feat: add portable package builder"
```

### Task 10: Add CI, Windows Packaging, And Final Docs

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`

- [ ] **Step 1: Update the README with real usage, build, and troubleshooting instructions**

`README.md`
````markdown
# Logcat Tool for Win

Windows GUI Android `logcat` viewer with bundled `adb`, multi-device switching, filtering, export, and GitHub Actions packaging.

## Features

- Portable ZIP release for Windows
- Bundled `adb`
- USB and TCP device support
- Single active realtime stream with fast device switching
- Level, tag, and keyword filtering
- Session-state persistence and text export

## Development

```bash
pip install -e .[dev]
pytest
ruff check .
```

To run from source, download Android platform-tools into `src/logcat_tool_for_win/resources/platform-tools/` or set `LOGCAT_TOOL_ADB` to an existing `adb.exe` path.

## GitHub Actions Release

The `build-windows` job downloads official Android platform-tools, builds the app with PyInstaller, assembles the portable directory, and uploads `logcat-tool-for-win.zip` as an artifact.
````

- [ ] **Step 2: Add the GitHub Actions workflow**

`.github/workflows/ci.yml`
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .[dev]

      - name: Ruff
        run: ruff check .

      - name: Pytest
        run: pytest

      - name: Build sdist and wheel
        run: python -m build

  build-windows:
    runs-on: windows-latest
    needs: test
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .[dev]

      - name: Download Android platform-tools
        shell: pwsh
        run: |
          New-Item -ItemType Directory -Force -Path src\logcat_tool_for_win\resources | Out-Null
          $zipPath = "$env:RUNNER_TEMP\platform-tools.zip"
          Invoke-WebRequest -Uri "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zipPath
          Expand-Archive -Path $zipPath -DestinationPath src\logcat_tool_for_win\resources -Force

      - name: Build app
        run: pyinstaller --noconfirm --clean logcat-tool-for-win.spec

      - name: Assemble portable zip
        run: python scripts/build_portable.py --dist-root dist --output-root artifacts

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: logcat-tool-for-win
          path: artifacts/logcat-tool-for-win.zip
```

- [ ] **Step 3: Run the full local verification suite**

Run: `rtk pytest -q`
Expected: all tests pass

Run: `rtk ruff check .`
Expected: no lint errors

Run: `rtk python -m build`
Expected: `dist/` contains an sdist and wheel

- [ ] **Step 4: Commit CI and docs**

```bash
rtk git add README.md .github/workflows/ci.yml
rtk git commit -m "ci: add windows packaging workflow"
```

### Task 11: Publish The Repository And Verify The Workflow

**Files:**
- Modify: none

- [ ] **Step 1: Create the GitHub repository**

Run: `rtk gh repo create LogcatToolForWin --private --source . --remote origin`
Expected: GitHub creates the repository and configures `origin`

- [ ] **Step 2: Push the branch**

Run: `rtk git push -u origin main`
Expected: `main` is tracking `origin/main`

- [ ] **Step 3: Watch the first CI run**

Run: `rtk gh run watch --exit-status`
Expected: both `test` and `build-windows` jobs complete successfully

- [ ] **Step 4: Record the artifact and repository URL in the README or release notes if needed**

Run: `rtk gh repo view --json url`
Expected: the repository URL prints in JSON so it can be shared back to the user

- [ ] **Step 5: Commit any last-mile doc tweaks only if the push revealed missing instructions**

```bash
rtk git add README.md
rtk git commit -m "docs: clarify release usage"
```
