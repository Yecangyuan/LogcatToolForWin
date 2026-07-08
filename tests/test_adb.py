import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import logcat_tool_for_win.adb as adb_module
from logcat_tool_for_win.adb import (
    DEFAULT_TCP_PORT,
    ADBCommandError,
    build_logcat_command,
    connect_device,
    enable_tcpip,
    extract_tcp_port,
    get_manual_adb_path,
    get_device_route_ip,
    normalize_tcp_target,
    parse_route_source_ip,
    resolve_adb_path,
    run_adb,
    set_manual_adb_path,
    validate_tcp_target,
)
from logcat_tool_for_win.filters import build_logcat_filter_spec
from logcat_tool_for_win.models import FilterState


def test_validate_tcp_target_accepts_ipv4_target() -> None:
    assert validate_tcp_target("192.168.0.8:5555") == "192.168.0.8:5555"


def test_validate_tcp_target_trims_host_and_port_around_separator() -> None:
    assert validate_tcp_target(" 192.168.0.8 : 5555 ") == "192.168.0.8:5555"


def test_normalize_tcp_target_adds_default_port_for_ipv4_target() -> None:
    assert normalize_tcp_target("192.168.0.8") == "192.168.0.8:5555"


def test_normalize_tcp_target_trims_host_and_port_around_separator() -> None:
    assert normalize_tcp_target(" 192.168.0.8 : 5555 ") == "192.168.0.8:5555"


def test_extract_tcp_port_uses_default_for_blank_target() -> None:
    assert extract_tcp_port("") == DEFAULT_TCP_PORT


def test_extract_tcp_port_uses_default_for_target_without_port() -> None:
    assert extract_tcp_port("192.168.0.8") == DEFAULT_TCP_PORT


def test_extract_tcp_port_reads_port_from_target() -> None:
    assert extract_tcp_port("192.168.0.8:4567") == 4567


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


def test_resolve_adb_path_falls_back_to_path_adb_when_source_resource_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path_adb = tmp_path / "adb.exe"
    path_adb.write_text("adb", encoding="utf-8")
    source_file = tmp_path / "src" / "logcat_tool_for_win" / "adb.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("", encoding="utf-8")

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", SimpleNamespace(executable="python"))
    monkeypatch.setattr("logcat_tool_for_win.adb.__file__", str(source_file))
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)

    assert resolve_adb_path() == path_adb


def test_resolve_adb_path_prefers_manual_override_before_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manual_adb = tmp_path / "manual" / "adb.exe"
    manual_adb.parent.mkdir(parents=True)
    manual_adb.write_text("adb", encoding="utf-8")
    env_adb = tmp_path / "env" / "adb.exe"
    env_adb.parent.mkdir(parents=True)
    env_adb.write_text("adb", encoding="utf-8")

    monkeypatch.setattr(adb_module, "_manual_adb_path", None, raising=False)
    monkeypatch.setenv("LOGCAT_TOOL_ADB", str(env_adb))

    set_manual_adb_path(manual_adb)

    assert get_manual_adb_path() == manual_adb
    assert resolve_adb_path() == manual_adb


def test_get_manual_adb_path_clears_missing_path_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_adb = tmp_path / "missing" / "adb.exe"

    monkeypatch.setattr(adb_module, "_manual_adb_path", None, raising=False)

    set_manual_adb_path(missing_adb)

    assert get_manual_adb_path() is None
    assert adb_module._manual_adb_path is None


def test_run_adb_falls_back_to_path_adb_when_frozen_embedded_adb_keeps_failing_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_windows_startupinfo,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")
    path_adb = tmp_path / "platform-tools" / "adb.exe"
    path_adb.parent.mkdir(parents=True)
    path_adb.write_text("adb", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == str(embedded_adb):
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return completed

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb._runtime_adb_path", None, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert commands[0][0] == str(embedded_adb)
    assert commands[-1][0] == str(path_adb)
    assert str(path_adb) in [command[0] for command in commands]


def test_build_logcat_command_uses_runtime_fallback_adb_after_invalid_handle_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_windows_startupinfo,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")
    path_adb = tmp_path / "platform-tools" / "adb.exe"
    path_adb.parent.mkdir(parents=True)
    path_adb.write_text("adb", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=["adb", "devices", "-l"],
        returncode=0,
        stdout="List of devices attached\n\n",
        stderr="",
    )
    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )

    def fake_run(command, **kwargs):
        if command[0] == str(embedded_adb):
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return completed

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb._runtime_adb_path", None, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    run_adb(["devices", "-l"])
    command = build_logcat_command("SERIAL", FilterState())

    assert command[0] == str(path_adb)


