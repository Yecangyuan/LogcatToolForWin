from pathlib import Path

import pytest

from logcat_tool_for_win.adb import build_logcat_command, resolve_adb_path, validate_tcp_target
from logcat_tool_for_win.filters import build_logcat_filter_spec


def test_validate_tcp_target_accepts_ipv4_target() -> None:
    assert validate_tcp_target("192.168.0.8:5555") == "192.168.0.8:5555"


@pytest.mark.parametrize(
    "value",
    [
        "192.168.0.8",
        "999.168.0.8:5555",
        "192.168.0.8:notaport",
        "device-name",
    ],
)
def test_validate_tcp_target_rejects_invalid_target(value: str) -> None:
    with pytest.raises(ValueError):
        validate_tcp_target(value)


def test_resolve_adb_path_prefers_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adb_path = tmp_path / "custom-adb.exe"
    monkeypatch.setenv("LOGCAT_TOOL_ADB", str(adb_path))
    assert resolve_adb_path() == adb_path


def test_build_logcat_command_uses_resolved_adb_path_and_filter_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb_path = Path("/opt/android/platform-tools/adb.exe")
    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: adb_path)

    command = build_logcat_command("SERIAL", "I", ("MyApp",))

    assert command[:4] == [str(adb_path), "-s", "SERIAL", "logcat"]
    assert command[4] == "-v"
    assert command[5] == "threadtime"
    assert command[6:] == build_logcat_filter_spec("I", ("MyApp",))
