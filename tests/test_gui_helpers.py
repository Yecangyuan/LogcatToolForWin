from __future__ import annotations

from collections import deque
import queue
from types import SimpleNamespace

import logcat_tool_for_win.gui as gui
from logcat_tool_for_win.models import (
    AppStatus,
    DeviceInfo,
    FilterState,
    HighlightRule,
    LogEntry,
    StreamEvent,
)

DUMMY_TK = SimpleNamespace(NORMAL="normal", DISABLED="disabled", END="end")


def test_build_summary_text_reports_total_and_visible_counts() -> None:
    assert gui.build_summary_text(120, 24, "streaming") == "总行数：120 | 可见：24 | 状态：采集中"


def test_format_status_text_includes_reconnect_attempt() -> None:
    status = AppStatus(
        adb_ready=True,
        active_device_serial="R58M12345",
        stream_state="reconnecting",
        queue_depth=9,
        last_error="device offline",
        reconnect_attempt=2,
    )

    text = gui.format_status_text(status)

    assert "R58M12345" in text
    assert "第 2 次重连" in text


def test_build_highlight_rules_creates_rules_from_csv_text() -> None:
    rules = gui.build_highlight_rules("ANR, crash , ")

    assert [rule.name for rule in rules] == ["ANR", "crash"]


class DummyVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class TriggeringVar(DummyVar):
    def __init__(self, value: object, on_set) -> None:
        super().__init__(value)
        self.on_set = on_set

    def set(self, value: object) -> None:
        super().set(value)
        self.on_set()


class LowerCountingStr(str):
    def __new__(cls, value: str) -> "LowerCountingStr":
        instance = super().__new__(cls, value)
        instance.lower_calls = 0
        return instance

    def lower(self) -> str:
        self.lower_calls += 1
        return super().lower()


class DummyCombo:
    def __init__(self) -> None:
        self.values: tuple[str, ...] = ()

    def __setitem__(self, key: str, value: object) -> None:
        assert key == "values"
        self.values = tuple(value)


class DummyRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay: int, callback: object) -> None:
        self.after_calls.append((delay, callback))


class DummyText:
    def __init__(self) -> None:
        self.configure_calls: list[dict[str, object]] = []
        self.delete_calls: list[tuple[object, object]] = []
        self.insert_calls: list[tuple[object, str, object]] = []
        self.tag_add_calls: list[tuple[str, object, object]] = []
        self.tag_config_calls: list[tuple[str, dict[str, object]]] = []
        self.see_calls: list[object] = []
        self.index_calls: list[object] = []
        self.next_line = 1

    def configure(self, **kwargs: object) -> None:
        self.configure_calls.append(kwargs)

    def delete(self, start: object, end: object) -> None:
        self.delete_calls.append((start, end))
        self.next_line = 1

    def index(self, _index: object) -> str:
        self.index_calls.append(_index)
        return f"{self.next_line}.0"

    def insert(self, index: object, text: str, tag: object) -> None:
        self.insert_calls.append((index, text, tag))
        self.next_line += text.count("\n")

    def tag_add(self, tag: str, start: object, end: object) -> None:
        self.tag_add_calls.append((tag, start, end))

    def tag_config(self, tag: str, **kwargs: object) -> None:
        self.tag_config_calls.append((tag, kwargs))

    def see(self, index: object) -> None:
        self.see_calls.append(index)


class FailingSession:
    def stop(self) -> None:
        raise RuntimeError("stop failed")

    def join(self) -> None:
        return None


class ImmediateThread:
    def __init__(self, target, daemon: bool) -> None:
        self.target = target
        self.daemon = daemon

    def start(self) -> None:
        self.target()


def make_device(serial: str, state: str = "device", transport: str | None = None) -> DeviceInfo:
    return DeviceInfo(
        serial=serial,
        display_name=serial,
        transport=transport or ("tcp" if ":" in serial else "usb"),
        state=state,
        model="Pixel",
        product="pixel",
        raw_descriptor=serial,
    )


def make_modeled_device(serial: str, model: str, transport: str = "usb") -> DeviceInfo:
    return DeviceInfo(
        serial=serial,
        display_name=model,
        transport=transport,
        state="device",
        model=model,
        product="pixel",
        raw_descriptor=serial,
    )


