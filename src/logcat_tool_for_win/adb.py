from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

from logcat_tool_for_win.devices import parse_devices_output
from logcat_tool_for_win.filters import build_logcat_filter_spec
from logcat_tool_for_win.models import DeviceInfo, FilterState

DEFAULT_TCP_PORT = 5555
_runtime_adb_path: Optional[Path] = None
ADB_LAUNCH_OPTIONS = (
    (None, True),
    (True, True),
    (False, False),
    (True, False),
)
TCP_CONNECT_FAILURE_HINT = (
    "请确认手机和电脑在同一局域网；手机已开启 USB 调试并允许授权；"
    "如果还没有通过 USB 开启无线 ADB，请先用 USB 连上后点“USB 开启无线”；"
    "并确认端口未被防火墙拦截。"
)
ADB_INVALID_HANDLE_HINT = (
    "当前 adb 在这个 Windows 环境里无法正常启动。"
    "如果你在较老的 Windows 上运行，请优先使用 Releases 里的 "
    "logcat-tool-for-win-legacy-win7.zip；"
    "也可以安装可用的 Android platform-tools，并用 LOGCAT_TOOL_ADB 指向 adb.exe。"
)
DEVICE_IP_DISCOVERY_COMMANDS = (
    (["shell", "ip", "route"], "route"),
    (["shell", "ip", "-f", "inet", "addr", "show", "wlan0"], "ipv4"),
    (["shell", "ifconfig", "wlan0"], "ipv4"),
    (["shell", "getprop", "dhcp.wlan0.ipaddress"], "ipv4"),
)


class ADBCommandError(RuntimeError):
    pass


def _is_windows() -> bool:
    return os.name == "nt"


def _suppress_windows_error_dialogs() -> None:
    if not _is_windows():
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


def build_adb_process_kwargs(
    *,
    timeout: Optional[float] = None,
    bufsize: Optional[int] = None,
    close_fds: Optional[bool] = None,
    hide_window: bool = True,
    merge_stderr: bool = False,
) -> dict[str, object]:
    run_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        "text": True,
    }
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    if bufsize is not None:
        run_kwargs["bufsize"] = bufsize
    if _is_windows():
        run_kwargs["close_fds"] = False if close_fds is None else close_fds
        if hide_window:
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
            if startupinfo_factory is not None:
                startupinfo = startupinfo_factory()
                startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
                startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
                run_kwargs["startupinfo"] = startupinfo
    return run_kwargs


def iter_adb_process_kwargs(
    *,
    timeout: Optional[float] = None,
    bufsize: Optional[int] = None,
    merge_stderr: bool = False,
) -> Iterator[dict[str, object]]:
    for close_fds, hide_window in ADB_LAUNCH_OPTIONS:
        yield build_adb_process_kwargs(
            timeout=timeout,
            bufsize=bufsize,
            close_fds=close_fds,
            hide_window=hide_window,
            merge_stderr=merge_stderr,
        )


def _is_invalid_windows_handle(exc: OSError) -> bool:
    if not _is_windows():
        return False
    if getattr(exc, "winerror", None) == 6:
        return True
    message = str(exc).lower()
    return (
        "winerror 6" in message
        or "句柄无效" in message
        or "handle is invalid" in message
    )


def _iter_unique_paths(paths: Iterator[Optional[Path]]) -> Iterator[Path]:
    seen: set[str] = set()
    for path in paths:
        if path is None:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        yield path


def _preferred_runtime_adb_path() -> Optional[Path]:
    global _runtime_adb_path
    if _runtime_adb_path is None:
        return None
    if _runtime_adb_path.exists():
        return _runtime_adb_path
    _runtime_adb_path = None
    return None


def _default_adb_path() -> Path:
    override = os.environ.get("LOGCAT_TOOL_ADB")
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        bundled_adb = bundle_root / "platform-tools" / "adb.exe"
        if bundled_adb.exists():
            return bundled_adb
        packaged_adb = Path(sys.executable).resolve().parent / "platform-tools" / "adb.exe"
        if packaged_adb.exists():
            return packaged_adb
        path_adb = shutil.which("adb")
        if path_adb:
            return Path(path_adb)
        return packaged_adb

    source_adb = Path(__file__).resolve().parent / "resources" / "platform-tools" / "adb.exe"
    if source_adb.exists():
        return source_adb
    path_adb = shutil.which("adb")
    if path_adb:
        return Path(path_adb)
    return source_adb