def test_run_adb_reports_actionable_hint_when_all_candidate_adb_paths_fail_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_windows_startupinfo,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")
    path_adb = tmp_path / "platform-tools" / "adb.exe"
    path_adb.parent.mkdir(parents=True)
    path_adb.write_text("adb", encoding="utf-8")
    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )

    def raise_invalid_handle(command, **kwargs):
        exc = OSError("[WinError 6] 句柄无效。")
        exc.winerror = 6
        raise exc

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb._runtime_adb_path", None, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", raise_invalid_handle)

    with pytest.raises(ADBCommandError) as exc_info:
        run_adb(["version"])

    message = str(exc_info.value)
    assert "无法启动 adb：[WinError 6] 句柄无效。" in message
    assert str(embedded_adb) in message
    assert str(path_adb) in message
    assert "logcat-tool-for-win-legacy-win7.zip" in message
    assert "LOGCAT_TOOL_ADB" in message


def test_run_adb_falls_back_to_path_adb_when_frozen_embedded_adb_crashes_with_access_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_windows_startupinfo,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")
    path_adb = tmp_path / "platform-tools" / "adb.exe"
    path_adb.parent.mkdir(parents=True)
    path_adb.write_text("adb", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == str(embedded_adb):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0xC0000005,
                stdout="",
                stderr="",
            )
        return completed

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb._runtime_adb_path", None, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert commands[0][0] == str(embedded_adb)
    assert commands[-1][0] == str(path_adb)
    assert str(path_adb) in [command[0] for command in commands]


def test_build_logcat_command_uses_runtime_fallback_adb_after_access_violation_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_windows_startupinfo,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")
    path_adb = tmp_path / "platform-tools" / "adb.exe"
    path_adb.parent.mkdir(parents=True)
    path_adb.write_text("adb", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=["adb", "devices", "-l"],
        returncode=0,
        stdout="List of devices attached\n\n",
        stderr="",
    )
    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )

    def fake_run(command, **kwargs):
        if command[0] == str(embedded_adb):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0xC0000005,
                stdout="",
                stderr="",
            )
        return completed

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb._runtime_adb_path", None, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    run_adb(["devices", "-l"])
    command = build_logcat_command("SERIAL", FilterState())

    assert command[0] == str(path_adb)


def test_run_adb_reports_actionable_hint_when_all_candidate_adb_paths_crash_with_access_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_windows_startupinfo,
) -> None:
    embedded_adb = tmp_path / "_MEI12345" / "platform-tools" / "adb.exe"
    embedded_adb.parent.mkdir(parents=True)
    embedded_adb.write_text("adb", encoding="utf-8")
    path_adb = tmp_path / "platform-tools" / "adb.exe"
    path_adb.parent.mkdir(parents=True)
    path_adb.write_text("adb", encoding="utf-8")
    frozen_sys = SimpleNamespace(
        executable=str(tmp_path / "logcat-tool-for-win.exe"),
        frozen=True,
        _MEIPASS=str(tmp_path / "_MEI12345"),
    )

    def crash_adb(command, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=0xC0000005,
            stdout="",
            stderr="",
        )

    monkeypatch.delenv("LOGCAT_TOOL_ADB", raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb._runtime_adb_path", None, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.sys", frozen_sys)
    monkeypatch.setattr("shutil.which", lambda name: str(path_adb) if name == "adb" else None)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", crash_adb)

    with pytest.raises(ADBCommandError) as exc_info:
        run_adb(["version"])

    message = str(exc_info.value)
    assert "adb.exe 启动后崩溃退出（0xC0000005）" in message
    assert str(embedded_adb) in message
    assert str(path_adb) in message
    assert "logcat-tool-for-win-legacy-win7.zip" in message
    assert "LOGCAT_TOOL_ADB" in message


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