def make_entry(message: str = "ANR detected") -> LogEntry:
    return LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="ActivityManager",
        message=message,
        raw_line=f"06-18 10:00:00.000 E ActivityManager: {message}",
    )


def make_controller() -> gui.LogcatToolGUI:
    if gui.tk is None:
        gui.tk = DUMMY_TK
    controller = gui.LogcatToolGUI.__new__(gui.LogcatToolGUI)
    controller.status = AppStatus()
    controller.manual_stop = False
    controller._filter_refresh_suspended = False
    controller.events = queue.Queue()
    controller.root = DummyRoot()
    controller.status_var = DummyVar("")
    controller.summary_var = DummyVar("")
    controller.device_var = DummyVar("")
    controller.connect_var = DummyVar("")
    controller.level_var = DummyVar("V")
    controller.tag_var = DummyVar("")
    controller.keyword_var = DummyVar("")
    controller.highlight_var = DummyVar("")
    controller.device_combo = DummyCombo()
    controller.preset_var = DummyVar("")
    controller.named_presets = {}
    controller.raw_lines = deque()
    controller.visible_lines = deque()
    controller.filters = FilterState()
    controller.highlight_rules = []
    controller.auto_scroll_var = DummyVar(False)
    controller.match_only_var = DummyVar(False)
    controller.text = DummyText()
    controller.session = None
    controller.devices = []
    return controller


def test_run_background_task_schedules_result_on_tk_thread(monkeypatch) -> None:
    controller = make_controller()
    successes: list[str] = []
    errors: list[Exception] = []

    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)

    gui.LogcatToolGUI._run_background_task(
        controller,
        "正在执行...",
        lambda: "ok",
        successes.append,
        errors.append,
    )

    assert controller.status.last_error == "正在执行..."
    assert successes == []
    assert errors == []
    assert len(controller.root.after_calls) == 1

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert successes == ["ok"]
    assert errors == []


def test_poll_stream_ignores_late_line_events_after_stop() -> None:
    controller = make_controller()
    controller.status.stream_state = "idle"
    controller.manual_stop = True
    controller.events.put(StreamEvent(kind="line", entry=make_entry()))
    appended: list[LogEntry] = []
    controller._append_entry = lambda entry: appended.append(entry)
    controller._render_visible = lambda: None

    gui.LogcatToolGUI._poll_stream(controller)

    assert appended == []
    assert controller.status.queue_depth == 0
    assert controller.root.after_calls[0][0] == gui.QUEUE_DRAIN_MS


def test_poll_stream_skips_status_update_when_idle_queue_is_unchanged() -> None:
    controller = make_controller()
    controller.status.queue_depth = 0
    controller.status_var.set("stable status")
    controller.summary_var.set("stable summary")

    gui.LogcatToolGUI._poll_stream(controller)

    assert controller.status_var.get() == "stable status"
    assert controller.summary_var.get() == "stable summary"
    assert controller.root.after_calls[0][0] == gui.QUEUE_DRAIN_MS


def test_poll_stream_appends_new_visible_lines_without_full_redraw() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    controller.events.put(StreamEvent(kind="line", entry=make_entry("first")))
    full_renders: list[object] = []
    controller._render_visible = lambda: full_renders.append(True)

    gui.LogcatToolGUI._poll_stream(controller)

    text = controller.text
    assert isinstance(text, DummyText)
    assert full_renders == []
    assert text.delete_calls == []
    assert text.insert_calls == [
        (gui.tk.END, "06-18 10:00:00.000 E ActivityManager: first\n", "E")
    ]
    assert controller.summary_var.get() == "总行数：1 | 可见：1 | 状态：采集中"
    assert controller.root.after_calls[0][0] == gui.QUEUE_DRAIN_MS


def test_append_visible_entries_configures_each_highlight_tag_once() -> None:
    controller = make_controller()
    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111"),
        HighlightRule(name="unused", pattern="unused", foreground="#ffffff"),
    ]
    first_entry = make_entry("ANR first")
    second_entry = make_entry("ANR second")
    for entry in (first_entry, second_entry):
        entry.highlight_keys = ("ANR",)
        entry.matches_filters = True

    gui.LogcatToolGUI._append_visible_entries(controller, [first_entry, second_entry])

    assert controller.text.tag_config_calls == [
        ("highlight::ANR", {"foreground": "#ffcc00", "background": "#111111"})
    ]
    assert controller.text.tag_add_calls == [
        ("highlight::ANR", "1.0", "2.0"),
        ("highlight::ANR", "2.0", "3.0"),
    ]


