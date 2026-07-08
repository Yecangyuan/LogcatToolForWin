from __future__ import annotations

from collections import deque
from dataclasses import replace
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, TypeVar, Union

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError as exc:  # pragma: no cover - depends on interpreter build
    tk = None
    filedialog = None
    messagebox = None
    ttk = None
    TK_IMPORT_ERROR: Optional[ModuleNotFoundError] = exc
else:
    TK_IMPORT_ERROR = None

from logcat_tool_for_win.adb import (
    ADBCommandError,
    DEFAULT_TCP_PORT,
    build_logcat_command,
    clear_logcat,
    connect_device,
    enable_tcpip,
    extract_tcp_port,
    get_manual_adb_path,
    get_device_route_ip,
    is_adb_auth_failure_message,
    is_local_adb_service_failure_message,
    list_devices,
    normalize_tcp_target,
    restart_server,
    resolve_adb_path,
    set_manual_adb_path,
    validate_tcp_port,
)
from logcat_tool_for_win.config import (
    QUEUE_DRAIN_MS,
    RAW_LOG_CAP,
    VISIBLE_LOG_CAP,
    get_presets_file,
    get_state_file,
)
from logcat_tool_for_win.devices import device_label
from logcat_tool_for_win.export import export_lines
from logcat_tool_for_win.filters import (
    PreparedFilterState,
    entry_matches_prepared,
    normalize_tag_filters,
    prepare_filter_state,
)
from logcat_tool_for_win.highlight import (
    DEFAULT_LEVEL_COLORS,
    build_highlight_rule_cache_key,
    match_highlight_rules,
)
from logcat_tool_for_win.log_stream import LogcatSession
from logcat_tool_for_win.models import (
    AppStatus,
    DeviceInfo,
    FilterState,
    HighlightRule,
    LogEntry,
    NamedPreset,
    StreamEvent,
)
from logcat_tool_for_win.presets import load_presets, load_state, save_preset, save_state

MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_MS = 2_000
WIRELESS_ROUTE_IP_RETRY_ATTEMPTS = 4
WIRELESS_ROUTE_IP_RETRY_DELAY_SECONDS = 0.5
MAX_EVENTS_PER_TICK = 500
FILTER_REFRESH_DELAY_MS = 120
MAX_RECENT_TARGETS = 8
DEVICE_SYNC_TASK_KEY = "device-sync"
CLEAR_LOGCAT_TASK_KEY = "clear-logcat"
T = TypeVar("T")

BG = "#0F172A"
SURFACE = "#1E293B"
SURFACE_ALT = "#334155"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
ACCENT = "#22C55E"
WARN = "#FB923C"
ERROR = "#F87171"
HIGHLIGHT_TAG_PREFIX = "highlight::"
WIRELESS_ADB_BUTTON_LABEL = "USB 开启无线"
WIRELESS_ADB_ERROR_TITLE = f"{WIRELESS_ADB_BUTTON_LABEL}失败"
RESTART_ADB_BUTTON_LABEL = "重启 ADB"
ADB_PATH_BUTTON_LABEL = "ADB 路径"
STREAM_STATE_LABELS = {
    "idle": "空闲",
    "streaming": "采集中",
    "reconnecting": "重连中",
    "failed": "失败",
}


def build_summary_text(total_lines: int, visible_lines: int, stream_state: str) -> str:
    return f"总行数：{total_lines} | 可见：{visible_lines} | 状态：{format_stream_state(stream_state)}"


def format_stream_state(stream_state: str) -> str:
    return STREAM_STATE_LABELS.get(stream_state, stream_state)


def build_highlight_rules(raw: str) -> list[HighlightRule]:
    rules: list[HighlightRule] = []
    seen: set[str] = set()
    for item in raw.split(","):
        pattern = item.strip()
        if pattern and pattern not in seen:
            seen.add(pattern)
            rules.append(HighlightRule(name=pattern, pattern=pattern, foreground=WARN))
    return rules


def build_highlight_text_tag(rule_name: str) -> str:
    return f"{HIGHLIGHT_TAG_PREFIX}{rule_name}"


def format_status_text(status: AppStatus) -> str:
    base = (
        f"ADB：{'就绪' if status.adb_ready else '不可用'} | "
        f"ADB路径：{status.adb_path or '-'} | "
        f"设备：{status.active_device_serial or '-'} | "
        f"状态：{format_stream_state(status.stream_state)} | "
        f"队列：{status.queue_depth}"
    )
    if status.reconnect_attempt:
        base += f" | 第 {status.reconnect_attempt} 次重连"
    if status.last_error:
        base += f" | {status.last_error}"
    return base


def _ensure_tcp_device(devices: Iterable[DeviceInfo], target: str) -> list[DeviceInfo]:
    result = list(devices)
    for index, device in enumerate(result):
        if device.serial == target:
            if device.transport != "tcp" or device.state != "device":
                result[index] = DeviceInfo(
                    serial=target,
                    display_name=device.display_name or target,
                    transport="tcp",
                    state="device",
                    model=device.model,
                    product=device.product,
                    raw_descriptor=f"{target}\tdevice",
                )
            return result
    result.append(
        DeviceInfo(
            serial=target,
            display_name=target,
            transport="tcp",
            state="device",
            model="",
            product="",
            raw_descriptor=f"{target}\tdevice",
        )
    )
    return result


