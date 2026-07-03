from __future__ import annotations

from collections import deque
import queue
import threading
from pathlib import Path
from typing import Callable, Optional, TypeVar, Union

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
    DEFAULT_TCP_PORT,
    build_logcat_command,
    clear_logcat,
    connect_device,
    enable_tcpip,
    extract_tcp_port,
    get_device_route_ip,
    list_devices,
    restart_server,
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
from logcat_tool_for_win.filters import entry_matches, normalize_tag_filters
from logcat_tool_for_win.highlight import DEFAULT_LEVEL_COLORS, match_highlight_rules
from logcat_tool_for_win.log_stream import LogcatSession
from logcat_tool_for_win.models import (
    AppStatus,
    DeviceInfo,
    FilterState,
    HighlightRule,
    LogEntry,
    StreamEvent,
)
from logcat_tool_for_win.presets import load_presets, load_state, save_preset, save_state

MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_MS = 2_000
MAX_EVENTS_PER_TICK = 500
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
    for item in raw.split(","):
        pattern = item.strip()
        if pattern:
            rules.append(HighlightRule(name=pattern, pattern=pattern, foreground=WARN))
    return rules


def build_highlight_text_tag(rule_name: str) -> str:
    return f"{HIGHLIGHT_TAG_PREFIX}{rule_name}"