def test_append_visible_entries_skips_index_lookup_for_plain_lines() -> None:
    controller = make_controller()
    entry = make_entry("plain line")
    entry.matches_filters = True
    entry.highlight_keys = ()

    gui.LogcatToolGUI._append_visible_entries(controller, [entry])

    assert controller.text.index_calls == []
    assert controller.text.tag_add_calls == []
    assert controller.text.insert_calls == [
        (gui.tk.END, "06-18 10:00:00.000 E ActivityManager: plain line\n", "E")
    ]


def test_append_entry_skips_highlight_matching_for_hidden_match_only_entry(monkeypatch) -> None:
    controller = make_controller()
    entry = make_entry("hidden line")
    filters = FilterState(minimum_level="F", match_only=True)
    rules = [HighlightRule(name="hidden", pattern="hidden", foreground="#fff")]
    calls: list[object] = []

    def match_highlights(entry_arg: LogEntry, rules_arg: list[HighlightRule]) -> tuple[str, ...]:
        calls.append((entry_arg, rules_arg))
        return ("hidden",)

    monkeypatch.setattr(gui, "match_highlight_rules", match_highlights)

    visible_entry, full_render_required = gui.LogcatToolGUI._append_entry(
        controller,
        entry,
        filters,
        rules,
    )

    assert visible_entry is None
    assert full_render_required is False
    assert calls == []
    assert entry.highlight_keys == ()


def test_refresh_visible_entries_skips_highlight_matching_for_hidden_match_only_entries(
    monkeypatch,
) -> None:
    controller = make_controller()
    hidden_entry = make_entry("hidden line")
    visible_entry = make_entry("visible line")
    visible_entry.level = "F"
    controller.raw_lines.extend([hidden_entry, visible_entry])
    filters = FilterState(minimum_level="F", match_only=True)
    rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    calls: list[LogEntry] = []

    controller._current_filters = lambda: filters
    controller._current_highlight_rules = lambda: rules

    def match_highlights(entry_arg: LogEntry, rules_arg: list[HighlightRule]) -> tuple[str, ...]:
        calls.append(entry_arg)
        return ("line",)

    monkeypatch.setattr(gui, "match_highlight_rules", match_highlights)

    gui.LogcatToolGUI._refresh_visible_entries(controller)

    assert calls == [visible_entry]
    assert list(controller.visible_lines) == [visible_entry]
    assert hidden_entry.highlight_keys == ()
    assert visible_entry.highlight_keys == ("line",)


def test_poll_stream_reuses_filter_snapshot_for_line_batch() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    for index in range(3):
        controller.events.put(StreamEvent(kind="line", entry=make_entry(f"line {index}")))
    filters = FilterState(minimum_level="E")
    rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    calls: list[str] = []

    def current_filters() -> FilterState:
        calls.append("filters")
        return filters

    def current_highlight_rules() -> list[HighlightRule]:
        calls.append("rules")
        return rules

    controller._current_filters = current_filters
    controller._current_highlight_rules = current_highlight_rules

    gui.LogcatToolGUI._poll_stream(controller)

    assert calls == ["filters", "rules"]
    assert len(controller.raw_lines) == 3
    assert len(controller.visible_lines) == 3
    assert controller.filters is filters
    assert controller.highlight_rules is rules


def test_poll_stream_prepares_keyword_filter_once_for_line_batch() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    for index in range(3):
        controller.events.put(StreamEvent(kind="line", entry=make_entry(f"crash {index}")))
    keyword = LowerCountingStr("CRASH")
    filters = FilterState(minimum_level="V", keyword=keyword)

    controller._current_filters = lambda: filters
    controller._current_highlight_rules = lambda: []

    gui.LogcatToolGUI._poll_stream(controller)

    assert keyword.lower_calls == 1
    assert len(controller.visible_lines) == 3


