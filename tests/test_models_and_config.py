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