def format_status_text(status: AppStatus) -> str:
    base = (
        f"ADB：{'就绪' if status.adb_ready else '不可用'} | "
        f"设备：{status.active_device_serial or '-'} | "
        f"状态：{format_stream_state(status.stream_state)} | "
        f"队列：{status.queue_depth}"
    )
    if status.reconnect_attempt:
        base += f" | 第 {status.reconnect_attempt} 次重连"
    if status.last_error:
        base += f" | {status.last_error}"
    return base


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
        self.filters, self.highlight_rules, recent_target = load_state(self.state_file)
        self.named_presets = load_presets(self.presets_file)
        self.status = AppStatus()
        self.manual_stop = True
        self.reconnect_target_serial = ""
        self._filter_trace_ids: list[tuple[tk.Variable, str]] = []

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
        self.root.after(QUEUE_DRAIN_MS, self._poll_stream)

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
        self.connect_entry = ttk.Entry(
            toolbar,
            textvariable=self.connect_var,
            width=18,
            style="App.TEntry",
        )
        self.connect_entry.pack(side=tk.LEFT, padx=8)
        ttk.Button(toolbar, text="连接", style="App.TButton", command=self.connect_tcp).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            toolbar,
            text="开启无线",
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
            self.highlight_var,
            self.auto_scroll_var,
            self.match_only_var,
        ):
            trace_id = variable.trace_add("write", self._handle_filter_trace)
            self._filter_trace_ids.append((variable, trace_id))

    def _handle_filter_trace(self, *_args: object) -> None:
        self._refresh_visible_entries()

    def _run_background_task(
        self,
        pending_message: str,
        action: Callable[[], T],
        on_success: Callable[[T], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.status.last_error = pending_message
        self._update_status()

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self.root.after(0, lambda error=exc: on_error(error))
            else:
                self.root.after(0, lambda value=result: on_success(value))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_preset_choices(self) -> None:
        names = sorted(self.named_presets)
        self.preset_combo["values"] = names
        if not self.preset_var.get() and names:
            self.preset_var.set(names[0])

    def focus_keyword(self) -> None:
        self.keyword_entry.focus_set()

    def _apply_devices(self, devices: list[DeviceInfo]) -> None:
        current_label = self.device_var.get()
        preserve_stream_target = self.status.stream_state in {"streaming", "reconnecting"}
        self.devices = devices
        labels = [device_label(device) for device in self.devices]
        self.device_combo["values"] = labels
        if current_label in labels:
            self.device_var.set(current_label)
        elif labels:
            self.device_var.set(labels[0])
        else:
            self.device_var.set("")
        self.status.adb_ready = True
        self.status.last_error = ""
        if not preserve_stream_target:
            self._sync_selected_device()
        self._update_status()

    def _handle_refresh_devices_error(self, exc: Exception) -> None:
        preserve_stream_target = self.status.stream_state in {"streaming", "reconnecting"}
        self.devices = []
        self.device_combo["values"] = ()
        self.device_var.set("")
        self.status.adb_ready = False
        self.status.last_error = str(exc)
        if not preserve_stream_target:
            self.status.active_device_serial = ""
        self._update_status()

    def refresh_devices(self) -> None:
        try:
            devices = list_devices()
        except Exception as exc:
            self._handle_refresh_devices_error(exc)
        else:
            self._apply_devices(devices)

    def refresh_devices_async(self) -> None:
        self._run_background_task(
            "正在刷新设备...",
            list_devices,
            self._apply_devices,
            self._handle_refresh_devices_error,
        )

    def connect_tcp(self) -> None:
        target = self.connect_var.get().strip()
        if not target:
            messagebox.showwarning("需要目标地址", "请输入 IP:端口 格式的 TCP 目标。")
            return

        def action() -> tuple[str, list[DeviceInfo]]:
            return connect_device(target).strip(), list_devices()

        self._run_background_task(
            f"正在连接 {target}...",
            action,
            self._handle_connect_tcp_success,
            self._handle_connect_tcp_error,
        )

    def _handle_connect_tcp_success(self, result: tuple[str, list[DeviceInfo]]) -> None:
        message, devices = result
        self._apply_devices(devices)
        self.status.last_error = message
        self._update_status()

    def _handle_connect_tcp_error(self, exc: Exception) -> None:
        messagebox.showerror("连接失败", str(exc))
        self.status.last_error = str(exc)
        self._update_status()

    def enable_wireless_adb(self) -> None:
        try:
            device = self._current_device()
        except ValueError as exc:
            messagebox.showwarning("需要选择设备", str(exc))
            return

        if device.state != "device":
            messagebox.showwarning(
                "设备未就绪",
                f"当前设备状态为 {device.state}，请先选择已就绪的 USB 设备。",
            )
            return
        if device.transport != "usb":
            messagebox.showwarning("需要 USB 设备", "请先选择通过 USB 连接的设备。")
            return

        try:
            port = extract_tcp_port(self.connect_var.get().strip(), DEFAULT_TCP_PORT)
        except Exception as exc:
            messagebox.showwarning("TCP 端口无效", str(exc))
            return

        self._run_background_task(
            f"正在为 {device.serial} 开启无线 ADB...",
            lambda: self._prepare_wireless_adb(device.serial, port),
            self._handle_wireless_adb_success,
            self._handle_wireless_adb_error,
        )

    def _prepare_wireless_adb(self, serial: str, port: int) -> tuple[str, str, list[DeviceInfo]]:
        route_ip = ""
        try:
            route_ip = get_device_route_ip(serial)
        except Exception:
            route_ip = ""

        tcpip_message = enable_tcpip(serial, port).strip()
        target = ""
        if route_ip:
            target = f"{route_ip}:{port}"
            connect_message = connect_device(target, attempts=3, delay_seconds=1.0).strip()
            message = connect_message or f"已连接 {target}"
        else:
            prefix = tcpip_message or "已开启无线 ADB。"
            message = f"{prefix} 请在连接框输入手机 IP:{port} 后点连接。"
        return target, message, list_devices()

    def _handle_wireless_adb_success(self, result: tuple[str, str, list[DeviceInfo]]) -> None:
        target, message, devices = result
        if target:
            self.connect_var.set(target)
        self._apply_devices(devices)
        self.status.last_error = message
        self._update_status()

    def _handle_wireless_adb_error(self, exc: Exception) -> None:
        messagebox.showerror("开启无线失败", str(exc))
        self.status.last_error = str(exc)
        self._update_status()

    def _current_device(self) -> DeviceInfo:
        current = self.device_var.get()
        for device in self.devices:
            if device_label(device) == current:
                return device
        raise ValueError("未选择设备。")

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
        try:
            device = self._current_device()
        except ValueError as exc:
            messagebox.showwarning("需要选择设备", str(exc))
            return

        if device.state != "device":
            messagebox.showwarning(
                "设备未就绪",
                f"当前设备状态为 {device.state}，请先选择已就绪的设备。",
            )
            return

        stop_error = self._stop_active_session(manual=True)
        if stop_error:
            self.status.stream_state = "failed"
            self.status.last_error = stop_error
            messagebox.showerror("停止失败", stop_error)
            self._update_status()
            return
        self.filters = self._current_filters()
        self.highlight_rules = self._current_highlight_rules()
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
            self.session = LogcatSession(
                build_logcat_command(device.serial, FilterState()),
                self.events,
            )
            self.session.start()
        except Exception as exc:
            self.session = None
            self.manual_stop = True
            self.status.stream_state = "failed"
            self.status.last_error = str(exc)
            messagebox.showerror("启动失败", str(exc))
        self._update_status()

    def stop_stream(self) -> None:
        stop_error = self._stop_active_session(manual=True)
        if stop_error:
            self.status.stream_state = "failed"
            self.status.last_error = stop_error
            self.status.queue_depth = 0
            self._update_status()
            return
        self.status.stream_state = "idle"
        self.status.reconnect_attempt = 0
        self.status.queue_depth = 0
        self.status.last_error = ""
        self.reconnect_target_serial = ""
        self._update_status()

    def clear_view(self) -> None:
        self.raw_lines.clear()
        self.visible_lines.clear()
        self._render_visible()

    def clear_device_logcat(self) -> None:
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
        )

    def _handle_clear_logcat_success(self) -> None:
        self.status.last_error = "已清空设备 logcat。"
        self._update_status()

    def _handle_clear_logcat_error(self, exc: Exception) -> None:
        messagebox.showerror("清空失败", str(exc))
        self.status.last_error = str(exc)
        self._update_status()

    def restart_adb(self) -> None:
        self.stop_stream()
        self._run_background_task(
            "正在重启 ADB...",
            self._restart_adb_and_list_devices,
            self._handle_restart_adb_success,
            self._handle_restart_adb_error,
        )

    def _restart_adb_and_list_devices(self) -> list[DeviceInfo]:
        restart_server()
        return list_devices()

    def _handle_restart_adb_success(self, devices: list[DeviceInfo]) -> None:
        self._apply_devices(devices)
        self.status.last_error = ""
        self._update_status()

    def _handle_restart_adb_error(self, exc: Exception) -> None:
        messagebox.showerror("ADB 重启失败", str(exc))
        self.status.last_error = str(exc)
        self._update_status()

    def save_named_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showwarning("需要预设名称", "保存前请输入预设名称。")
            return

        filters = self._current_filters()
        try:
            save_preset(self.presets_file, name, filters)
        except Exception as exc:
            messagebox.showerror("保存预设失败", str(exc))
            return

        self.named_presets[name] = filters
        self.preset_var.set(name)
        self._refresh_preset_choices()

    def load_named_preset(self) -> None:
        name = self.preset_var.get().strip()
        preset = self.named_presets.get(name)
        if preset is None:
            messagebox.showwarning("预设不存在", f"未找到名为“{name}”的预设。")
            return

        self.level_var.set(preset.minimum_level)
        self.tag_var.set(", ".join(preset.tag_filters))
        self.keyword_var.set(preset.keyword)
        self.auto_scroll_var.set(preset.auto_scroll)
        self.match_only_var.set(preset.match_only)

    def save_session_state(self) -> None:
        self.filters = self._current_filters()
        self.highlight_rules = self._current_highlight_rules()
        try:
            save_state(
                self.state_file,
                self.filters,
                self.highlight_rules,
                self.connect_var.get().strip(),
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
            self.status.last_error = "重连次数已用尽。"
            self._update_status()
            return

        self.reconnect_target_serial = self.reconnect_target_serial or self.status.active_device_serial
        self.status.reconnect_attempt += 1
        self.status.stream_state = "reconnecting"
        if not self.status.last_error:
            self.status.last_error = "日志流意外停止。"
        self._update_status()
        self.root.after(RECONNECT_DELAY_MS, self._retry_stream)

    def _retry_stream(self) -> None:
        target_serial = getattr(self, "reconnect_target_serial", "") or self.status.active_device_serial
        if self.manual_stop or not target_serial:
            return

        self.refresh_devices()
        for device in self.devices:
            if device.serial == target_serial and device.state == "device":
                self.device_var.set(device_label(device))
                self.start_stream()
                return

        self.status.stream_state = "failed"
        self.status.last_error = "重连设备不可用。"
        self._update_status()

    def _poll_stream(self) -> None:
        updated = False
        full_render_required = False
        new_visible_entries: list[LogEntry] = []
        processed = 0

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
                visible_entry, entry_full_render_required = self._append_entry(event.entry)
                if visible_entry is not None:
                    new_visible_entries.append(visible_entry)
                full_render_required = full_render_required or entry_full_render_required
            elif event.kind == "stderr":
                if self.manual_stop or self.status.stream_state not in {"streaming", "reconnecting"}:
                    continue
                self.status.last_error = event.message
            elif event.kind == "stopped":
                self.session = None
                if self.status.stream_state == "streaming":
                    self._schedule_reconnect()

        if updated:
            if full_render_required:
                self._render_visible()
            else:
                self._append_visible_entries(new_visible_entries)

        self.status.queue_depth = self.events.qsize()
        self._update_status()
        delay = 0 if self.status.queue_depth else QUEUE_DRAIN_MS
        self.root.after(delay, self._poll_stream)

    def _append_entry(self, entry: LogEntry) -> tuple[Optional[LogEntry], bool]:
        self.raw_lines.append(entry)
        filters = self._current_filters()
        rules = self._current_highlight_rules()
        self.filters = filters
        self.highlight_rules = rules
        entry.matches_filters = entry_matches(entry, filters)
        entry.highlight_keys = match_highlight_rules(entry, rules)
        if entry.matches_filters or not filters.match_only:
            full_render_required = (
                self.visible_lines.maxlen is not None
                and len(self.visible_lines) >= self.visible_lines.maxlen
            )
            self.visible_lines.append(entry)
            return entry, full_render_required
        return None, False

    def _refresh_visible_entries(self) -> None:
        filters = self._current_filters()
        rules = self._current_highlight_rules()
        self.filters = filters
        self.highlight_rules = rules
        self.visible_lines.clear()
        for entry in self.raw_lines:
            entry.matches_filters = entry_matches(entry, filters)
            entry.highlight_keys = match_highlight_rules(entry, rules)
            if entry.matches_filters or not filters.match_only:
                self.visible_lines.append(entry)
        self._render_visible()

    def _render_visible(self) -> None:
        rule_map = {rule.name: rule for rule in self.highlight_rules}
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)

        for entry in self.visible_lines:
            self._insert_visible_entry(entry, rule_map)

        self.text.configure(state=tk.DISABLED)
        self._update_summary()
        if self.auto_scroll_var.get():
            self.text.see(tk.END)

    def _append_visible_entries(self, entries: list[LogEntry]) -> None:
        if entries:
            rule_map = {rule.name: rule for rule in self.highlight_rules}
            self.text.configure(state=tk.NORMAL)
            for entry in entries:
                self._insert_visible_entry(entry, rule_map)
            self.text.configure(state=tk.DISABLED)
            if self.auto_scroll_var.get():
                self.text.see(tk.END)
        self._update_summary()

    def _insert_visible_entry(self, entry: LogEntry, rule_map: dict[str, HighlightRule]) -> None:
        line_start = self.text.index(tk.END)
        self.text.insert(tk.END, entry.raw_line + "\n", entry.level)
        line_end = self.text.index(tk.END)

        if not entry.matches_filters and not self.filters.match_only:
            self.text.tag_add("filtered-out", line_start, line_end)

        for rule_name in entry.highlight_keys:
            rule = rule_map.get(rule_name)
            if rule is None:
                continue
            tag_name = build_highlight_text_tag(rule_name)
            self.text.tag_config(
                tag_name,
                foreground=rule.foreground,
                background=rule.background or "",
            )
            self.text.tag_add(tag_name, line_start, line_end)

    def _sync_selected_device(self) -> None:
        try:
            self.status.active_device_serial = self._current_device().serial
        except ValueError:
            if self.status.stream_state == "idle":
                self.status.active_device_serial = ""
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

    def _update_status(self) -> None:
        self.status_var.set(format_status_text(self.status))
        self._update_summary()

    def _update_summary(self) -> None:
        self.summary_var.set(
            build_summary_text(len(self.raw_lines), len(self.visible_lines), self.status.stream_state)
        )

    def _on_close(self) -> None:
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
