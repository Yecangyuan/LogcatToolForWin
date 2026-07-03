import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from logcat_tool_for_win.adb import (
    ADBCommandError,
    build_logcat_command,
    connect_device,
    resolve_adb_path,
    run_adb,
    validate_tcp_target,
)
from logcat_tool_for_win.filters import build_logcat_filter_spec
from logcat_tool_for_win.models import FilterState


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


def test_resolve_adb_path_prefers_embedded_platform_tools_when_frozen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")

    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )
    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)

    assert resolve_adb_path() == embedded_adb


def test_build_logcat_command_uses_resolved_adb_path_and_filter_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb_path = Path("/opt/android/platform-tools/adb.exe")
    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: adb_path)

    state = FilterState(minimum_level="I", tag_filters=("MyApp",))
    command = build_logcat_command("SERIAL", state)

    assert command[:4] == [str(adb_path), "-s", "SERIAL", "logcat"]
    assert command[4] == "-v"
    assert command[5] == "threadtime"
    assert command[6:] == build_logcat_filter_spec("I", ("MyApp",))


def test_connect_device_returns_stdout_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    assert connect_device("192.168.0.8:5555") == "connected to 192.168.0.8:5555\n"


def test_run_adb_raises_adb_command_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "version"],
        returncode=1,
        stdout="",
        stderr="failed",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", lambda *args, **kwargs: completed)

    with pytest.raises(ADBCommandError, match="failed"):
        run_adb(["version"])


def test_run_adb_suppresses_windows_error_dialogs_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "version"],
        returncode=0,
        stdout="Android Debug Bridge version\n",
        stderr="",
    )
    calls: list[str] = []

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", lambda *args, **kwargs: completed)
    monkeypatch.setattr(
        "logcat_tool_for_win.adb._suppress_windows_error_dialogs",
        lambda: calls.append("suppressed"),
    )

    run_adb(["version"])

    assert calls == ["suppressed"]


def test_run_adb_does_not_inherit_invalid_gui_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    captured_kwargs = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    run_adb(["connect", "192.168.0.8:5555"])

    assert captured_kwargs["stdin"] == subprocess.DEVNULL