def test_connect_device_accepts_success_after_daemon_startup_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = "\n".join(
        [
            "* daemon not running; starting now at tcp:5037",
            "* daemon started successfully",
            "connected to 192.168.0.8:5555",
        ]
    )
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout=f"{output}\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    assert connect_device("192.168.0.8:5555") == f"{output}\n"


def test_connect_device_accepts_success_written_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="",
        stderr="connected to 192.168.0.8:5555\n",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    assert connect_device("192.168.0.8:5555") == "connected to 192.168.0.8:5555\n"


def test_connect_device_rejects_failed_connect_output_with_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to connect to 192.168.0.8:5555: Connection refused\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError, match="failed to connect"):
        connect_device("192.168.0.8:5555")


def test_connect_device_adds_actionable_hint_to_failed_connect_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to connect to 192.168.0.8:5555: Connection refused\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    message = str(exc_info.value)
    assert "无法连接 192.168.0.8:5555" in message
    assert "请确认手机和电脑在同一局域网" in message
    assert "点“USB 开启无线”" in message
    assert "原始错误：failed to connect to 192.168.0.8:5555: Connection refused" in message


def test_connect_device_preserves_adb_launch_failure_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "logcat_tool_for_win.adb.run_adb",
        lambda args, timeout=10.0: (_ for _ in ()).throw(
            ADBCommandError("无法启动 adb：[WinError 6] 句柄无效。")
        ),
    )

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    assert str(exc_info.value) == "无法启动 adb：[WinError 6] 句柄无效。"


def test_connect_device_preserves_adb_crash_failure_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "logcat_tool_for_win.adb.run_adb",
        lambda args, timeout=10.0: (_ for _ in ()).throw(
            ADBCommandError("adb.exe 启动后崩溃退出（0xC0000005）")
        ),
    )

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    assert str(exc_info.value) == "adb.exe 启动后崩溃退出（0xC0000005）"


def test_connect_device_explains_connection_refused_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to connect to 192.168.0.8:5555: Connection refused\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    message = str(exc_info.value)
    assert "目标端口拒绝连接" in message
    assert "先用 USB 连上后点“USB 开启无线”" in message


@pytest.mark.parametrize(
    ("stdout", "expected_hint"),
    [
        (
            "failed to connect to 192.168.0.8:5555: 由于目标计算机积极拒绝，无法连接。\n",
            "目标端口拒绝连接",
        ),
        (
            "failed to connect to 192.168.0.8:5555: 连接尝试失败，因为连接方在一段时间后没有正确答复或连接的主机没有反应。\n",
            "连接超时",
        ),
        (
            "failed to connect to 192.168.0.8:5555: 网络无法访问。\n",
            "无法到达目标设备",
        ),
    ],
)
def test_connect_device_explains_localized_windows_failures(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    expected_hint: str,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout=stdout,
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    assert expected_hint in str(exc_info.value)


def test_connect_device_explains_timeout_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to connect to 192.168.0.8:5555: Operation timed out\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    message = str(exc_info.value)
    assert "连接超时" in message
    assert "确认手机当前 IP 是否仍然是 192.168.0.8" in message


def test_connect_device_explains_unreachable_network_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to connect to 192.168.0.8:5555: No route to host\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    message = str(exc_info.value)
    assert "无法到达目标设备" in message
    assert "电脑和手机不在同一网段" in message


def test_connect_device_explains_local_adb_daemon_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="cannot connect to daemon at tcp:5037: Connection refused\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    message = str(exc_info.value)
    assert "本机 ADB 服务异常" in message
    assert "可先点界面的“重启 ADB”" in message


def test_connect_device_explains_authentication_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to authenticate to 192.168.0.8:5555\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555")

    message = str(exc_info.value)
    assert "设备鉴权失败" in message
    assert "解锁手机并在屏幕上允许 USB 调试授权" in message


def test_connect_device_rejects_connected_output_for_different_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.9:5555\n",
        stderr="",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", lambda args, timeout=10.0: completed)

    with pytest.raises(ADBCommandError, match="connected to 192.168.0.9:5555"):
        connect_device("192.168.0.8:5555")


def test_connect_device_uses_default_port_for_ipv4_target_without_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    calls: list[list[str]] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)

    assert connect_device("192.168.0.8") == "connected to 192.168.0.8:5555\n"
    assert calls == [["connect", "192.168.0.8:5555"]]


