from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from pathlib import Path

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
        raise ValueError(f"Invalid TCP port: {port_text}")

    return target


def run_adb(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(resolve_adb_path()), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"adb exited with {result.returncode}"
        )
        raise ADBCommandError(message)
    return result


def list_devices() -> list[DeviceInfo]:
    result = run_adb(["devices", "-l"])
    return parse_devices_output(result.stdout)


def connect_device(target: str) -> str:
    result = run_adb(["connect", validate_tcp_target(target)])
    return result.stdout


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
