from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from logcat_tool_for_win.devices import parse_devices_output
from logcat_tool_for_win.filters import build_logcat_filter_spec
from logcat_tool_for_win.models import DeviceInfo, FilterState

DEFAULT_TCP_PORT = 5555


class ADBCommandError(RuntimeError):
    pass


def _suppress_windows_error_dialogs() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        sem_failcriticalerrors = 0x0001
        sem_nogpfault_error_box = 0x0002
        kernel32 = ctypes.windll.kernel32
        current_mode = kernel32.SetErrorMode(0)
        kernel32.SetErrorMode(current_mode | sem_failcriticalerrors | sem_nogpfault_error_box)
    except Exception:
        return


def resolve_adb_path() -> Path:
    override = os.environ.get("LOGCAT_TOOL_ADB")
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        bundled_adb = bundle_root / "platform-tools" / "adb.exe"
        if bundled_adb.exists():
            return bundled_adb
        return Path(sys.executable).resolve().parent / "platform-tools" / "adb.exe"

    return Path(__file__).resolve().parent / "resources" / "platform-tools" / "adb.exe"


def validate_tcp_target(target: str) -> str:
    host, port_text = target.rsplit(":", 1)
    ipaddress.ip_address(host)

    port = int(port_text)
    if port < 1 or port > 65535:
        raise ValueError(f"无效的 TCP 端口：{port_text}")

    return target


def validate_tcp_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError(f"无效的 TCP 端口：{port}")
    return port


def extract_tcp_port(target: str, default: int = DEFAULT_TCP_PORT) -> int:
    stripped = target.strip()
    if not stripped:
        return validate_tcp_port(default)
    return int(validate_tcp_target(stripped).rsplit(":", 1)[1])


def parse_route_source_ip(output: str) -> str:
    for line in output.splitlines():
        parts = line.split()
        if "src" not in parts:
            continue
        src_index = parts.index("src")
        if src_index + 1 >= len(parts):
            continue
        try:
            address = ipaddress.ip_address(parts[src_index + 1])
        except ValueError:
            continue
        if not address.is_loopback:
            return str(address)
    return ""


def run_adb(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    _suppress_windows_error_dialogs()
    run_kwargs = {
        "capture_output": True,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    result = subprocess.run(
        [str(resolve_adb_path()), *args],
        **run_kwargs,
    )
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"adb 退出，代码：{result.returncode}"
        )
        raise ADBCommandError(message)
    return result


def list_devices() -> list[DeviceInfo]:
    result = run_adb(["devices", "-l"])
    return parse_devices_output(result.stdout)


def connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
    validated_target = validate_tcp_target(target)
    last_error: Optional[ADBCommandError] = None
    for attempt in range(max(1, attempts)):
        if attempt and delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            result = run_adb(["connect", validated_target])
        except ADBCommandError as exc:
            last_error = exc
            continue
        return result.stdout
    if last_error is not None:
        raise last_error
    raise ADBCommandError(f"无法连接 {validated_target}")


def enable_tcpip(serial: str, port: int = DEFAULT_TCP_PORT) -> str:
    result = run_adb(["-s", serial, "tcpip", str(validate_tcp_port(port))])
    return result.stdout


def get_device_route_ip(serial: str) -> str:
    result = run_adb(["-s", serial, "shell", "ip", "route"], timeout=5.0)
    return parse_route_source_ip(result.stdout)


def restart_server() -> None:
    run_adb(["kill-server"])
    run_adb(["start-server"])


def clear_logcat(serial: str) -> subprocess.CompletedProcess[str]:
    return run_adb(["-s", serial, "logcat", "-c"])


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