def test_connect_device_normalizes_spaced_tcp_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    calls: list[list[str]] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)

    assert connect_device(" 192.168.0.8 : 5555 ") == "connected to 192.168.0.8:5555\n"
    assert calls == [["connect", "192.168.0.8:5555"]]


def test_connect_device_retries_transient_adb_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        if len(calls) == 1:
            raise ADBCommandError("connection refused")
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)
    monkeypatch.setattr("logcat_tool_for_win.adb.time.sleep", lambda seconds: sleeps.append(seconds))

    assert connect_device("192.168.0.8:5555", attempts=2, delay_seconds=0.5) == (
        "connected to 192.168.0.8:5555\n"
    )
    assert calls == [["connect", "192.168.0.8:5555"], ["connect", "192.168.0.8:5555"]]
    assert sleeps == [0.5]


def test_connect_device_does_not_retry_known_connect_output_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="failed to connect to 192.168.0.8:5555: Connection refused\n",
        stderr="",
    )
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)
    monkeypatch.setattr("logcat_tool_for_win.adb.time.sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555", attempts=3, delay_seconds=0.5)

    assert "目标端口拒绝连接" in str(exc_info.value)
    assert calls == [["connect", "192.168.0.8:5555"]]
    assert sleeps == []


@pytest.mark.parametrize(
    "message",
    [
        "无法启动 adb：[WinError 6] 句柄无效。",
        "adb.exe 启动后崩溃退出（0xC0000005）",
    ],
)
def test_connect_device_does_not_retry_adb_launch_failures(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        raise ADBCommandError(message)

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)
    monkeypatch.setattr("logcat_tool_for_win.adb.time.sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555", attempts=3, delay_seconds=0.5)

    assert str(exc_info.value) == message
    assert calls == [["connect", "192.168.0.8:5555"]]
    assert sleeps == []


@pytest.mark.parametrize(
    ("message", "expected_text"),
    [
        (
            "failed to connect to 192.168.0.8:5555: Connection refused",
            "目标端口拒绝连接",
        ),
        (
            "cannot connect to daemon at tcp:5037: Connection refused",
            "本机 ADB 服务异常",
        ),
        (
            "* daemon not running; starting now at tcp:5037\n"
            "ADB server didn't ACK\n"
            "Full server startup log: C:\\Users\\tester\\AppData\\Local\\Temp\\adb.log\n"
            "cannot bind listener: Permission denied",
            "本机 ADB 服务异常",
        ),
    ],
)
def test_connect_device_does_not_retry_known_direct_connect_failures(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected_text: str,
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        raise ADBCommandError(message)

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)
    monkeypatch.setattr("logcat_tool_for_win.adb.time.sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555", attempts=3, delay_seconds=0.5)

    assert expected_text in str(exc_info.value)
    assert calls == [["connect", "192.168.0.8:5555"]]
    assert sleeps == []


@pytest.mark.parametrize(
    "message",
    [
        "未找到 adb：/missing/adb.exe",
        "无法执行 adb，请检查权限：/adb.exe",
        "未找到可用 adb：C:/Android/platform-tools/adb.exe",
    ],
)
def test_connect_device_does_not_retry_deterministic_adb_path_failures(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        raise ADBCommandError(message)

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)
    monkeypatch.setattr("logcat_tool_for_win.adb.time.sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(ADBCommandError) as exc_info:
        connect_device("192.168.0.8:5555", attempts=3, delay_seconds=0.5)

    assert str(exc_info.value) == message
    assert calls == [["connect", "192.168.0.8:5555"]]
    assert sleeps == []


def test_enable_tcpip_runs_tcpip_command_for_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "-s", "USB123", "tcpip", "5555"],
        returncode=0,
        stdout="restarting in TCP mode port: 5555\n",
        stderr="",
    )
    captured: dict[str, object] = {}

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        captured["args"] = args
        captured["timeout"] = timeout
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)

    assert enable_tcpip("USB123", 5555) == "restarting in TCP mode port: 5555\n"
    assert captured["args"] == ["-s", "USB123", "tcpip", "5555"]