def iter_adb_paths() -> Iterator[Path]:
    override = os.environ.get("LOGCAT_TOOL_ADB")
    if override:
        yield Path(override)
        return

    runtime_adb = _preferred_runtime_adb_path()
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        bundled_adb = bundle_root / "platform-tools" / "adb.exe"
        packaged_adb = Path(sys.executable).resolve().parent / "platform-tools" / "adb.exe"
        path_adb = shutil.which("adb")
        yielded = False
        for candidate in _iter_unique_paths(
            iter(
                (
                    runtime_adb,
                    bundled_adb if bundled_adb.exists() else None,
                    packaged_adb if packaged_adb.exists() else None,
                    Path(path_adb) if path_adb else None,
                )
            )
        ):
            yielded = True
            yield candidate
        if not yielded:
            yield packaged_adb
        return

    source_adb = Path(__file__).resolve().parent / "resources" / "platform-tools" / "adb.exe"
    path_adb = shutil.which("adb")
    yielded = False
    for candidate in _iter_unique_paths(
        iter(
            (
                runtime_adb,
                source_adb if source_adb.exists() else None,
                Path(path_adb) if path_adb else None,
            )
        )
    ):
        yielded = True
        yield candidate
    if not yielded:
        yield source_adb


def resolve_adb_path() -> Path:
    return next(iter_adb_paths(), _default_adb_path())


def _remember_runtime_adb_path(adb_path: Path) -> None:
    if os.environ.get("LOGCAT_TOOL_ADB"):
        return
    global _runtime_adb_path
    _runtime_adb_path = adb_path


def _format_invalid_handle_adb_error(adb_paths: list[Path], exc: OSError) -> ADBCommandError:
    attempted_paths = "；".join(str(path) for path in adb_paths)
    return ADBCommandError(
        f"无法启动 adb：{exc}\n"
        f"已尝试的 adb：{attempted_paths}\n"
        f"{ADB_INVALID_HANDLE_HINT}"
    )


def validate_tcp_target(target: str) -> str:
    stripped = target.strip()
    if ":" not in stripped:
        raise ValueError("请输入 IP:端口 格式的 TCP 目标。")

    host, port_text = (part.strip() for part in stripped.rsplit(":", 1))
    try:
        ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(f"无效的 TCP IP 地址：{host}") from exc

    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"无效的 TCP 端口：{port_text}") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"无效的 TCP 端口：{port_text}")

    return f"{host}:{port}"


def normalize_tcp_target(target: str, default_port: int = DEFAULT_TCP_PORT) -> str:
    stripped = target.strip()
    if not stripped:
        raise ValueError("请输入 TCP 目标地址。")
    if ":" in stripped:
        return validate_tcp_target(stripped)
    return validate_tcp_target(f"{stripped}:{validate_tcp_port(default_port)}")


def validate_tcp_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError(f"无效的 TCP 端口：{port}")
    return port


def extract_tcp_port(target: str, default: int = DEFAULT_TCP_PORT) -> int:
    stripped = target.strip()
    if not stripped:
        return validate_tcp_port(default)
    return int(normalize_tcp_target(stripped, default).rsplit(":", 1)[1])


def validate_connect_output(output: str, target: str) -> str:
    message = output.strip()
    lowered_target = target.lower()
    for line in message.splitlines():
        stripped_line = line.strip()
        lowered_line = stripped_line.lower()
        for prefix in ("connected to ", "already connected to "):
            if not lowered_line.startswith(prefix):
                continue
            connected_target = stripped_line[len(prefix) :].strip()
            if connected_target.lower() == lowered_target:
                return output
            raise ADBCommandError(stripped_line)
    raise ADBCommandError(message or f"无法连接 {target}")


def _combine_process_output(stdout: str, stderr: str) -> str:
    parts = [part.rstrip("\n") for part in (stdout, stderr) if part.strip()]
    if not parts:
        return ""
    suffix = "\n" if stdout.endswith("\n") or stderr.endswith("\n") else ""
    return "\n".join(parts) + suffix


def _specific_connect_failure_hint(target: str, message: str) -> str:
    lowered = message.lower()
    host = target.rsplit(":", 1)[0]
    if "cannot connect to daemon at tcp:5037" in lowered:
        return "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
    if "connection refused" in lowered or "actively refused" in lowered:
        return "目标端口拒绝连接。通常是手机端还没监听该端口；请先用 USB 连上后点“USB 开启无线”，再重新连接。"
    if "timed out" in lowered or "timeout" in lowered:
        return f"连接超时。请确认手机当前 IP 是否仍然是 {host}，并检查路由器隔离、防火墙或端口拦截。"
    if "no route to host" in lowered or "network is unreachable" in lowered:
        return "无法到达目标设备。通常是电脑和手机不在同一网段，或目标 IP 已变化。"
    return ""