class LogcatToolGUI:
    def __init__(self, root: tk.Tk) -> None:
        if TK_IMPORT_ERROR is not None:
            raise RuntimeError("当前 Python 环境不支持 Tkinter。") from TK_IMPORT_ERROR
        self.root = root
        self.root.title("Windows Logcat 工具")
        self.root.geometry("1280x780")
        self.root.minsize(980, 620)
        self.root.configure(bg=BG)

        self.state_file = get_state_file()
        self.presets_file = get_presets_file()
        self.events: queue.Queue[StreamEvent] = queue.Queue()
        self.devices: list[DeviceInfo] = []
        self.session: Optional[LogcatSession] = None
        self.raw_lines: deque[LogEntry] = deque(maxlen=RAW_LOG_CAP)
        self.visible_lines: deque[LogEntry] = deque(maxlen=VISIBLE_LOG_CAP)
        (
            self.filters,
            self.highlight_rules,
            recent_target,
            recent_targets,
            manual_adb_path,
        ) = load_state(
            self.state_file
        )
        saved_adb_restore_message = self._restore_saved_manual_adb_path(manual_adb_path)
        self.named_presets = load_presets(self.presets_file)
        self.status = AppStatus()
        self.status.adb_path = str(resolve_adb_path())
        self.status.last_error = saved_adb_restore_message
        self.manual_stop = True
        self.reconnect_target_serial = ""
        self.recent_targets = recent_targets[:MAX_RECENT_TARGETS]
        self._configured_highlight_styles: dict[str, tuple[str, str]] = {}
        self._highlight_tag_map_cache_key: tuple[str, ...] = ()
        self._highlight_tag_map_cache: dict[str, str] = {}
        self._background_task_versions: dict[str, int] = {}
        self._filter_refresh_suspended = False
        self._filter_trace_ids: list[tuple[tk.Variable, str]] = []
        self._poll_stream_callback_id: Optional[object] = None

        self.device_var = tk.StringVar()
        self.connect_var = tk.StringVar(value=recent_target)
        self.level_var = tk.StringVar(value=self.filters.minimum_level)
        self.tag_var = tk.StringVar(value=", ".join(self.filters.tag_filters))
        self.keyword_var = tk.StringVar(value=self.filters.keyword)
        self.highlight_var = tk.StringVar(
            value=", ".join(rule.pattern for rule in self.highlight_rules)
        )
        self.preset_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value=build_summary_text(0, 0, self.status.stream_state))
        self.status_var = tk.StringVar(value=format_status_text(self.status))
        self.auto_scroll_var = tk.BooleanVar(value=self.filters.auto_scroll)
        self.match_only_var = tk.BooleanVar(value=self.filters.match_only)

        self._configure_style()
        self._build_ui()
        self._bind_shortcuts()
        self._bind_filter_updates()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(0, self.refresh_devices_async)
        self._refresh_visible_entries()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background=BG)
        style.configure("Panel.TFrame", background=SURFACE)
        style.configure("Panel.TLabelframe", background=SURFACE, foreground=TEXT, borderwidth=1)
        style.configure("Panel.TLabelframe.Label", background=SURFACE, foreground=TEXT)
        style.configure("Panel.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Toolbar.TFrame", background=BG)
        style.configure(
            "App.TButton",
            background=SURFACE_ALT,
            foreground=TEXT,
            borderwidth=0,
            focusthickness=0,
            padding=6,
        )
        style.map(
            "App.TButton",
            background=[("active", ACCENT), ("pressed", ACCENT)],
            foreground=[("active", BG), ("pressed", BG)],
        )
        style.configure(
            "App.TCheckbutton",
            background=SURFACE,
            foreground=TEXT,
            indicatorcolor=SURFACE_ALT,
        )
        style.configure(
            "App.TCombobox",
            fieldbackground=BG,
            background=SURFACE_ALT,
            foreground=TEXT,
            arrowcolor=TEXT,
        )
        style.configure(
            "App.TEntry",
            fieldbackground=BG,
            foreground=TEXT,
            insertcolor=TEXT,
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(outer, style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 10))

        self.device_combo = ttk.Combobox(
            toolbar,
            textvariable=self.device_var,
            state="readonly",
            width=34,
            style="App.TCombobox",
        )
        self.device_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_selected_device())

        ttk.Button(toolbar, text="刷新", style="App.TButton", command=self.refresh_devices_async).pack(
            side=tk.LEFT, padx=4
        )
        self.connect_combo = ttk.Combobox(
            toolbar,
            textvariable=self.connect_var,
            width=20,
            style="App.TCombobox",
        )
        self.connect_combo.pack(side=tk.LEFT, padx=8)
        self._refresh_connect_choices()
        ttk.Button(toolbar, text="连接", style="App.TButton", command=self.connect_tcp).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            toolbar,
            text=WIRELESS_ADB_BUTTON_LABEL,
            style="App.TButton",
            command=self.enable_wireless_adb,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="开始", style="App.TButton", command=self.start_stream).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(toolbar, text="停止", style="App.TButton", command=self.stop_stream).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            toolbar,
            text="清空视图",
            style="App.TButton",
            command=self.clear_view,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            toolbar,
            text="清空设备日志",
            style="App.TButton",
            command=self.clear_device_logcat,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            toolbar,
            text="导出可见",
            style="App.TButton",
            command=self.export_visible,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="导出原始", style="App.TButton", command=self.export_raw).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            toolbar,
            text="重启 ADB",
            style="App.TButton",
            command=self.restart_adb,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            toolbar,
            text=ADB_PATH_BUTTON_LABEL,
            style="App.TButton",
            command=self.configure_adb_path,
        ).pack(side=tk.LEFT, padx=4)

        body = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(body, style="Panel.TFrame", padding=12)
        viewer = ttk.Frame(body, style="Panel.TFrame", padding=12)
        body.add(controls, weight=1)
        body.add(viewer, weight=4)

        filters_panel = ttk.LabelFrame(
            controls,
            text="筛选",
            style="Panel.TLabelframe",
            padding=10,
        )
        filters_panel.pack(fill=tk.X)

        self._panel_label(filters_panel, "级别").pack(anchor=tk.W)
        self.level_combo = ttk.Combobox(
            filters_panel,
            textvariable=self.level_var,
            state="readonly",
            values=("V", "D", "I", "W", "E", "F"),
            style="App.TCombobox",
        )
        self.level_combo.pack(fill=tk.X, pady=(4, 8))

        self._panel_label(filters_panel, "标签（逗号分隔）").pack(anchor=tk.W)
        ttk.Entry(filters_panel, textvariable=self.tag_var, style="App.TEntry").pack(
            fill=tk.X, pady=(4, 8)
        )

        self._panel_label(filters_panel, "关键词").pack(anchor=tk.W)
        self.keyword_entry = ttk.Entry(
            filters_panel,
            textvariable=self.keyword_var,
            style="App.TEntry",
        )
        self.keyword_entry.pack(fill=tk.X, pady=(4, 8))

        self._panel_label(filters_panel, "高亮关键词").pack(anchor=tk.W)
        ttk.Entry(filters_panel, textvariable=self.highlight_var, style="App.TEntry").pack(
            fill=tk.X, pady=(4, 8)
        )

        ttk.Checkbutton(
            filters_panel,
            text="自动滚动",
            style="App.TCheckbutton",
            variable=self.auto_scroll_var,
        ).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(
            filters_panel,
            text="仅显示匹配",
            style="App.TCheckbutton",
            variable=self.match_only_var,
        ).pack(anchor=tk.W, pady=(2, 0))

        presets_panel = ttk.LabelFrame(
            controls,
            text="预设",
            style="Panel.TLabelframe",
            padding=10,
        )
        presets_panel.pack(fill=tk.X, pady=(12, 0))

        self._panel_label(presets_panel, "预设名称").pack(anchor=tk.W)
        self.preset_combo = ttk.Combobox(
            presets_panel,
            textvariable=self.preset_var,
            style="App.TCombobox",
        )
        self.preset_combo.pack(fill=tk.X, pady=(4, 8))
        self._refresh_preset_choices()

        ttk.Button(
            presets_panel,
            text="保存预设",
            style="App.TButton",
            command=self.save_named_preset,
        ).pack(fill=tk.X, pady=2)
        ttk.Button(
            presets_panel,
            text="加载预设",
            style="App.TButton",
            command=self.load_named_preset,
        ).pack(fill=tk.X, pady=2)
        ttk.Button(
            presets_panel,
            text="保存会话状态",
            style="App.TButton",
            command=self.save_session_state,
        ).pack(fill=tk.X, pady=(10, 2))

        viewer_header = tk.Frame(viewer, bg=SURFACE)
        viewer_header.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            viewer_header,
            text="实时日志",
            bg=SURFACE,
            fg=TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            viewer_header,
            textvariable=self.summary_var,
            bg=SURFACE,
            fg=MUTED,
            anchor="w",
        ).pack(fill=tk.X, pady=(4, 0))

        text_frame = tk.Frame(viewer, bg=SURFACE_ALT, highlightthickness=1, highlightbackground=SURFACE_ALT)
        text_frame.pack(fill=tk.BOTH, expand=True)

        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        x_scroll = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.text = tk.Text(
            text_frame,
            wrap="none",
            bg="#020617",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#14532D",
            relief=tk.FLAT,
            borderwidth=0,
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.configure(state=tk.DISABLED)
        y_scroll.config(command=self.text.yview)
        x_scroll.config(command=self.text.xview)
        self.text.tag_config("filtered-out", foreground=MUTED)
        for level, color in DEFAULT_LEVEL_COLORS.items():
            self.text.tag_config(level, foreground=color)

        tk.Label(
            outer,
            textvariable=self.status_var,
            bg=SURFACE_ALT,
            fg=TEXT,
            anchor="w",
            padx=10,
            pady=8,
        ).pack(fill=tk.X, pady=(10, 0))

    def _panel_label(self, parent: Union[ttk.Frame, ttk.LabelFrame], text: str) -> ttk.Label:
        return ttk.Label(parent, text=text, style="Panel.TLabel")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-f>", lambda _event: self.focus_keyword())
        self.root.bind("<Control-l>", lambda _event: self.clear_view())
        self.root.bind("<Control-s>", lambda _event: self.save_session_state())
        self.root.bind("<Control-e>", lambda _event: self.export_visible())
        self.root.bind("<Control-Shift-E>", lambda _event: self.export_raw())
        self.root.bind("<F5>", lambda _event: self.refresh_devices_async())

    def _bind_filter_updates(self) -> None:
        for variable in (
            self.level_var,
            self.tag_var,
            self.keyword_var,
            self.match_only_var,
        ):
            trace_id = variable.trace_add("write", self._handle_filter_trace)
            self._filter_trace_ids.append((variable, trace_id))
        highlight_trace_id = self.highlight_var.trace_add("write", self._handle_highlight_trace)
        self._filter_trace_ids.append((self.highlight_var, highlight_trace_id))
        auto_scroll_trace_id = self.auto_scroll_var.trace_add("write", self._handle_auto_scroll_trace)
        self._filter_trace_ids.append((self.auto_scroll_var, auto_scroll_trace_id))

    def _handle_filter_trace(self, *_args: object) -> None:
        if self._filter_refresh_suspended:
            return
        self.filters = self._current_filters()
        self._schedule_filter_refresh("full")

    def _handle_highlight_trace(self, *_args: object) -> None:
        if self._filter_refresh_suspended:
            return
        self.highlight_rules = self._current_highlight_rules()
        self._schedule_filter_refresh("highlight")

    def _handle_auto_scroll_trace(self, *_args: object) -> None:
        if self._filter_refresh_suspended:
            return
        self.filters = replace(self.filters, auto_scroll=self.auto_scroll_var.get())
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def _schedule_filter_refresh(self, refresh_kind: str = "full") -> None:
        pending_callback_id = getattr(self, "_pending_filter_refresh_id", None)
        pending_refresh_kind = getattr(self, "_pending_filter_refresh_kind", None)
        if pending_callback_id is not None:
            self._cancel_ui_callback(pending_callback_id)
        next_refresh_kind = (
            "full"
            if refresh_kind == "full" or pending_refresh_kind == "full"
            else "highlight"
        )
        version = getattr(self, "_filter_refresh_version", 0) + 1
        self._filter_refresh_version = version
        self._pending_filter_refresh_kind = next_refresh_kind
        self._pending_filter_refresh_id = self._schedule_ui_callback_handle(
            FILTER_REFRESH_DELAY_MS,
            lambda expected_version=version, expected_kind=next_refresh_kind: (
                self._run_scheduled_filter_refresh(expected_version, expected_kind)
            ),
        )

    def _run_scheduled_filter_refresh(self, expected_version: int, expected_kind: str) -> None:
        self._pending_filter_refresh_id = None
        self._pending_filter_refresh_kind = None
        if self._filter_refresh_suspended:
            return
        if getattr(self, "_filter_refresh_version", 0) != expected_version:
            return
        if expected_kind == "full":
            self._refresh_visible_entries()
            return
        self._refresh_highlight_entries()

    def _invalidate_pending_filter_refreshes(self) -> None:
        pending_callback_id = getattr(self, "_pending_filter_refresh_id", None)
        if pending_callback_id is not None:
            self._cancel_ui_callback(pending_callback_id)
        self._filter_refresh_version = getattr(self, "_filter_refresh_version", 0) + 1
        self._pending_filter_refresh_id = None
        self._pending_filter_refresh_kind = None

    def _run_background_task(
        self,
        pending_message: str,
        action: Callable[[], T],
        on_success: Callable[[T], None],
        on_error: Callable[[Exception], None],
        task_key: Optional[str] = None,
    ) -> None:
        self.status.last_error = pending_message
        self._update_status()
        task_version = self._advance_background_task_version(task_key)

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self._schedule_ui_callback(
                    0,
                    lambda error=exc, key=task_key, version=task_version: self._deliver_background_error(
                        key,
                        version,
                        error,
                        on_error,
                    ),
                )
            else:
                self._schedule_ui_callback(
                    0,
                    lambda value=result, key=task_key, version=task_version: self._deliver_background_success(
                        key,
                        version,
                        value,
                        on_success,
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _advance_background_task_version(self, task_key: Optional[str]) -> Optional[int]:
        if not task_key:
            return None
        versions = getattr(self, "_background_task_versions", None)
        if versions is None:
            versions = {}
            self._background_task_versions = versions
        version = versions.get(task_key, 0) + 1
        versions[task_key] = version
        return version

    def _is_current_background_task(
        self,
        task_key: Optional[str],
        task_version: Optional[int],
    ) -> bool:
        if not task_key or task_version is None:
            return True
        versions = getattr(self, "_background_task_versions", {})
        return versions.get(task_key) == task_version

    def _deliver_background_success(
        self,
        task_key: Optional[str],
        task_version: Optional[int],
        result: T,
        on_success: Callable[[T], None],
    ) -> None:
        if self._is_ui_closing():
            return
        if not self._is_current_background_task(task_key, task_version):
            return
        on_success(result)

    def _deliver_background_error(
        self,
        task_key: Optional[str],
        task_version: Optional[int],
        exc: Exception,
        on_error: Callable[[Exception], None],
    ) -> None:
        if self._is_ui_closing():
            return
        if not self._is_current_background_task(task_key, task_version):
            return
        on_error(exc)

    def _is_ui_closing(self) -> bool:
        return bool(getattr(self, "_ui_closing", False))

    def _schedule_ui_callback_handle(self, delay: int, callback: Callable[[], None]) -> Optional[object]:
        if self._is_ui_closing():
            return None
        try:
            return self.root.after(delay, callback)
        except Exception as exc:
            message = str(exc).lower()
            if "destroyed" in message or "can't invoke" in message or "invalid command" in message:
                return None
            raise

    def _cancel_ui_callback(self, callback_id: object) -> bool:
        after_cancel = getattr(self.root, "after_cancel", None)
        if after_cancel is None:
            return False
        try:
            after_cancel(callback_id)
        except Exception as exc:
            message = str(exc).lower()
            if "destroyed" in message or "can't invoke" in message or "invalid command" in message:
                return False
            raise
        return True

    def _schedule_ui_callback(self, delay: int, callback: Callable[[], None]) -> bool:
        return self._schedule_ui_callback_handle(delay, callback) is not None

    def _ensure_poll_stream_scheduled(self, delay: int = QUEUE_DRAIN_MS) -> bool:
        if getattr(self, "_poll_stream_callback_id", None) is not None:
            return False
        callback_id = self._schedule_ui_callback_handle(delay, self._poll_stream)
        if callback_id is None:
            return False
        self._poll_stream_callback_id = callback_id
        return True

    def _cancel_poll_stream_callback(self) -> bool:
        callback_id = getattr(self, "_poll_stream_callback_id", None)
        if callback_id is None:
            return False
        self._poll_stream_callback_id = None
        return self._cancel_ui_callback(callback_id)

    def _refresh_preset_choices(self) -> None:
        names = sorted(self.named_presets)
        self.preset_combo["values"] = names
        if not self.preset_var.get() and names:
            self.preset_var.set(names[0])

    def focus_keyword(self) -> None:
        self.keyword_entry.focus_set()

    def _stream_target_serial(self) -> str:
        return getattr(self, "reconnect_target_serial", "") or self.status.active_device_serial

    def _build_stale_stream_target_device(self, serial: str) -> DeviceInfo:
        for device in self.devices:
            if device.serial == serial:
                return DeviceInfo(
                    serial=device.serial,
                    display_name=device.display_name,
                    transport=device.transport,
                    state="offline",
                    model=device.model,
                    product=device.product,
                    raw_descriptor=device.raw_descriptor,
                )
        transport = "tcp" if ":" in serial else "usb"
        return DeviceInfo(
            serial=serial,
            display_name=serial,
            transport=transport,
            state="offline",
            model="",
            product="",
            raw_descriptor=f"{serial}\toffline",
        )

    def _restore_saved_manual_adb_path(self, manual_adb_path: str) -> str:
        stripped_path = manual_adb_path.strip()
        if not stripped_path:
            set_manual_adb_path(None)
            return ""
        path = Path(stripped_path)
        if path.exists():
            set_manual_adb_path(path)
            return ""
        set_manual_adb_path(None)
        return f"保存的 ADB 路径已失效，已恢复自动检测：{path}"

    def _preserve_stream_target_device(
        self,
        devices: list[DeviceInfo],
    ) -> tuple[list[DeviceInfo], Optional[DeviceInfo]]:
        target_serial = self._stream_target_serial()
        if not target_serial:
            return list(devices), None
        preserved_devices = list(devices)
        for device in preserved_devices:
            if device.serial == target_serial:
                return preserved_devices, device
        stale_target = self._build_stale_stream_target_device(target_serial)
        preserved_devices.append(stale_target)
        return preserved_devices, stale_target

    def _apply_devices(self, devices: list[DeviceInfo]) -> None:
        self.status.adb_path = str(resolve_adb_path())
        current_label = self.device_var.get()
        preserve_stream_target = self.status.stream_state in {"streaming", "reconnecting"}
        stream_target: Optional[DeviceInfo] = None
        if preserve_stream_target:
            devices, stream_target = self._preserve_stream_target_device(devices)
        self.devices = devices
        labels = [device_label(device) for device in self.devices]
        self.device_combo["values"] = labels
        if preserve_stream_target and stream_target is not None:
            self.device_var.set(device_label(stream_target))
        elif current_label in labels:
            self.device_var.set(current_label)
        elif labels:
            self.device_var.set(labels[0])
        else:
            self.device_var.set("")
        self.status.adb_ready = True
        self.status.last_error = ""
        if not preserve_stream_target:
            self._sync_selected_device(update_status=False)
        self._update_status()

    def _handle_refresh_devices_error(self, exc: Exception) -> None:
        self.status.adb_path = str(resolve_adb_path())
        preserve_stream_target = self.status.stream_state in {"streaming", "reconnecting"}
        if preserve_stream_target:
            self.devices, stream_target = self._preserve_stream_target_device(self.devices)
        else:
            stream_target = None
        labels = [device_label(device) for device in self.devices]
        if labels:
            self.device_combo["values"] = labels
            if preserve_stream_target and stream_target is not None:
                self.device_var.set(device_label(stream_target))
            elif self.device_var.get() not in labels:
                self.device_var.set(labels[0])
        else:
            self.device_combo["values"] = ()
            self.device_var.set("")
        self.status.adb_ready = False
        self.status.last_error = str(exc)
        if not preserve_stream_target:
            if labels:
                try:
                    self.status.active_device_serial = self._current_device().serial
                except ValueError:
                    self.status.active_device_serial = self.devices[0].serial
            else:
                self.status.active_device_serial = ""
        self._update_status()

    def refresh_devices(self) -> None:
        try:
            devices = list_devices()
        except Exception as exc:
            self._handle_user_refresh_devices_error(exc)
        else:
            self._apply_devices(devices)

    def refresh_devices_async(self) -> None:
        self._run_background_task(
            "正在刷新设备...",
            list_devices,
            self._apply_devices,
            self._handle_user_refresh_devices_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _handle_user_refresh_devices_error(self, exc: Exception) -> None:
        self._handle_refresh_devices_error(exc)
        message = str(exc)
        if self._show_adb_launch_recovery_prompt(message):
            return
        self._show_local_adb_service_recovery_prompt(message)

    def connect_tcp(self) -> None:
        raw_target = self.connect_var.get().strip()
        selected_usb_device = self._selected_usb_device_for_tcp_connect()
        if not raw_target:
            if selected_usb_device is not None:
                self._run_background_task(
                    f"正在为 {selected_usb_device.serial} 开启无线 ADB...",
                    lambda: self._prepare_wireless_adb(selected_usb_device.serial, DEFAULT_TCP_PORT),
                    self._handle_wireless_adb_success,
                    self._handle_wireless_adb_error,
                    task_key=DEVICE_SYNC_TASK_KEY,
                )
                return
            wireless_warning = self._wireless_prepare_warning_for_selected_device()
            if wireless_warning is not None:
                messagebox.showwarning(*wireless_warning)
                return
            messagebox.showwarning("需要目标地址", "请输入 IP 或 IP:端口 格式的 TCP 目标。")
            return
        if raw_target.isdigit() and selected_usb_device is not None:
            try:
                port = validate_tcp_port(int(raw_target))
            except ValueError as exc:
                messagebox.showwarning("TCP 端口无效", str(exc))
                return
            self._run_background_task(
                f"正在为 {selected_usb_device.serial} 开启无线 ADB...",
                lambda: self._prepare_wireless_adb(selected_usb_device.serial, port),
                self._handle_wireless_adb_success,
                self._handle_wireless_adb_error,
                task_key=DEVICE_SYNC_TASK_KEY,
            )
            return
        if raw_target.isdigit():
            wireless_warning = self._wireless_prepare_warning_for_selected_device()
            if wireless_warning is not None:
                messagebox.showwarning(*wireless_warning)
            else:
                messagebox.showwarning("需要 USB 设备", "请先选择通过 USB 连接的设备。")
            return
        try:
            target = normalize_tcp_target(raw_target)
        except ValueError as exc:
            if ":" in raw_target:
                _host_text, port_text = (part.strip() for part in raw_target.rsplit(":", 1))
                try:
                    port = validate_tcp_port(int(port_text))
                except ValueError as port_exc:
                    messagebox.showwarning("TCP 端口无效", str(port_exc))
                    return
                if not _host_text:
                    if selected_usb_device is not None:
                        self._run_background_task(
                            f"正在为 {selected_usb_device.serial} 开启无线 ADB...",
                            lambda: self._prepare_wireless_adb(selected_usb_device.serial, port),
                            self._handle_wireless_adb_success,
                            self._handle_wireless_adb_error,
                            task_key=DEVICE_SYNC_TASK_KEY,
                        )
                        return
                    wireless_warning = self._wireless_prepare_warning_for_selected_device()
                    if wireless_warning is not None:
                        messagebox.showwarning(*wireless_warning)
                    else:
                        messagebox.showwarning("需要 USB 设备", "请先选择通过 USB 连接的设备。")
                    return
            if selected_usb_device is not None:
                if ":" not in raw_target:
                    messagebox.showwarning("TCP 目标无效", str(exc))
                    return
                if _host_text:
                    messagebox.showwarning("TCP 目标无效", str(exc))
                    return
            messagebox.showwarning("TCP 目标无效", str(exc))
            return
        if target != raw_target:
            self.connect_var.set(target)

        existing_devices = list(self.devices)

        def action() -> tuple[str, str, list[DeviceInfo]]:
            connected_target, message = self._connect_tcp_target_with_usb_fallback(
                target,
                selected_usb_device,
            )
            message = message.strip() or f"已连接 {connected_target}"
            try:
                devices = list_devices()
            except Exception as exc:
                return (
                    connected_target,
                    f"{message}；设备列表刷新失败：{exc}",
                    _ensure_tcp_device(existing_devices, connected_target),
                )
            return connected_target, message, _ensure_tcp_device(devices, connected_target)

        self._run_background_task(
            f"正在连接 {target}...",
            action,
            self._handle_connect_tcp_success,
            self._handle_connect_tcp_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _handle_connect_tcp_success(self, result: tuple[str, str, list[DeviceInfo]]) -> None:
        target, message, devices = result
        self._remember_connect_target(target)
        self.connect_var.set(target)
        self._apply_devices(devices)
        self._select_device_by_serial(target)
        self.status.last_error = message or f"已连接 {target}"
        self._update_status()

    def _handle_connect_tcp_error(self, exc: Exception) -> None:
        message = self._format_connect_tcp_error_message(exc)
        if self._is_adb_launch_failure_message(message) or self._is_local_adb_service_failure_message(message):
            self._handle_refresh_devices_error(exc)
        if self._show_adb_launch_recovery_prompt(message):
            return
        if self._show_local_adb_service_recovery_prompt(message):
            return
        messagebox.showerror("连接失败", message)
        self.status.last_error = message
        self._update_status()

    def _show_cached_adb_recovery_prompt(self, message: str = "") -> bool:
        if message:
            message = message.strip()
        else:
            message = (self.status.last_error or "").strip()
        if not message:
            return False
        recovery_message = message.split("\n\n", 1)[0].strip()
        if self._show_adb_launch_recovery_prompt(recovery_message):
            return True
        return self._show_local_adb_service_recovery_prompt(recovery_message)

    def enable_wireless_adb(self) -> None:
        if not self.status.adb_ready:
            if self._show_cached_adb_recovery_prompt():
                return
            messagebox.showwarning("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")
            return

        try:
            device = self._current_device()
        except ValueError as exc:
            messagebox.showwarning("需要选择设备", str(exc))
            return

        if device.transport != "usb":
            messagebox.showwarning("需要 USB 设备", "请先选择通过 USB 连接的设备。")
            return
        if device.state != "device":
            messagebox.showwarning(
                "设备未就绪",
                f"当前设备状态为 {device.state}，请先选择已就绪的 USB 设备。",
            )
            return

        raw_target = self.connect_var.get().strip()
        try:
            port, preferred_target = self._resolve_wireless_connect_preferences(raw_target)
        except ValueError as exc:
            title = "TCP 端口无效" if str(exc).startswith("无效的 TCP 端口") else "TCP 目标无效"
            messagebox.showwarning(title, str(exc))
            return

        self._run_background_task(
            f"正在为 {device.serial} 开启无线 ADB...",
            lambda: self._prepare_wireless_adb(device.serial, port, preferred_target),
            self._handle_wireless_adb_success,
            self._handle_wireless_adb_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _resolve_wireless_connect_preferences(self, raw_target: str) -> tuple[int, str]:
        stripped = raw_target.strip()
        if not stripped:
            return DEFAULT_TCP_PORT, ""
        if ":" not in stripped:
            if stripped.isdigit():
                return validate_tcp_port(int(stripped)), ""
            return DEFAULT_TCP_PORT, normalize_tcp_target(stripped)

        host_text, port_text = (part.strip() for part in stripped.rsplit(":", 1))
        try:
            port = validate_tcp_port(int(port_text))
        except ValueError as exc:
            raise ValueError(f"无效的 TCP 端口：{port_text}") from exc
        if not host_text:
            return port, ""
        return port, normalize_tcp_target(f"{host_text}:{port}")

    def _prepare_wireless_adb(
        self,
        serial: str,
        port: int,
        preferred_target: str = "",
    ) -> tuple[str, str, list[DeviceInfo]]:
        route_ip = self._route_ip_for_serial(serial)

        tcpip_message = enable_tcpip(serial, port).strip()
        if not route_ip:
            route_ip = self._route_ip_for_serial_with_retries(
                serial,
                attempts=WIRELESS_ROUTE_IP_RETRY_ATTEMPTS,
                delay_seconds=WIRELESS_ROUTE_IP_RETRY_DELAY_SECONDS,
            )
        target = ""
        if route_ip:
            target = f"{route_ip}:{port}"
            connect_message = connect_device(target, attempts=3, delay_seconds=1.0).strip()
            message = connect_message or f"已连接 {target}"
        elif preferred_target:
            target = preferred_target
            connect_message = connect_device(target, attempts=3, delay_seconds=1.0).strip()
            message = connect_message or f"已连接 {target}"
        else:
            prefix = tcpip_message or "已开启无线 ADB。"
            message = f"{prefix} 请在连接框输入手机 IP:{port} 后点连接。"
        try:
            devices = list_devices()
        except Exception as exc:
            devices = _ensure_tcp_device(self.devices, target) if target else list(self.devices)
            message = f"{message}；设备列表刷新失败：{exc}"
        return target, message, devices

    def _handle_wireless_adb_success(self, result: tuple[str, str, list[DeviceInfo]]) -> None:
        target, message, devices = result
        if target:
            self._remember_connect_target(target)
            self.connect_var.set(target)
        self._apply_devices(devices)
        if target:
            self._select_device_by_serial(target)
        self.status.last_error = message
        self._update_status()

    def _handle_wireless_adb_error(self, exc: Exception) -> None:
        raw_message = str(exc).strip() or "开启无线 ADB 失败。"
        if self._is_adb_launch_failure_message(raw_message) or self._is_local_adb_service_failure_message(raw_message):
            self._handle_refresh_devices_error(exc)
        if self._show_adb_launch_recovery_prompt(raw_message):
            return
        if self._show_local_adb_service_recovery_prompt(raw_message):
            return
        message = self._format_wireless_adb_error_message(exc)
        messagebox.showerror(WIRELESS_ADB_ERROR_TITLE, message)
        self.status.last_error = message
        self._update_status()

    def _selected_usb_device_for_tcp_connect(self) -> Optional[DeviceInfo]:
        try:
            device = self._current_device()
        except ValueError:
            return None
        if device.transport != "usb" or device.state != "device":
            return None
        return device

    def _wireless_prepare_warning_for_selected_device(self) -> Optional[tuple[str, str]]:
        try:
            device = self._current_device()
        except ValueError as exc:
            return "需要选择设备", str(exc)
        if device.transport != "usb":
            return "需要 USB 设备", "请先选择通过 USB 连接的设备。"
        if device.state != "device":
            return "设备未就绪", f"当前设备状态为 {device.state}，请先选择已就绪的 USB 设备。"
        return None

    def _connect_tcp_target(self, target: str) -> str:
        return connect_device(target, attempts=3, delay_seconds=1.0)

    def _connect_tcp_target_with_usb_fallback(
        self,
        target: str,
        selected_usb_device: Optional[DeviceInfo],
    ) -> tuple[str, str]:
        direct_error: Optional[ADBCommandError] = None
        try:
            return target, self._connect_tcp_target(target)
        except ADBCommandError as exc:
            direct_error = exc
            if self._is_adb_launch_failure_message(str(exc)):
                raise
            if self._is_local_adb_service_failure_message(str(exc)):
                raise
            if is_adb_auth_failure_message(str(exc)):
                raise
            if selected_usb_device is None:
                raise
            route_ip = self._route_ip_for_serial(selected_usb_device.serial)
            if route_ip and not self._usb_route_ip_matches_tcp_target(route_ip, target):
                return self._connect_tcp_target_with_updated_usb_ip(
                    target,
                    route_ip,
                    selected_usb_device,
                    direct_error,
                )

        port = extract_tcp_port(target, DEFAULT_TCP_PORT)
        try:
            enable_tcpip(selected_usb_device.serial, port)
            route_ip = self._route_ip_for_serial_with_retries(
                selected_usb_device.serial,
                attempts=WIRELESS_ROUTE_IP_RETRY_ATTEMPTS,
                delay_seconds=WIRELESS_ROUTE_IP_RETRY_DELAY_SECONDS,
            )
            if route_ip and not self._usb_route_ip_matches_tcp_target(route_ip, target):
                return self._retry_tcp_target_with_updated_usb_ip_after_enable(
                    target,
                    route_ip,
                    selected_usb_device,
                    direct_error,
                )
            retry_message = self._connect_tcp_target(target).strip()
        except Exception as exc:
            raise ADBCommandError(
                self._format_connect_tcp_retry_error(selected_usb_device, direct_error, exc)
            ) from exc
        retry_message = retry_message or f"已连接 {target}"
        return target, f"首次直连失败，已自动为 {selected_usb_device.serial} 开启无线 ADB；{retry_message}"

    def _connect_tcp_target_with_updated_usb_ip(
        self,
        original_target: str,
        route_ip: str,
        selected_usb_device: DeviceInfo,
        direct_error: Exception,
    ) -> tuple[str, str]:
        port = extract_tcp_port(original_target, DEFAULT_TCP_PORT)
        retry_target = f"{route_ip}:{port}"
        try:
            enable_tcpip(selected_usb_device.serial, port)
            retry_message = self._connect_tcp_target(retry_target).strip()
        except Exception as exc:
            raise ADBCommandError(
                self._format_connect_tcp_retarget_retry_error(
                    selected_usb_device,
                    original_target,
                    retry_target,
                    direct_error,
                    exc,
                )
            ) from exc
        retry_message = retry_message or f"已连接 {retry_target}"
        return (
            retry_target,
            f"首次直连 {original_target} 失败，检测到 {selected_usb_device.serial} 当前 IP 已变为 "
            f"{route_ip}，已自动改连 {retry_target}；{retry_message}",
        )

    def _retry_tcp_target_with_updated_usb_ip_after_enable(
        self,
        original_target: str,
        route_ip: str,
        selected_usb_device: DeviceInfo,
        direct_error: Exception,
    ) -> tuple[str, str]:
        port = extract_tcp_port(original_target, DEFAULT_TCP_PORT)
        retry_target = f"{route_ip}:{port}"
        try:
            retry_message = self._connect_tcp_target(retry_target).strip()
        except Exception as exc:
            raise ADBCommandError(
                self._format_connect_tcp_retarget_retry_error(
                    selected_usb_device,
                    original_target,
                    retry_target,
                    direct_error,
                    exc,
                )
            ) from exc
        retry_message = retry_message or f"已连接 {retry_target}"
        return (
            retry_target,
            f"首次直连 {original_target} 失败，检测到 {selected_usb_device.serial} 当前 IP 已变为 "
            f"{route_ip}，已自动改连 {retry_target}；{retry_message}",
        )

    def _route_ip_for_serial(self, serial: str) -> str:
        try:
            return get_device_route_ip(serial).strip()
        except Exception:
            return ""

    def _route_ip_for_serial_with_retries(
        self,
        serial: str,
        *,
        attempts: int,
        delay_seconds: float,
    ) -> str:
        max_attempts = max(1, attempts)
        for attempt in range(max_attempts):
            route_ip = self._route_ip_for_serial(serial)
            if route_ip:
                return route_ip
            if attempt + 1 < max_attempts and delay_seconds > 0:
                time.sleep(delay_seconds)
        return ""

    def _usb_route_ip_matches_tcp_target(self, route_ip: str, target: str) -> bool:
        if not route_ip:
            return True
        target_host = target.rsplit(":", 1)[0]
        return route_ip == target_host

    def _attach_usb_ip_hint_to_tcp_error(
        self,
        error: ADBCommandError,
        target: str,
        route_ip: str,
    ) -> None:
        if not route_ip:
            return
        _target_host, target_port = target.rsplit(":", 1)
        error.usb_ip_hint = f"当前选中的 USB 设备 IP 是 {route_ip}；可改连 {route_ip}:{target_port}。"

    def _format_connect_tcp_retry_error(
        self,
        selected_usb_device: DeviceInfo,
        direct_error: Exception,
        retry_error: Exception,
    ) -> str:
        direct_message = str(direct_error).strip() or "直连失败。"
        retry_message = str(retry_error).strip() or "自动开启无线 ADB 后重连失败。"
        return (
            f"{direct_message}\n\n"
            f"已尝试为当前 USB 设备 {selected_usb_device.serial} 自动开启无线 ADB 后再连接，"
            f"但仍失败：{retry_message}"
        )

    def _format_connect_tcp_retarget_retry_error(
        self,
        selected_usb_device: DeviceInfo,
        original_target: str,
        retry_target: str,
        direct_error: Exception,
        retry_error: Exception,
    ) -> str:
        direct_message = str(direct_error).strip() or "直连失败。"
        retry_message = str(retry_error).strip() or "改连当前设备 IP 后仍失败。"
        return (
            f"{direct_message}\n\n"
            f"已检测到当前 USB 设备 {selected_usb_device.serial} 的 IP 不再是 {original_target}，"
            f"程序已自动改连 {retry_target}，但仍失败：{retry_message}"
        )

    def _format_connect_tcp_error_message(self, exc: Exception) -> str:
        message = str(exc).strip() or "连接失败。"
        if self._is_adb_launch_failure_message(message):
            return message
        if self._is_local_adb_service_failure_message(message):
            return message
        if is_adb_auth_failure_message(message):
            return message
        if "已尝试为当前 USB 设备 " in message or "已检测到当前 USB 设备 " in message:
            return message
        usb_ip_hint = getattr(exc, "usb_ip_hint", "").strip()
        diagnostics = f"\n\n{usb_ip_hint}" if usb_ip_hint else ""
        return (
            f"{message}\n\n"
            "已先尝试直连目标地址。"
            "如果当前选中的是已授权的 USB 设备，程序也会自动尝试为它开启无线 ADB 后再重连；"
            f"也可以手动点“{WIRELESS_ADB_BUTTON_LABEL}”。"
            f"{diagnostics}"
        )

    def _format_wireless_adb_error_message(self, exc: Exception) -> str:
        message = str(exc).strip() or "开启无线 ADB 失败。"
        return (
            f"{message}\n\n"
            "请确认当前选择的是已授权 USB 调试的设备，并保持数据线连接稳定后再试。"
        )

    def _show_adb_launch_recovery_prompt(self, message: str) -> bool:
        if not self._is_adb_launch_failure_message(message):
            return False
        prompt = (
            f"{message}\n\n"
            f"可直接点界面里的“{ADB_PATH_BUTTON_LABEL}”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？"
        )
        should_configure = messagebox.askyesno("ADB 无法启动", prompt)
        self.status.last_error = prompt
        self._update_status()
        if should_configure:
            self.configure_adb_path()
        return True

    def _show_local_adb_service_recovery_prompt(self, message: str) -> bool:
        if not self._is_local_adb_service_failure_message(message):
            return False
        prompt = (
            f"{message}\n\n"
            f"可直接点界面里的“{RESTART_ADB_BUTTON_LABEL}”尝试恢复。\n\n"
            "是否现在重启 ADB？"
        )
        should_restart = messagebox.askyesno("ADB 服务异常", prompt)
        self.status.last_error = prompt
        self._update_status()
        if should_restart:
            self.restart_adb()
        return True

    def _is_adb_launch_failure_message(self, message: str) -> bool:
        normalized = message.strip()
        return (
            "无法启动 adb：" in normalized
            or "未找到 adb：" in normalized
            or "未找到可用 adb：" in normalized
            or "无法执行 adb，请检查权限：" in normalized
            or "adb.exe 启动后崩溃退出（0x" in normalized
        )

    def _is_local_adb_service_failure_message(self, message: str) -> bool:
        normalized = message.strip()
        return (
            "本机 ADB 服务异常" in normalized
            or is_local_adb_service_failure_message(normalized)
        )

    def _remember_connect_target(self, target: str) -> None:
        try:
            normalized_target = normalize_tcp_target(target)
        except ValueError:
            self._refresh_connect_choices()
            return
        self.recent_targets = [
            normalized_target,
            *[item for item in self.recent_targets if item != normalized_target],
        ][:MAX_RECENT_TARGETS]
        self._refresh_connect_choices()

    def _refresh_connect_choices(self) -> None:
        connect_combo = getattr(self, "connect_combo", None)
        if connect_combo is not None:
            connect_combo["values"] = tuple(self.recent_targets)

    def _current_device(self) -> DeviceInfo:
        current = self.device_var.get()
        for device in self.devices:
            if device_label(device) == current:
                return device
        raise ValueError("未选择设备。")

    def _select_device_by_serial(self, serial: str) -> bool:
        for device in self.devices:
            if device.serial == serial:
                self.device_var.set(device_label(device))
                if self.status.stream_state not in {"streaming", "reconnecting"}:
                    self.status.active_device_serial = device.serial
                return True
        return False

    def _current_filters(self) -> FilterState:
        return FilterState(
            minimum_level=self.level_var.get() or "V",
            tag_filters=normalize_tag_filters(self.tag_var.get()),
            keyword=self.keyword_var.get().strip(),
            match_only=self.match_only_var.get(),
            auto_scroll=self.auto_scroll_var.get(),
        )

    def _current_highlight_rules(self) -> list[HighlightRule]:
        return build_highlight_rules(self.highlight_var.get())

    def start_stream(self) -> None:
        if not self.status.adb_ready:
            cached_error = self.status.last_error
            if self.status.stream_state == "reconnecting":
                self._fail_retry_stream("ADB 不可用。")
            if self._show_cached_adb_recovery_prompt(cached_error):
                return
            messagebox.showwarning("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")
            return

        try:
            device = self._current_device()
        except ValueError as exc:
            messagebox.showwarning("需要选择设备", str(exc))
            if self.status.stream_state == "reconnecting":
                self._fail_retry_stream(str(exc).strip())
            return

        if device.state != "device":
            message = f"当前设备状态为 {device.state}，请先选择已就绪的设备。"
            messagebox.showwarning(
                "设备未就绪",
                message,
            )
            if self.status.stream_state == "reconnecting":
                self._fail_retry_stream(message)
            return

        self._cancel_poll_stream_callback()
        stop_error = self._stop_active_session(manual=True)
        if stop_error:
            self.status.stream_state = "failed"
            self.status.reconnect_attempt = 0
            self.reconnect_target_serial = ""
            self.status.last_error = stop_error
            messagebox.showerror("停止失败", stop_error)
            self._update_status()
            return
        retrying = self.status.stream_state == "reconnecting"
        self.manual_stop = False
        self.status.active_device_serial = device.serial
        self.reconnect_target_serial = device.serial
        self.status.stream_state = "streaming"
        self.status.last_error = ""
        if not retrying:
            self.status.reconnect_attempt = 0

        try:
            self.events = queue.Queue()
            self.status.queue_depth = 0
            self.session = LogcatSession(
                build_logcat_command(device.serial, FilterState()),
                self.events,
            )
            self.session.start()
            self._ensure_poll_stream_scheduled()
        except Exception as exc:
            self.session = None
            self.manual_stop = True
            self.status.stream_state = "failed"
            self.status.reconnect_attempt = 0
            self.reconnect_target_serial = ""
            message = str(exc)
            if self._is_adb_launch_failure_message(message) or self._is_local_adb_service_failure_message(message):
                self._handle_refresh_devices_error(exc)
                self.status.stream_state = "failed"
                self.status.reconnect_attempt = 0
                self.reconnect_target_serial = ""
            if self._show_adb_launch_recovery_prompt(message):
                self._update_status()
                return
            if self._show_local_adb_service_recovery_prompt(message):
                self._update_status()
                return
            self.status.last_error = message
            messagebox.showerror("启动失败", message)
        self._update_status()

    def stop_stream(self) -> None:
        stop_error = self._stop_active_session(manual=True)
        self._cancel_poll_stream_callback()
        if stop_error:
            self.status.stream_state = "failed"
            self.status.reconnect_attempt = 0
            self.reconnect_target_serial = ""
            self.status.last_error = stop_error
            self.status.queue_depth = 0
            self._update_status()
            return
        self._discard_pending_events()
        self.status.stream_state = "idle"
        self.status.reconnect_attempt = 0
        self.status.queue_depth = 0
        self.status.last_error = ""
        self.reconnect_target_serial = ""
        self._update_status()

    def clear_view(self) -> None:
        self.raw_lines.clear()
        self.visible_lines.clear()
        self._discard_pending_line_events()
        queue_depth = self.events.qsize()
        status_changed = self.status.queue_depth != queue_depth
        self.status.queue_depth = queue_depth
        self._render_visible()
        if status_changed:
            self._update_status()

    def clear_device_logcat(self) -> None:
        if not self.status.adb_ready:
            if self._show_cached_adb_recovery_prompt():
                return
            messagebox.showwarning("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")
            return

        try:
            device = self._current_device()
        except ValueError as exc:
            messagebox.showwarning("需要选择设备", str(exc))
            return

        self._run_background_task(
            f"正在清空 {device.serial} 的设备日志...",
            lambda: clear_logcat(device.serial),
            lambda _result: self._handle_clear_logcat_success(),
            self._handle_clear_logcat_error,
            task_key=CLEAR_LOGCAT_TASK_KEY,
        )

    def _handle_clear_logcat_success(self) -> None:
        self.status.last_error = "已清空设备 logcat。"
        self._update_status()

    def _handle_clear_logcat_error(self, exc: Exception) -> None:
        message = str(exc)
        if self._is_adb_launch_failure_message(message):
            self._handle_refresh_devices_error(exc)
            self._show_adb_launch_recovery_prompt(message)
            return
        if self._is_local_adb_service_failure_message(message):
            self._handle_refresh_devices_error(exc)
            self._show_local_adb_service_recovery_prompt(message)
            return
        messagebox.showerror("清空失败", message)
        self.status.last_error = message
        self._update_status()

    def _prepare_stream_recovery_action(self) -> None:
        stop_error = self._stop_active_session(manual=True)
        self._cancel_poll_stream_callback()
        self.session = None
        self.events = queue.Queue()
        self.status.stream_state = "idle"
        self.status.reconnect_attempt = 0
        self.reconnect_target_serial = ""
        self.status.queue_depth = 0
        self.status.last_error = stop_error or ""
        self._update_status()

    def restart_adb(self) -> None:
        self._prepare_stream_recovery_action()
        self._run_background_task(
            "正在重启 ADB...",
            self._restart_adb_and_list_devices,
            self._handle_restart_adb_success,
            self._handle_restart_adb_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _restart_adb_and_list_devices(self) -> list[DeviceInfo]:
        restart_server()
        return list_devices()

    def _handle_restart_adb_success(self, devices: list[DeviceInfo]) -> None:
        self._apply_devices(devices)
        self.status.last_error = ""
        self._update_status()

    def _handle_restart_adb_error(self, exc: Exception) -> None:
        message = str(exc)
        if self._is_adb_launch_failure_message(message):
            self._handle_refresh_devices_error(exc)
            self._show_adb_launch_recovery_prompt(message)
            return
        if self._is_local_adb_service_failure_message(message):
            self._handle_refresh_devices_error(exc)
            self._show_local_adb_service_recovery_prompt(message)
            return
        messagebox.showerror("ADB 重启失败", message)
        self._handle_refresh_devices_error(exc)
        self.status.last_error = message
        self._update_status()

    def configure_adb_path(self) -> None:
        current_path = resolve_adb_path()
        decision = messagebox.askyesnocancel(
            ADB_PATH_BUTTON_LABEL,
            (
                f"当前 adb：{current_path}\n\n"
                "选择“是”可切换 adb.exe；选择“否”恢复自动检测；"
                "选择“取消”保持不变。"
            ),
        )
        if decision is None:
            return

        selected_path: Optional[Path] = None
        success_message_prefix = "已恢复自动检测 ADB："
        if decision:
            selected = filedialog.askopenfilename(
                title="选择 adb.exe",
                initialdir=str(current_path.parent),
                filetypes=(
                    ("ADB 可执行文件", "adb.exe"),
                    ("可执行文件", "*.exe"),
                    ("所有文件", "*.*"),
                ),
            )
            if not selected:
                return
            selected_path = Path(selected)
            success_message_prefix = "已切换 ADB："

        self._prepare_stream_recovery_action()

        previous_manual_path = get_manual_adb_path()

        def action() -> tuple[str, list[DeviceInfo], Optional[Path], str]:
            set_manual_adb_path(selected_path)
            try:
                devices = list_devices()
                resolved_path = str(resolve_adb_path())
            finally:
                set_manual_adb_path(previous_manual_path)
            return f"{success_message_prefix}{resolved_path}", devices, selected_path, resolved_path

        self._run_background_task(
            "正在切换 ADB...",
            action,
            self._handle_configure_adb_path_success,
            self._handle_configure_adb_path_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _handle_configure_adb_path_success(
        self,
        result: tuple[str, list[DeviceInfo], Optional[Path], str],
    ) -> None:
        message, devices, selected_path, resolved_path = result
        set_manual_adb_path(selected_path)
        self._apply_devices(devices)
        self.status.adb_path = resolved_path
        self.status.last_error = message
        self._update_status()

    def _handle_configure_adb_path_error(self, exc: Exception) -> None:
        self.status.adb_path = str(resolve_adb_path())
        message = str(exc)
        self._handle_refresh_devices_error(exc)
        self.status.adb_path = str(resolve_adb_path())
        if self._show_adb_launch_recovery_prompt(message):
            return
        if self._show_local_adb_service_recovery_prompt(message):
            return
        messagebox.showerror("ADB 路径切换失败", message)
        self.status.last_error = message
        self._update_status()

    def save_named_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showwarning("需要预设名称", "保存前请输入预设名称。")
            return

        filters = self.filters
        highlight_rules = self.highlight_rules
        try:
            save_preset(self.presets_file, name, filters, highlight_rules)
        except Exception as exc:
            messagebox.showerror("保存预设失败", str(exc))
            return

        self.named_presets[name] = NamedPreset(
            filters=filters,
            highlight_patterns=tuple(rule.pattern for rule in highlight_rules),
        )
        self.preset_var.set(name)
        self._refresh_preset_choices()

    def load_named_preset(self) -> None:
        name = self.preset_var.get().strip()
        preset = self.named_presets.get(name)
        if preset is None:
            messagebox.showwarning("预设不存在", f"未找到名为“{name}”的预设。")
            return

        self._filter_refresh_suspended = True
        try:
            self.level_var.set(preset.filters.minimum_level)
            self.tag_var.set(", ".join(preset.filters.tag_filters))
            self.keyword_var.set(preset.filters.keyword)
            self.highlight_var.set(", ".join(preset.highlight_patterns))
            self.auto_scroll_var.set(preset.filters.auto_scroll)
            self.match_only_var.set(preset.filters.match_only)
        finally:
            self._filter_refresh_suspended = False
        self.filters = self._current_filters()
        self.highlight_rules = self._current_highlight_rules()
        self._refresh_visible_entries()

    def save_session_state(self) -> None:
        recent_target = self.connect_var.get().strip()
        self._remember_connect_target(recent_target)
        try:
            save_state(
                self.state_file,
                self.filters,
                self.highlight_rules,
                recent_target,
                self.recent_targets,
                str(get_manual_adb_path() or ""),
            )
            self.status.last_error = "会话状态已保存。"
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            self.status.last_error = str(exc)
        self._update_status()

    def export_visible(self) -> None:
        self._export_entries(list(self.visible_lines), "可见")

    def export_raw(self) -> None:
        self._export_entries(list(self.raw_lines), "原始")

    def _export_entries(self, entries: list[LogEntry], label: str) -> None:
        if not entries:
            message = f"当前没有可导出的{label}日志。"
            messagebox.showwarning("没有日志", message)
            self.status.last_error = message
            self._update_status()
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt")
        if not path:
            return
        try:
            export_lines(Path(path), [entry.raw_line for entry in entries])
            self.status.last_error = f"已导出{label}日志。"
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            self.status.last_error = str(exc)
        self._update_status()

    def _schedule_reconnect(self) -> None:
        if self.manual_stop:
            return
        if self.status.reconnect_attempt >= MAX_RECONNECT_ATTEMPTS:
            self.status.stream_state = "failed"
            self.status.reconnect_attempt = 0
            self.reconnect_target_serial = ""
            self.status.last_error = "重连次数已用尽。"
            self._update_status()
            return

        self.reconnect_target_serial = self.reconnect_target_serial or self.status.active_device_serial
        if not self.reconnect_target_serial:
            self._fail_retry_stream("缺少重连目标。")
            return
        self.status.reconnect_attempt += 1
        self.status.stream_state = "reconnecting"
        if not self.status.last_error:
            self.status.last_error = "日志流意外停止。"
        self._update_status()
        self._schedule_ui_callback(RECONNECT_DELAY_MS, self._retry_stream)

    def _retry_stream(self) -> None:
        target_serial = getattr(self, "reconnect_target_serial", "") or self.status.active_device_serial
        if self.manual_stop or self.status.stream_state != "reconnecting":
            return
        if not target_serial:
            self._fail_retry_stream("缺少重连目标。")
            return
        self._run_background_task(
            "正在重连设备...",
            list_devices,
            lambda devices, serial=target_serial: self._handle_retry_stream_refresh_success(serial, devices),
            self._handle_retry_stream_refresh_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _handle_retry_stream_refresh_success(
        self,
        target_serial: str,
        devices: list[DeviceInfo],
    ) -> None:
        if self.manual_stop or self.status.stream_state != "reconnecting":
            return
        self._apply_devices(devices)
        for device in self.devices:
            if device.serial == target_serial and device.state == "device":
                self.device_var.set(device_label(device))
                self.start_stream()
                return
        if ":" in target_serial:
            self._retry_tcp_stream_target(target_serial)
            return
        self._fail_retry_stream()

    def _handle_retry_stream_refresh_error(self, exc: Exception) -> None:
        if self.manual_stop or self.status.stream_state != "reconnecting":
            return
        message = str(exc)
        target_serial = getattr(self, "reconnect_target_serial", "") or self.status.active_device_serial
        if (
            target_serial
            and ":" in target_serial
            and not self._is_adb_launch_failure_message(message)
            and not self._is_local_adb_service_failure_message(message)
        ):
            self._retry_tcp_stream_target(target_serial)
            return
        self._handle_refresh_devices_error(exc)
        self._fail_retry_stream(message.strip())
        if self._show_adb_launch_recovery_prompt(message):
            return
        self._show_local_adb_service_recovery_prompt(message)

    def _retry_tcp_stream_target(self, target_serial: str) -> None:
        if self.manual_stop or self.status.stream_state != "reconnecting":
            return
        self._run_background_task(
            "正在重连设备...",
            lambda: self._reconnect_tcp_stream_target(target_serial),
            lambda devices, serial=target_serial: self._handle_retry_stream_refresh_success(serial, devices),
            self._handle_retry_tcp_stream_error,
            task_key=DEVICE_SYNC_TASK_KEY,
        )

    def _reconnect_tcp_stream_target(self, target_serial: str) -> list[DeviceInfo]:
        existing_devices = list(self.devices)
        connect_device(target_serial, attempts=2, delay_seconds=1.0)
        try:
            return list_devices()
        except Exception:
            return _ensure_tcp_device(existing_devices, target_serial)

    def _handle_retry_tcp_stream_error(self, exc: Exception) -> None:
        if self.manual_stop or self.status.stream_state != "reconnecting":
            return
        message = str(exc).strip()
        if self._is_adb_launch_failure_message(message):
            self._handle_refresh_devices_error(exc)
            self._fail_retry_stream(message)
            self._show_adb_launch_recovery_prompt(message)
            return
        if self._is_local_adb_service_failure_message(message):
            self._handle_refresh_devices_error(exc)
            self._fail_retry_stream(message)
            self._show_local_adb_service_recovery_prompt(message)
            return
        if not message:
            message = "TCP 重连失败。"
        self._fail_retry_stream(message)

    def _fail_retry_stream(self, refresh_error: str = "") -> None:
        self.status.stream_state = "failed"
        self.status.reconnect_attempt = 0
        self.reconnect_target_serial = ""
        if refresh_error:
            self.status.last_error = f"重连设备不可用：{refresh_error}"
        else:
            self.status.last_error = "重连设备不可用。"
        self._update_status()

    def _handle_stream_runtime_failure(self, message: str) -> bool:
        if not message:
            return False
        if message.startswith("logcat 进程异常退出，代码："):
            self.status.stream_state = "failed"
            self.status.reconnect_attempt = 0
            self.reconnect_target_serial = ""
            self.status.last_error = message
            self._update_status()
            return True
        if not (
            self._is_adb_launch_failure_message(message)
            or self._is_local_adb_service_failure_message(message)
        ):
            return False
        self._handle_refresh_devices_error(RuntimeError(message))
        self.status.stream_state = "failed"
        self.status.reconnect_attempt = 0
        self.reconnect_target_serial = ""
        if self._show_adb_launch_recovery_prompt(message):
            return True
        self._show_local_adb_service_recovery_prompt(message)
        return True

    def _poll_stream(self) -> None:
        self._poll_stream_callback_id = None
        updated = False
        status_dirty = False
        new_visible_entries: list[LogEntry] = []
        filters_snapshot: Optional[FilterState] = None
        prepared_filters_snapshot: Optional[PreparedFilterState] = None
        highlight_rules_snapshot: Optional[list[HighlightRule]] = None
        highlight_rules_cache_key_snapshot: Optional[tuple[tuple[str, str, bool], ...]] = None
        processed = 0
        previously_rendered_visible_count = len(self.visible_lines)

        while processed < MAX_EVENTS_PER_TICK:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if event.kind == "line" and event.entry is not None:
                if self.manual_stop or self.status.stream_state != "streaming":
                    continue
                updated = True
                if self.status.reconnect_attempt:
                    self.status.reconnect_attempt = 0
                    self.status.last_error = ""
                    status_dirty = True
                if filters_snapshot is None:
                    filters_snapshot = self.filters
                    prepared_filters_snapshot = prepare_filter_state(filters_snapshot)
                    highlight_rules_snapshot = self.highlight_rules
                    highlight_rules_cache_key_snapshot = build_highlight_rule_cache_key(
                        highlight_rules_snapshot
                    )
                visible_entry = self._append_entry(
                    event.entry,
                    filters_snapshot,
                    highlight_rules_snapshot,
                    prepared_filters_snapshot,
                    highlight_rules_cache_key_snapshot,
                )
                if visible_entry is not None:
                    new_visible_entries.append(visible_entry)
            elif event.kind == "stderr":
                if self.manual_stop or self.status.stream_state not in {"streaming", "reconnecting"}:
                    continue
                if self._handle_stream_runtime_failure(event.message):
                    status_dirty = False
                    continue
                if self.status.last_error != event.message:
                    self.status.last_error = event.message
                    status_dirty = True
            elif event.kind == "stopped":
                self.session = None
                if self.status.stream_state == "streaming":
                    self._schedule_reconnect()

        if updated:
            self._append_visible_entries(new_visible_entries, previously_rendered_visible_count)

        queue_depth = self.events.qsize()
        status_changed = status_dirty or self.status.queue_depth != queue_depth
        self.status.queue_depth = queue_depth
        if status_changed:
            self._update_status()
        if self.session is not None or self.status.stream_state == "streaming" or self.status.queue_depth:
            delay = 0 if self.status.queue_depth else QUEUE_DRAIN_MS
            self._ensure_poll_stream_scheduled(delay)

    def _append_entry(
        self,
        entry: LogEntry,
        filters: Optional[FilterState] = None,
        rules: Optional[list[HighlightRule]] = None,
        prepared_filters: Optional[PreparedFilterState] = None,
        rules_cache_key: Optional[tuple[tuple[str, str, bool], ...]] = None,
    ) -> Optional[LogEntry]:
        self.raw_lines.append(entry)
        if filters is None:
            filters = self.filters
        if prepared_filters is None:
            prepared_filters = prepare_filter_state(filters)
        if rules is None:
            rules = self.highlight_rules
        if rules_cache_key is None:
            rules_cache_key = build_highlight_rule_cache_key(rules)
        entry.matches_filters = entry_matches_prepared(entry, prepared_filters)
        if entry.matches_filters or not filters.match_only:
            entry.highlight_keys = match_highlight_rules(
                entry,
                rules,
                rule_cache_key=rules_cache_key,
            )
            self.visible_lines.append(entry)
            return entry
        entry.highlight_keys = ()
        return None

    def _refresh_visible_entries(self) -> None:
        self._invalidate_pending_filter_refreshes()
        filters = self.filters
        prepared_filters = prepare_filter_state(filters)
        rules = self.highlight_rules
        rules_cache_key = build_highlight_rule_cache_key(rules)
        self.visible_lines.clear()
        for entry in self.raw_lines:
            entry.matches_filters = entry_matches_prepared(entry, prepared_filters)
            if entry.matches_filters or not filters.match_only:
                entry.highlight_keys = match_highlight_rules(
                    entry,
                    rules,
                    rule_cache_key=rules_cache_key,
                )
                self.visible_lines.append(entry)
            else:
                entry.highlight_keys = ()
        self._render_visible()

    def _refresh_highlight_entries(self) -> None:
        self._invalidate_pending_filter_refreshes()
        rules = self.highlight_rules
        rules_cache_key = build_highlight_rule_cache_key(rules)
        for entry in self.visible_lines:
            entry.highlight_keys = match_highlight_rules(
                entry,
                rules,
                rule_cache_key=rules_cache_key,
            )
        self._render_visible()

    def _render_visible(self) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        if any(entry.highlight_keys for entry in self.visible_lines):
            rule_map = {rule.name: rule for rule in self.highlight_rules}
            tag_map = self._highlight_tag_map(rule_map)
            self._configure_highlight_tags(rule_map, tag_map, self.visible_lines)
        else:
            tag_map = {}

        for entry in self.visible_lines:
            self._insert_visible_entry(entry, tag_map)

        self.text.configure(state=tk.DISABLED)
        self._update_summary()
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def _append_visible_entries(
        self,
        entries: list[LogEntry],
        previously_rendered_count: Optional[int] = None,
    ) -> None:
        trim_count = 0
        if previously_rendered_count is not None:
            overflow = max(0, previously_rendered_count + len(entries) - len(self.visible_lines))
            trim_count = min(overflow, previously_rendered_count)
            skip_count = overflow - trim_count
            if skip_count:
                entries = entries[skip_count:]

        if trim_count or entries:
            self.text.configure(state=tk.NORMAL)
            if trim_count:
                self.text.delete("1.0", f"{trim_count + 1}.0")
            if any(entry.highlight_keys for entry in entries):
                rule_map = {rule.name: rule for rule in self.highlight_rules}
                tag_map = self._highlight_tag_map(rule_map)
                self._configure_highlight_tags(rule_map, tag_map, entries)
            else:
                tag_map = {}
            for entry in entries:
                self._insert_visible_entry(entry, tag_map)
            self.text.configure(state=tk.DISABLED)
            if self.auto_scroll_var.get():
                self.text.see(tk.END)
        self._update_summary()

    def _build_highlight_tag_map(
        self,
        rule_map: dict[str, HighlightRule],
    ) -> dict[str, str]:
        return {rule_name: build_highlight_text_tag(rule_name) for rule_name in rule_map}

    def _highlight_tag_map(
        self,
        rule_map: dict[str, HighlightRule],
    ) -> dict[str, str]:
        cache_key = tuple(rule_map)
        if getattr(self, "_highlight_tag_map_cache_key", ()) != cache_key:
            self._highlight_tag_map_cache_key = cache_key
            self._highlight_tag_map_cache = self._build_highlight_tag_map(rule_map)
        return self._highlight_tag_map_cache

    def _configure_highlight_tags(
        self,
        rule_map: dict[str, HighlightRule],
        tag_map: dict[str, str],
        entries: Iterable[LogEntry],
    ) -> None:
        used_rule_names = {
            rule_name
            for entry in entries
            for rule_name in entry.highlight_keys
            if rule_name in rule_map
        }
        for rule_name in sorted(used_rule_names):
            rule = rule_map[rule_name]
            tag_name = tag_map[rule_name]
            style = (rule.foreground, rule.background or "")
            if self._configured_highlight_styles.get(tag_name) == style:
                continue
            self.text.tag_config(
                tag_name,
                foreground=style[0],
                background=style[1],
            )
            self._configured_highlight_styles[tag_name] = style

    def _insert_visible_entry(self, entry: LogEntry, tag_map: dict[str, str]) -> None:
        highlight_names = (
            tuple(rule_name for rule_name in entry.highlight_keys if rule_name in tag_map)
            if entry.highlight_keys
            else ()
        )
        add_filtered_out_tag = not entry.matches_filters and not self.filters.match_only
        if not add_filtered_out_tag and not highlight_names:
            self.text.insert(tk.END, entry.raw_line + "\n", entry.level)
            return

        line_start = self.text.index(tk.END)
        self.text.insert(tk.END, entry.raw_line + "\n", entry.level)
        line_end = self.text.index(tk.END)
        if add_filtered_out_tag:
            self.text.tag_add("filtered-out", line_start, line_end)

        for rule_name in highlight_names:
            tag_name = tag_map[rule_name]
            self.text.tag_add(tag_name, line_start, line_end)

    def _sync_selected_device(self, *, update_status: bool = True) -> None:
        try:
            self.status.active_device_serial = self._current_device().serial
        except ValueError:
            if self.status.stream_state == "idle":
                self.status.active_device_serial = ""
        if update_status:
            self._update_status()

    def _stop_active_session(self, manual: bool) -> Optional[str]:
        self.manual_stop = manual
        current_session = self.session
        if current_session is None:
            return None

        failures: list[str] = []
        try:
            current_session.stop()
        except Exception as exc:
            failures.append(str(exc))
        else:
            try:
                current_session.join()
            except Exception as exc:
                failures.append(str(exc))

        if failures:
            return "; ".join(failures)
        if self.session is current_session:
            self.session = None
            self.events = queue.Queue()
        return None

    def _discard_pending_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    def _discard_pending_line_events(self) -> None:
        retained_events: list[StreamEvent] = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.kind != "line":
                retained_events.append(event)
        for event in retained_events:
            self.events.put(event)

    def _update_status(self) -> None:
        status_text = format_status_text(self.status)
        if self.status_var.get() != status_text:
            self.status_var.set(status_text)
        self._update_summary()

    def _update_summary(self) -> None:
        summary_text = build_summary_text(
            len(self.raw_lines),
            len(self.visible_lines),
            self.status.stream_state,
        )
        if self.summary_var.get() != summary_text:
            self.summary_var.set(summary_text)

    def _on_close(self) -> None:
        self._ui_closing = True
        self._cancel_poll_stream_callback()
        self._invalidate_pending_filter_refreshes()
        self.save_session_state()
        self._stop_active_session(manual=True)
        self.root.destroy()


def main() -> int:
    if TK_IMPORT_ERROR is not None:
        raise RuntimeError("当前 Python 环境不支持 Tkinter。") from TK_IMPORT_ERROR
    root = tk.Tk()
    LogcatToolGUI(root)
    root.mainloop()
    return 0