def test_refresh_visible_entries_prepares_keyword_filter_once_for_raw_log_batch() -> None:
    controller = make_controller()
    for index in range(3):
        controller.raw_lines.append(make_entry(f"crash {index}"))
    keyword = LowerCountingStr("CRASH")
    filters = FilterState(minimum_level="V", keyword=keyword)

    controller._current_filters = lambda: filters
    controller._current_highlight_rules = lambda: []

    gui.LogcatToolGUI._refresh_visible_entries(controller)

    assert keyword.lower_calls == 1
    assert len(controller.visible_lines) == 3


def test_load_named_preset_batches_filter_refreshes() -> None:
    controller = make_controller()
    refreshes: list[str] = []
    controller.named_presets = {
        "Errors": FilterState(
            minimum_level="E",
            tag_filters=("ActivityManager", "SystemUI"),
            keyword="crash",
            auto_scroll=False,
            match_only=True,
        )
    }
    controller.preset_var = DummyVar("Errors")

    def refresh_visible_entries() -> None:
        refreshes.append("refresh")

    def trigger_filter_trace() -> None:
        gui.LogcatToolGUI._handle_filter_trace(controller)

    controller._refresh_visible_entries = refresh_visible_entries
    controller.level_var = TriggeringVar("V", trigger_filter_trace)
    controller.tag_var = TriggeringVar("", trigger_filter_trace)
    controller.keyword_var = TriggeringVar("", trigger_filter_trace)
    controller.auto_scroll_var = TriggeringVar(True, trigger_filter_trace)
    controller.match_only_var = TriggeringVar(False, trigger_filter_trace)

    gui.LogcatToolGUI.load_named_preset(controller)

    assert refreshes == ["refresh"]
    assert controller.level_var.get() == "E"
    assert controller.tag_var.get() == "ActivityManager, SystemUI"
    assert controller.keyword_var.get() == "crash"
    assert controller.auto_scroll_var.get() is False
    assert controller.match_only_var.get() is True


def test_poll_stream_full_renders_when_visible_log_cap_rolls_over() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    controller.visible_lines = deque([make_entry("old")], maxlen=1)
    controller.events.put(StreamEvent(kind="line", entry=make_entry("new")))
    full_renders: list[object] = []
    controller._render_visible = lambda: full_renders.append(True)

    gui.LogcatToolGUI._poll_stream(controller)

    assert full_renders == [True]
    assert [entry.message for entry in controller.visible_lines] == ["new"]


def test_poll_stream_limits_events_per_tick_and_reschedules_immediately() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    for index in range(gui.MAX_EVENTS_PER_TICK + 1):
        controller.events.put(StreamEvent(kind="line", entry=make_entry(f"line {index}")))

    gui.LogcatToolGUI._poll_stream(controller)

    text = controller.text
    assert isinstance(text, DummyText)
    assert len(controller.raw_lines) == gui.MAX_EVENTS_PER_TICK
    assert len(text.insert_calls) == gui.MAX_EVENTS_PER_TICK
    assert controller.status.queue_depth == 1
    assert controller.root.after_calls[0][0] == 0


def test_stop_stream_surfaces_stop_failures_instead_of_claiming_idle() -> None:
    controller = make_controller()
    controller.session = FailingSession()
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = "R58M12345"

    gui.LogcatToolGUI.stop_stream(controller)

    assert controller.status.stream_state == "failed"
    assert "stop failed" in controller.status.last_error


def test_stop_active_session_retains_failed_session_ownership() -> None:
    controller = make_controller()
    original_events = controller.events
    failing_session = FailingSession()
    controller.session = failing_session

    error = gui.LogcatToolGUI._stop_active_session(controller, manual=True)

    assert error == "stop failed"
    assert controller.session is failing_session
    assert controller.events is original_events


def test_stop_stream_discards_pending_events_without_active_session() -> None:
    controller = make_controller()
    controller.session = None
    controller.status.stream_state = "streaming"
    controller.status.queue_depth = 2
    controller.events.put(StreamEvent(kind="line", entry=make_entry("late one")))
    controller.events.put(StreamEvent(kind="line", entry=make_entry("late two")))

    gui.LogcatToolGUI.stop_stream(controller)

    assert controller.events.empty()
    assert controller.status.stream_state == "idle"
    assert controller.status.queue_depth == 0