def format_connect_error(target: str, error: ADBCommandError) -> ADBCommandError:
    message = str(error).strip()
    detail = _specific_connect_failure_hint(target, message)
    hint = TCP_CONNECT_FAILURE_HINT if not detail else f"{detail} {TCP_CONNECT_FAILURE_HINT}"
    if not message:
        return ADBCommandError(f"无法连接 {target}。{hint}")
    if message.startswith(f"无法连接 {target}"):
        return ADBCommandError(message)
    return ADBCommandError(f"无法连接 {target}。{hint}\n原始错误：{message}")


def parse_route_source_ip(output: str) -> str:
    fallback_address = ""
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
            if address.version == 4:
                return str(address)
            if not fallback_address:
                fallback_address = str(address)
    return fallback_address


def _extract_first_non_loopback_ipv4(output: str) -> str:
    for candidate in re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", output):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback:
            return str(address)
    return ""


def run_adb(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    _suppress_windows_error_dialogs()
    launch_kwargs = list(iter_adb_process_kwargs(timeout=timeout))
    if _is_windows():
        launch_kwargs.extend(iter_adb_process_kwargs(timeout=timeout, merge_stderr=True))
    adb_paths = list(iter_adb_paths())
    for path_index, adb_path in enumerate(adb_paths):
        command = [str(adb_path), *args]
        for attempt_index, process_kwargs in enumerate(launch_kwargs):
            try:
                result = subprocess.run(
                    command,
                    **process_kwargs,
                )
            except subprocess.TimeoutExpired as exc:
                raise ADBCommandError(f"ADB 命令超时（{timeout:g} 秒）：{' '.join(args)}") from exc
            except FileNotFoundError as exc:
                if path_index + 1 < len(adb_paths):
                    break
                raise ADBCommandError(f"未找到 adb：{adb_path}") from exc
            except PermissionError as exc:
                raise ADBCommandError(f"无法执行 adb，请检查权限：{adb_path}") from exc
            except OSError as exc:
                if attempt_index + 1 < len(launch_kwargs) and _is_invalid_windows_handle(exc):
                    continue
                if _is_invalid_windows_handle(exc) and path_index + 1 < len(adb_paths):
                    break
                if _is_invalid_windows_handle(exc):
                    raise _format_invalid_handle_adb_error(adb_paths, exc) from exc
                raise ADBCommandError(f"无法启动 adb：{exc}") from exc
            _remember_runtime_adb_path(adb_path)
            if result.returncode != 0:
                stdout_text = (result.stdout or "").strip()
                stderr_text = (result.stderr or "").strip()
                message_parts = [part for part in (stderr_text, stdout_text) if part]
                message = "\n".join(message_parts) or f"adb 退出，代码：{result.returncode}"
                raise ADBCommandError(message)
            return result
    raise ADBCommandError(f"未找到可用 adb：{_default_adb_path()}")


def list_devices() -> list[DeviceInfo]:
    result = run_adb(["devices", "-l"])
    return parse_devices_output(result.stdout)


def connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
    validated_target = normalize_tcp_target(target)
    last_error: Optional[ADBCommandError] = None
    for attempt in range(max(1, attempts)):
        if attempt and delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            result = run_adb(["connect", validated_target])
        except ADBCommandError as exc:
            last_error = exc
            continue
        try:
            return validate_connect_output(result.stdout, validated_target)
        except ADBCommandError as exc:
            combined_output = _combine_process_output(result.stdout, result.stderr)
            if combined_output and combined_output != result.stdout:
                try:
                    return validate_connect_output(combined_output, validated_target)
                except ADBCommandError as combined_exc:
                    last_error = combined_exc
                    continue
            last_error = exc
            continue
    if last_error is not None:
        raise format_connect_error(validated_target, last_error) from last_error
    raise ADBCommandError(f"无法连接 {validated_target}")


def enable_tcpip(serial: str, port: int = DEFAULT_TCP_PORT) -> str:
    result = run_adb(["-s", serial, "tcpip", str(validate_tcp_port(port))])
    return result.stdout


def get_device_route_ip(serial: str) -> str:
    for command, parser_kind in DEVICE_IP_DISCOVERY_COMMANDS:
        try:
            result = run_adb(["-s", serial, *command], timeout=5.0)
        except ADBCommandError:
            continue
        if parser_kind == "route":
            address = parse_route_source_ip(result.stdout)
        else:
            address = _extract_first_non_loopback_ipv4(result.stdout)
        if address:
            return address
    return ""


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