def test_parse_route_source_ip_returns_first_non_loopback_src() -> None:
    output = "\n".join(
        [
            "local 127.0.0.0/8 dev lo table local src 127.0.0.1",
            "192.168.1.0/24 dev wlan0 proto kernel scope link src 192.168.1.111",
        ]
    )

    assert parse_route_source_ip(output) == "192.168.1.111"


def test_parse_route_source_ip_prefers_ipv4_src_for_wireless_adb() -> None:
    output = "\n".join(
        [
            "2001:db8::/64 dev wlan0 proto ra metric 1024 pref medium src 2001:db8::111",
            "192.168.1.0/24 dev wlan0 proto kernel scope link src 192.168.1.111",
        ]
    )

    assert parse_route_source_ip(output) == "192.168.1.111"


def test_get_device_route_ip_parses_adb_shell_ip_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "-s", "USB123", "shell", "ip", "route"],
        returncode=0,
        stdout="default via 192.168.1.1 dev wlan0 proto static src 192.168.1.111\n",
        stderr="",
    )
    captured: dict[str, object] = {}

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        captured["args"] = args
        captured["timeout"] = timeout
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)

    assert get_device_route_ip("USB123") == "192.168.1.111"
    assert captured["args"] == ["-s", "USB123", "shell", "ip", "route"]
    assert captured["timeout"] == 5.0