def test_start_stream_uses_unfiltered_capture_command_for_raw_export(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    ui_filters = FilterState(
        minimum_level="E",
        tag_filters=("MyApp",),
        keyword="fatal",
        match_only=True,
        auto_scroll=False,
    )
    captured: dict[str, object] = {}

    controller._current_device = lambda: selected_device
    controller._current_filters = lambda: ui_filters
    controller._current_highlight_rules = lambda: []
    controller._stop_active_session = lambda manual: None
    controller._update_status = lambda: None

    def fake_build_logcat_command(serial: str, filter_state: FilterState) -> list[str]:
        captured["serial"] = serial
        captured["filter_state"] = filter_state
        return ["adb", "-s", serial, "logcat"]

    class DummySession:
        def __init__(self, command: list[str], events: queue.Queue[StreamEvent]) -> None:
            captured["command"] = command
            captured["events"] = events

        def start(self) -> None:
            captured["started"] = True

    monkeypatch.setattr(gui, "build_logcat_command", fake_build_logcat_command)
    monkeypatch.setattr(gui, "LogcatSession", DummySession)
    monkeypatch.setattr(gui, "messagebox", SimpleNamespace(showwarning=lambda *args: None, showerror=lambda *args: None))

    gui.LogcatToolGUI.start_stream(controller)

    capture_filters = captured["filter_state"]
    assert isinstance(capture_filters, FilterState)
    assert capture_filters.minimum_level == "V"
    assert capture_filters.tag_filters == ()
    assert controller.filters == ui_filters


def test_start_stream_resets_stale_queue_depth(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    controller.status.queue_depth = 42
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None
    controller._update_status = lambda: None

    class DummySession:
        def __init__(self, command: list[str], events: queue.Queue[StreamEvent]) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(gui, "build_logcat_command", lambda serial, filter_state: ["adb", "-s", serial, "logcat"])
    monkeypatch.setattr(gui, "LogcatSession", DummySession)
    monkeypatch.setattr(gui, "messagebox", SimpleNamespace(showwarning=lambda *args: None, showerror=lambda *args: None))

    gui.LogcatToolGUI.start_stream(controller)

    assert controller.status.queue_depth == 0


def test_refresh_devices_failure_clears_stale_devices_and_selection(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial

    def raise_refresh_error() -> list[DeviceInfo]:
        raise RuntimeError("adb unavailable")

    monkeypatch.setattr(gui, "list_devices", raise_refresh_error)

    gui.LogcatToolGUI.refresh_devices(controller)

    assert controller.devices == []
    assert controller.device_var.get() == ""
    assert controller.device_combo.values == ()
    assert controller.status.active_device_serial == ""
    assert controller.status.adb_ready is False
    assert "adb unavailable" in controller.status.last_error


def test_refresh_devices_async_schedules_list_devices(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.refresh_devices_async(controller)

    assert captured["message"] == "正在刷新设备..."
    devices = captured["action"]()
    captured["on_success"](devices)

    assert controller.devices == [device]
    assert controller.device_var.get() == gui.device_label(device)


def test_current_device_resolves_duplicate_models_by_unique_label() -> None:
    controller = make_controller()
    first_device = make_modeled_device("USB123", "Pixel_8")
    second_device = make_modeled_device("USB456", "Pixel_8")
    controller.devices = [first_device, second_device]
    controller.device_var.set(gui.device_label(second_device))

    selected = gui.LogcatToolGUI._current_device(controller)

    assert selected.serial == second_device.serial


def test_connect_tcp_schedules_connect_and_refresh(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("192.168.1.111:5555")
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str) -> str:
        calls.append(("connect", target))
        return "connected to 192.168.1.111:5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    assert captured["message"] == "正在连接 192.168.1.111:5555..."
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("connect", "192.168.1.111:5555")]
    assert controller.devices == [device]
    assert controller.status.last_error == "connected to 192.168.1.111:5555"


def test_connect_tcp_defaults_to_5555_when_port_is_omitted(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("192.168.1.111:5555")
    controller.connect_var.set("192.168.1.111")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str) -> str:
        calls.append(("connect", target))
        return "connected to 192.168.1.111:5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    assert controller.connect_var.get() == "192.168.1.111:5555"
    assert captured["message"] == "正在连接 192.168.1.111:5555..."
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("connect", "192.168.1.111:5555")]
    assert controller.devices == [device]


def test_connect_tcp_prepares_selected_usb_device_before_connecting_target(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    controller.connect_var.set(tcp_device.serial)
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [
        ("tcpip", ("USB123", 5555)),
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
    ]
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial


def test_connect_tcp_keeps_connected_target_when_device_refresh_fails(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    target = "192.168.1.111:5555"
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set(target)
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def raise_refresh_error():
        raise RuntimeError("[WinError 6] 句柄无效。")

    monkeypatch.setattr(gui, "enable_tcpip", lambda serial, port: "restarting in TCP mode port: 5555\n")
    monkeypatch.setattr(
        gui,
        "connect_device",
        lambda target, attempts=1, delay_seconds=0.0: f"connected to {target}\n",
    )
    monkeypatch.setattr(gui, "list_devices", raise_refresh_error)
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert [device.serial for device in controller.devices] == ["USB123", target]
    assert controller.device_var.get() == f"{target} [tcp]"
    assert controller.status.active_device_serial == target
    assert controller.status.last_error == (
        "connected to 192.168.1.111:5555；设备列表刷新失败：[WinError 6] 句柄无效。"
    )


def test_connect_tcp_selects_connected_tcp_device_when_usb_was_selected(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    controller.connect_var.set(tcp_device.serial)
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(gui, "enable_tcpip", lambda serial, port: "restarting in TCP mode port: 5555\n")
    monkeypatch.setattr(
        gui,
        "connect_device",
        lambda target, attempts=1, delay_seconds=0.0: f"connected to {target}\n",
    )
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial


def test_select_device_by_serial_preserves_active_stream_target() -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device, tcp_device]
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = usb_device.serial

    selected = gui.LogcatToolGUI._select_device_by_serial(controller, tcp_device.serial)

    assert selected is True
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == usb_device.serial


def test_export_entries_warns_for_empty_logs_without_file_dialog(monkeypatch) -> None:
    controller = make_controller()
    warnings: list[tuple[str, str]] = []
    errors: list[tuple[object, ...]] = []
    save_dialog_calls: list[object] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: errors.append(args),
        ),
    )
    monkeypatch.setattr(
        gui,
        "filedialog",
        SimpleNamespace(
            asksaveasfilename=lambda **kwargs: save_dialog_calls.append(kwargs) or "logs.txt"
        ),
    )

    gui.LogcatToolGUI._export_entries(controller, [], "可见")

    assert warnings == [("没有日志", "当前没有可导出的可见日志。")]
    assert errors == []
    assert save_dialog_calls == []
    assert controller.status.last_error == "当前没有可导出的可见日志。"


def test_clear_device_logcat_schedules_background_clear(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    controller._current_device = lambda: selected_device

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_clear_logcat(serial: str) -> None:
        calls.append(("clear", serial))

    monkeypatch.setattr(gui, "clear_logcat", fake_clear_logcat)
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.clear_device_logcat(controller)

    assert captured["message"] == "正在清空 USB123 的设备日志..."
    captured["action"]()
    captured["on_success"](None)

    assert calls == [("clear", "USB123")]
    assert controller.status.last_error == "已清空设备 logcat。"


def test_restart_adb_schedules_restart_and_refresh(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    controller.stop_stream = lambda: calls.append(("stop", None))

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(gui, "restart_server", lambda: calls.append(("restart", None)))
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.restart_adb(controller)

    assert calls == [("stop", None)]
    assert captured["message"] == "正在重启 ADB..."
    devices = captured["action"]()
    captured["on_success"](devices)

    assert calls == [("stop", None), ("restart", None)]
    assert controller.devices == [device]
    assert controller.status.last_error == ""


def test_restart_adb_aborts_when_stream_stop_fails(monkeypatch) -> None:
    controller = make_controller()
    calls: list[tuple[str, object]] = []

    def fake_stop_stream() -> None:
        calls.append(("stop", None))
        controller.status.stream_state = "failed"
        controller.status.last_error = "stop failed"

    def fake_run_background_task(message, action, on_success, on_error) -> None:
        calls.append(("background", message))

    controller.stop_stream = fake_stop_stream
    controller._run_background_task = fake_run_background_task
    monkeypatch.setattr(gui, "restart_server", lambda: calls.append(("restart", None)))

    gui.LogcatToolGUI.restart_adb(controller)

    assert calls == [("stop", None)]
    assert controller.status.last_error == "stop failed"


def test_build_highlight_text_tag_avoids_builtin_tag_collisions() -> None:
    assert gui.build_highlight_text_tag("E") != "E"
    assert gui.build_highlight_text_tag("filtered-out") != "filtered-out"


def test_retry_stream_uses_preserved_reconnect_target_after_refresh() -> None:
    controller = make_controller()
    target_device = make_device("target-serial")
    other_device = make_device("other-serial")
    started_with: list[str] = []
    controller.reconnect_target_serial = target_device.serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = other_device.serial

    def fake_refresh_devices() -> None:
        controller.devices = [other_device, target_device]
        controller.device_var.set(gui.device_label(other_device))
        controller.status.active_device_serial = other_device.serial

    controller.refresh_devices = fake_refresh_devices
    controller.start_stream = lambda: started_with.append(controller.device_var.get())

    gui.LogcatToolGUI._retry_stream(controller)

    assert started_with == [gui.device_label(target_device)]


def test_retry_stream_preserves_refresh_failure_reason() -> None:
    controller = make_controller()
    controller.reconnect_target_serial = "target-serial"
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = "target-serial"

    def fake_refresh_devices() -> None:
        controller.devices = []
        controller.status.adb_ready = False
        controller.status.last_error = "adb unavailable"

    controller.refresh_devices = fake_refresh_devices
    controller.start_stream = lambda: None

    gui.LogcatToolGUI._retry_stream(controller)

    assert controller.status.stream_state == "failed"
    assert "重连设备不可用" in controller.status.last_error
    assert "adb unavailable" in controller.status.last_error


def test_enable_wireless_adb_enables_tcpip_and_connects_discovered_ip(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    calls: list[tuple[str, object]] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda _message, action, on_success, _on_error: on_success(action())
    controller._update_status = lambda: calls.append(("status", None))

    def fake_get_device_route_ip(serial: str) -> str:
        calls.append(("route_ip", serial))
        return "192.168.1.111"

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return "connected to 192.168.1.111:5555\n"

    monkeypatch.setattr(gui, "get_device_route_ip", fake_get_device_route_ip)
    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [selected_device, tcp_device])

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    adb_calls = [call for call in calls if call[0] != "status"]
    assert adb_calls == [
        ("route_ip", "USB123"),
        ("tcpip", ("USB123", 5555)),
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
    ]
    assert controller.connect_var.get() == "192.168.1.111:5555"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == "connected to 192.168.1.111:5555"


def test_enable_wireless_adb_keeps_connected_target_when_device_refresh_fails(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    target = "192.168.1.111:5555"

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda _message, action, on_success, _on_error: on_success(action())

    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "192.168.1.111")
    monkeypatch.setattr(gui, "enable_tcpip", lambda serial, port: "restarting in TCP mode port: 5555\n")
    monkeypatch.setattr(gui, "connect_device", lambda target, attempts, delay_seconds: f"connected to {target}\n")

    def raise_refresh_error():
        raise RuntimeError("[WinError 6] 句柄无效。")

    monkeypatch.setattr(gui, "list_devices", raise_refresh_error)

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert controller.connect_var.get() == target
    assert [device.serial for device in controller.devices] == ["USB123", target]
    assert controller.device_var.get() == f"{target} [tcp]"
    assert controller.status.active_device_serial == target
    assert controller.status.last_error == (
        "connected to 192.168.1.111:5555；设备列表刷新失败：[WinError 6] 句柄无效。"
    )


def test_enable_wireless_adb_explains_manual_connect_when_ip_is_unknown(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")

    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda _message, action, on_success, _on_error: on_success(action())

    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "")
    monkeypatch.setattr(gui, "enable_tcpip", lambda serial, port: "restarting in TCP mode port: 5555\n")
    monkeypatch.setattr(gui, "list_devices", lambda: [])

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert "手机 IP:5555" in controller.status.last_error
