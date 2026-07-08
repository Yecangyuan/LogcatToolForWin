from pathlib import Path

from logcat_tool_for_win.config import (
    APP_DIRNAME,
    QUEUE_DRAIN_MS,
    RAW_LOG_CAP,
    VISIBLE_LOG_CAP,
    get_config_dir,
    get_presets_file,
    get_state_file,
)
from logcat_tool_for_win.models import (
    AppStatus,
    DeviceInfo,
    FilterState,
    HighlightRule,
    LogEntry,
    StreamEvent,
)


def test_get_config_dir_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = get_config_dir()
    assert path == tmp_path / APP_DIRNAME
    assert path.exists()


def test_state_and_presets_files_use_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert get_state_file() == tmp_path / APP_DIRNAME / "state.json"
    assert get_presets_file() == tmp_path / APP_DIRNAME / "presets.json"


def test_default_caps_match_design() -> None:
    assert RAW_LOG_CAP == 20_000
    assert VISIBLE_LOG_CAP == 5_000
    assert QUEUE_DRAIN_MS == 100


def test_models_have_stable_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

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
    highlight = HighlightRule(name="Errors", pattern="E/", foreground="#ff0000")
    entry = LogEntry(
        timestamp_text="06-18 12:34:56.789",
        level="I",
        tag="ActivityManager",
        message="Started",
        raw_line="06-18 12:34:56.789 I/ActivityManager: Started",
    )
    status = AppStatus()
    event = StreamEvent(kind="status")

    assert device.transport == "usb"
    assert filters.minimum_level == "V"
    assert filters.tag_filters == ()
    assert filters.auto_scroll is True
    assert highlight.background == ""
    assert highlight.case_sensitive is False
    assert entry.matches_filters is True
    assert entry.highlight_keys == ()
    assert entry.lowered_raw_line is None
    assert status.stream_state == "idle"
    assert event.entry is None
    assert event.message == ""
    assert get_state_file() == tmp_path / APP_DIRNAME / "state.json"