def test_get_device_route_ip_falls_back_to_ip_addr_show_when_route_has_no_src(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    responses = {
        ("-s", "USB123", "shell", "ip", "route"): subprocess.CompletedProcess(
            args=["adb", "-s", "USB123", "shell", "ip", "route"],
            returncode=0,
            stdout="default via 192.168.1.1 dev wlan0 proto static\n",
            stderr="",
        ),
        ("-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"): subprocess.CompletedProcess(
            args=["adb", "-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
            returncode=0,
            stdout="inet 192.168.1.111/24 brd 192.168.1.255 scope global wlan0\n",
            stderr="",
        ),
    }

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        return responses[tuple(args)]

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)

    assert get_device_route_ip("USB123") == "192.168.1.111"
    assert calls == [
        ["-s", "USB123", "shell", "ip", "route"],
        ["-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
    ]


def test_get_device_route_ip_falls_back_to_ifconfig_and_getprop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    responses = {
        ("-s", "USB123", "shell", "ip", "route"): subprocess.CompletedProcess(
            args=["adb", "-s", "USB123", "shell", "ip", "route"],
            returncode=0,
            stdout="",
            stderr="",
        ),
        ("-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"): subprocess.CompletedProcess(
            args=["adb", "-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
            returncode=0,
            stdout="",
            stderr="",
        ),
        ("-s", "USB123", "shell", "ifconfig", "wlan0"): subprocess.CompletedProcess(
            args=["adb", "-s", "USB123", "shell", "ifconfig", "wlan0"],
            returncode=0,
            stdout="",
            stderr="",
        ),
        ("-s", "USB123", "shell", "getprop", "dhcp.wlan0.ipaddress"): subprocess.CompletedProcess(
            args=["adb", "-s", "USB123", "shell", "getprop", "dhcp.wlan0.ipaddress"],
            returncode=0,
            stdout="192.168.1.111\n",
            stderr="",
        ),
    }

    def fake_run_adb(args: list[str], timeout: float = 10.0):
        calls.append(args)
        return responses[tuple(args)]

    monkeypatch.setattr("logcat_tool_for_win.adb.run_adb", fake_run_adb)

    assert get_device_route_ip("USB123") == "192.168.1.111"
    assert calls == [
        ["-s", "USB123", "shell", "ip", "route"],
        ["-s", "USB123", "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
        ["-s", "USB123", "shell", "ifconfig", "wlan0"],
        ["-s", "USB123", "shell", "getprop", "dhcp.wlan0.ipaddress"],
    ]


def test_run_adb_raises_adb_command_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "version"],
        returncode=1,
        stdout="",
        stderr="failed",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.iter_adb_paths", lambda: iter([Path("/adb.exe")]))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", lambda *args, **kwargs: completed)

    with pytest.raises(ADBCommandError, match="failed"):
        run_adb(["version"])


def test_run_adb_includes_stdout_error_when_stderr_has_daemon_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=1,
        stdout="failed to connect to 192.168.0.8:5555: Connection refused\n",
        stderr="* daemon not running; starting now at tcp:5037\n* daemon started successfully\n",
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.iter_adb_paths", lambda: iter([Path("/adb.exe")]))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", lambda *args, **kwargs: completed)

    with pytest.raises(ADBCommandError, match="failed to connect"):
        run_adb(["connect", "192.168.0.8:5555"])


def test_run_adb_reports_missing_adb_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("logcat_tool_for_win.adb.iter_adb_paths", lambda: iter([Path("/missing/adb.exe")]))

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", raise_missing)

    with pytest.raises(ADBCommandError, match="未找到 adb：/missing/adb.exe"):
        run_adb(["version"])


def test_run_adb_reports_permission_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("logcat_tool_for_win.adb.iter_adb_paths", lambda: iter([Path("/adb.exe")]))

    def raise_permission(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", raise_permission)

    with pytest.raises(ADBCommandError, match="无法执行 adb，请检查权限：/adb.exe"):
        run_adb(["version"])


def test_run_adb_reports_timeout_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("logcat_tool_for_win.adb.iter_adb_paths", lambda: iter([Path("/adb.exe")]))

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["adb", "devices"], timeout=2.0)

    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", raise_timeout)

    with pytest.raises(ADBCommandError, match=r"ADB 命令超时（2 秒）：devices"):
        run_adb(["devices"], timeout=2.0)


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


def test_run_adb_uses_explicit_standard_handles_for_gui_processes(
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
    assert captured_kwargs["stdout"] == subprocess.PIPE
    assert captured_kwargs["stderr"] == subprocess.PIPE
    assert "capture_output" not in captured_kwargs


def test_run_adb_hides_windows_adb_process_with_startupinfo(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
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

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    run_adb(["connect", "192.168.0.8:5555"])

    startupinfo = captured_kwargs["startupinfo"]
    assert captured_kwargs["creationflags"] == 0x08000000
    assert isinstance(startupinfo, fake_windows_startupinfo)
    assert startupinfo.dwFlags & 0x00000001
    assert startupinfo.wShowWindow == 0


def test_run_adb_avoids_windows_close_fds_handle_list(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
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

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    run_adb(["connect", "192.168.0.8:5555"])

    assert captured_kwargs["close_fds"] is False


def test_run_adb_retries_invalid_windows_handle_with_isolated_fds(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) == 1:
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert captured_kwargs[0]["close_fds"] is False
    assert captured_kwargs[1]["close_fds"] is True


def test_run_adb_retries_string_only_invalid_windows_handle(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) == 1:
            raise OSError("[WinError 6] 句柄无效。")
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert captured_kwargs[0]["close_fds"] is False
    assert captured_kwargs[1]["close_fds"] is True


def test_run_adb_falls_back_without_startupinfo_after_repeated_invalid_windows_handles(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) < 3:
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert "startupinfo" in captured_kwargs[0]
    assert "startupinfo" in captured_kwargs[1]
    assert "startupinfo" not in captured_kwargs[2]
    assert "creationflags" not in captured_kwargs[2]


def test_run_adb_falls_back_to_plain_isolated_process_after_invalid_windows_handles(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) < 4:
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert "startupinfo" not in captured_kwargs[3]
    assert "creationflags" not in captured_kwargs[3]
    assert captured_kwargs[3]["close_fds"] is True


def test_run_adb_falls_back_to_merged_output_after_all_invalid_windows_handle_retries(
    monkeypatch: pytest.MonkeyPatch,
    fake_windows_startupinfo,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["adb", "connect", "192.168.0.8:5555"],
        returncode=0,
        stdout="connected to 192.168.0.8:5555\n",
        stderr="",
    )
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) < 5:
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return completed

    monkeypatch.setattr("logcat_tool_for_win.adb.resolve_adb_path", lambda: Path("C:/adb.exe"))
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.run", fake_run)

    result = run_adb(["connect", "192.168.0.8:5555"])

    assert result is completed
    assert len(captured_kwargs) == 5
    assert captured_kwargs[4]["stdout"] == subprocess.PIPE
    assert captured_kwargs[4]["stderr"] == subprocess.STDOUT
