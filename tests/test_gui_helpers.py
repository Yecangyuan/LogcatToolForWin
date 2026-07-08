from __future__ import annotations

from collections import deque
from pathlib import Path
import queue
from types import SimpleNamespace

import pytest

import logcat_tool_for_win.gui as gui
from logcat_tool_for_win.adb import ADBCommandError
from logcat_tool_for_win.devices import parse_devices_output
from logcat_tool_for_win.models import (
    AppStatus,
    DeviceInfo,
    FilterState,
    HighlightRule,
    LogEntry,
    NamedPreset,
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
        adb_path="C:/Android/platform-tools/adb.exe",
    )

    text = gui.format_status_text(status)

    assert "R58M12345" in text
    assert "第 2 次重连" in text
    assert "C:/Android/platform-tools/adb.exe" in text


def test_ensure_tcp_device_preserves_existing_metadata_when_recovering_target() -> None:
    target = "192.168.1.111:5555"
    existing = DeviceInfo(
        serial=target,
        display_name="Pixel 8",
        transport="tcp",
        state="offline",
        model="Pixel_8",
        product="husky",
        raw_descriptor=f"{target}\toffline",
    )

    devices = gui._ensure_tcp_device([existing], target)

    assert len(devices) == 1
    assert devices[0].serial == target
    assert devices[0].display_name == "Pixel 8"
    assert devices[0].transport == "tcp"
    assert devices[0].state == "device"
    assert devices[0].model == "Pixel_8"
    assert devices[0].product == "husky"


def test_build_highlight_rules_creates_rules_from_csv_text() -> None:
    rules = gui.build_highlight_rules("ANR, crash , ")

    assert [rule.name for rule in rules] == ["ANR", "crash"]


def test_build_highlight_rules_deduplicates_patterns_in_input_order() -> None:
    rules = gui.build_highlight_rules("ANR, crash, ANR, crash, timeout")

    assert [rule.name for rule in rules] == ["ANR", "crash", "timeout"]


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


class CountingVar(DummyVar):
    def __init__(self, value: object = "") -> None:
        super().__init__(value)
        self.set_calls = 0

    def set(self, value: object) -> None:
        self.set_calls += 1
        super().set(value)


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
        self.after_cancel_calls: list[str] = []
        self._after_id = 0

    def after(self, delay: int, callback: object) -> str:
        self.after_calls.append((delay, callback))
        self._after_id += 1
        return f"after-{self._after_id}"

    def after_cancel(self, callback_id: str) -> None:
        self.after_cancel_calls.append(callback_id)


class DestroyedRoot:
    def after(self, delay: int, callback: object) -> None:
        raise RuntimeError("application has been destroyed")


class BrokenAfterRoot:
    def after(self, delay: int, callback: object) -> None:
        raise RuntimeError("unexpected scheduler failure")


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


class JoinFailingSession:
    def stop(self) -> None:
        return None

    def join(self) -> None:
        raise RuntimeError("logcat 后台线程在 2 秒内未能停止。")


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
    controller._background_task_versions = {}
    controller._filter_refresh_suspended = False
    controller.events = queue.Queue()
    controller.root = DummyRoot()
    controller.status_var = DummyVar("")
    controller.summary_var = DummyVar("")
    controller.device_var = DummyVar("")
    controller.connect_var = DummyVar("")
    controller.recent_targets = []
    controller.level_var = DummyVar("V")
    controller.tag_var = DummyVar("")
    controller.keyword_var = DummyVar("")
    controller.highlight_var = DummyVar("")
    controller.device_combo = DummyCombo()
    controller.connect_combo = DummyCombo()
    controller.preset_var = DummyVar("")
    controller.named_presets = {}
    controller.raw_lines = deque()
    controller.visible_lines = deque()
    controller.filters = FilterState()
    controller.highlight_rules = []
    controller._configured_highlight_styles = {}
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


def test_run_background_task_ignores_ui_schedule_failure_after_close(monkeypatch) -> None:
    controller = make_controller()
    controller.root = DestroyedRoot()
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

    assert successes == []
    assert errors == []


def test_run_background_task_ignores_stale_result_for_same_task_key(monkeypatch) -> None:
    controller = make_controller()
    successes: list[str] = []
    errors: list[Exception] = []

    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)

    gui.LogcatToolGUI._run_background_task(
        controller,
        "第一次执行...",
        lambda: "old",
        successes.append,
        errors.append,
        task_key="device-sync",
    )
    gui.LogcatToolGUI._run_background_task(
        controller,
        "第二次执行...",
        lambda: "new",
        successes.append,
        errors.append,
        task_key="device-sync",
    )

    assert len(controller.root.after_calls) == 2

    _first_delay, first_callback = controller.root.after_calls[0]
    _second_delay, second_callback = controller.root.after_calls[1]
    first_callback()
    second_callback()

    assert successes == ["new"]
    assert errors == []


def test_schedule_ui_callback_reraises_unexpected_schedule_errors() -> None:
    controller = make_controller()
    controller.root = BrokenAfterRoot()

    with pytest.raises(RuntimeError, match="unexpected scheduler failure"):
        gui.LogcatToolGUI._schedule_ui_callback(controller, 0, lambda: None)


def test_schedule_reconnect_ignores_timer_schedule_failure_after_close() -> None:
    controller = make_controller()
    controller.root = DestroyedRoot()
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = "USB123"
    controller.reconnect_target_serial = ""

    gui.LogcatToolGUI._schedule_reconnect(controller)

    assert controller.status.stream_state == "reconnecting"
    assert controller.status.reconnect_attempt == 1
    assert controller.reconnect_target_serial == "USB123"


def test_schedule_reconnect_exhaustion_clears_retry_state() -> None:
    controller = make_controller()
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = "USB123"
    controller.reconnect_target_serial = "USB123"
    controller.status.reconnect_attempt = gui.MAX_RECONNECT_ATTEMPTS

    gui.LogcatToolGUI._schedule_reconnect(controller)

    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连次数已用尽。"


def test_schedule_reconnect_fails_immediately_when_target_is_missing() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = ""
    controller.reconnect_target_serial = ""

    gui.LogcatToolGUI._schedule_reconnect(controller)

    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连设备不可用：缺少重连目标。"
    assert controller.root.after_calls == []


def test_poll_stream_ignores_reschedule_failure_after_close() -> None:
    controller = make_controller()
    controller.root = DestroyedRoot()

    gui.LogcatToolGUI._poll_stream(controller)

    assert controller.status.queue_depth == 0


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
    assert controller.root.after_calls == []


def test_poll_stream_skips_status_update_when_idle_queue_is_unchanged() -> None:
    controller = make_controller()
    controller.status.queue_depth = 0
    controller.status_var.set("stable status")
    controller.summary_var.set("stable summary")

    gui.LogcatToolGUI._poll_stream(controller)

    assert controller.status_var.get() == "stable status"
    assert controller.summary_var.get() == "stable summary"
    assert controller.root.after_calls == []


def test_update_status_skips_redundant_variable_sets() -> None:
    controller = make_controller()
    status_text = gui.format_status_text(controller.status)
    summary_text = gui.build_summary_text(0, 0, controller.status.stream_state)
    controller.status_var = CountingVar(status_text)
    controller.summary_var = CountingVar(summary_text)

    gui.LogcatToolGUI._update_status(controller)

    assert controller.status_var.set_calls == 0
    assert controller.summary_var.set_calls == 0


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


def test_poll_stream_skips_redundant_status_update_for_line_only_batch() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    controller.events.put(StreamEvent(kind="line", entry=make_entry("first")))
    status_updates: list[str] = []
    controller._update_status = lambda: status_updates.append("status")

    gui.LogcatToolGUI._poll_stream(controller)

    assert status_updates == []
    assert len(controller.raw_lines) == 1
    assert len(controller.visible_lines) == 1
    assert controller.status.queue_depth == 0
    assert controller.root.after_calls[0][0] == gui.QUEUE_DRAIN_MS


def test_poll_stream_skips_redundant_status_update_for_repeated_stderr_message() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    controller.status.last_error = "device offline"
    controller.events.put(StreamEvent(kind="stderr", message="device offline"))
    status_updates: list[str] = []
    controller._update_status = lambda: status_updates.append("status")

    gui.LogcatToolGUI._poll_stream(controller)

    assert status_updates == []
    assert controller.status.last_error == "device offline"
    assert controller.status.queue_depth == 0


def test_poll_stream_ignores_late_stopped_event_while_already_reconnecting() -> None:
    controller = make_controller()
    controller.status.stream_state = "reconnecting"
    controller.manual_stop = False
    controller.session = object()
    controller.events.put(StreamEvent(kind="stopped"))
    reconnects: list[str] = []
    controller._schedule_reconnect = lambda: reconnects.append("reconnect")

    gui.LogcatToolGUI._poll_stream(controller)

    assert reconnects == []
    assert controller.session is None
    assert controller.status.stream_state == "reconnecting"
    assert controller.status.queue_depth == 0


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


def test_append_visible_entries_reuses_existing_highlight_style_across_batches() -> None:
    controller = make_controller()
    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111")
    ]
    first_entry = make_entry("ANR first")
    second_entry = make_entry("ANR second")
    first_entry.highlight_keys = ("ANR",)
    first_entry.matches_filters = True
    second_entry.highlight_keys = ("ANR",)
    second_entry.matches_filters = True

    gui.LogcatToolGUI._append_visible_entries(controller, [first_entry])
    gui.LogcatToolGUI._append_visible_entries(controller, [second_entry])

    assert controller.text.tag_config_calls == [
        ("highlight::ANR", {"foreground": "#ffcc00", "background": "#111111"})
    ]


def test_append_visible_entries_reuses_highlight_tag_map_across_batches_when_rules_are_unchanged(
    monkeypatch,
) -> None:
    controller = make_controller()
    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111")
    ]
    first_entry = make_entry("ANR first")
    second_entry = make_entry("ANR second")
    first_entry.highlight_keys = ("ANR",)
    first_entry.matches_filters = True
    second_entry.highlight_keys = ("ANR",)
    second_entry.matches_filters = True
    calls: list[str] = []

    def build_tag(rule_name: str) -> str:
        calls.append(rule_name)
        return f"highlight::{rule_name}"

    monkeypatch.setattr(gui, "build_highlight_text_tag", build_tag)

    gui.LogcatToolGUI._append_visible_entries(controller, [first_entry])
    gui.LogcatToolGUI._append_visible_entries(controller, [second_entry])

    assert calls == ["ANR"]


def test_append_visible_entries_builds_each_highlight_text_tag_once_per_batch(monkeypatch) -> None:
    controller = make_controller()
    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111")
    ]
    first_entry = make_entry("ANR first")
    second_entry = make_entry("ANR second")
    first_entry.highlight_keys = ("ANR",)
    first_entry.matches_filters = True
    second_entry.highlight_keys = ("ANR",)
    second_entry.matches_filters = True
    calls: list[str] = []

    def build_tag(rule_name: str) -> str:
        calls.append(rule_name)
        return f"highlight::{rule_name}"

    monkeypatch.setattr(gui, "build_highlight_text_tag", build_tag)

    gui.LogcatToolGUI._append_visible_entries(controller, [first_entry, second_entry])

    assert calls == ["ANR"]


def test_append_visible_entries_reconfigures_highlight_when_style_changes() -> None:
    controller = make_controller()
    first_entry = make_entry("ANR first")
    second_entry = make_entry("ANR second")
    first_entry.highlight_keys = ("ANR",)
    first_entry.matches_filters = True
    second_entry.highlight_keys = ("ANR",)
    second_entry.matches_filters = True

    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111")
    ]
    gui.LogcatToolGUI._append_visible_entries(controller, [first_entry])

    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffaa00", background="#222222")
    ]
    gui.LogcatToolGUI._append_visible_entries(controller, [second_entry])

    assert controller.text.tag_config_calls == [
        ("highlight::ANR", {"foreground": "#ffcc00", "background": "#111111"}),
        ("highlight::ANR", {"foreground": "#ffaa00", "background": "#222222"}),
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


def test_append_visible_entries_skips_highlight_tag_build_for_plain_lines(monkeypatch) -> None:
    controller = make_controller()
    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111")
    ]
    entry = make_entry("plain line")
    entry.matches_filters = True
    entry.highlight_keys = ()
    calls: list[str] = []

    def build_tag(rule_name: str) -> str:
        calls.append(rule_name)
        return f"highlight::{rule_name}"

    monkeypatch.setattr(gui, "build_highlight_text_tag", build_tag)

    gui.LogcatToolGUI._append_visible_entries(controller, [entry])

    assert calls == []


def test_render_visible_skips_highlight_tag_build_for_plain_lines(monkeypatch) -> None:
    controller = make_controller()
    controller.highlight_rules = [
        HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00", background="#111111")
    ]
    entry = make_entry("plain line")
    entry.matches_filters = True
    entry.highlight_keys = ()
    controller.visible_lines.append(entry)
    calls: list[str] = []

    def build_tag(rule_name: str) -> str:
        calls.append(rule_name)
        return f"highlight::{rule_name}"

    monkeypatch.setattr(gui, "build_highlight_text_tag", build_tag)

    gui.LogcatToolGUI._render_visible(controller)

    assert calls == []


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

    controller.filters = filters
    controller.highlight_rules = rules

    def match_highlights(entry_arg: LogEntry, rules_arg: list[HighlightRule]) -> tuple[str, ...]:
        calls.append(entry_arg)
        return ("line",)

    monkeypatch.setattr(gui, "match_highlight_rules", match_highlights)

    gui.LogcatToolGUI._refresh_visible_entries(controller)

    assert calls == [visible_entry]
    assert list(controller.visible_lines) == [visible_entry]
    assert hidden_entry.highlight_keys == ()
    assert visible_entry.highlight_keys == ("line",)


def test_poll_stream_uses_cached_filter_snapshot_for_line_batch() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    for index in range(3):
        controller.events.put(StreamEvent(kind="line", entry=make_entry(f"line {index}")))
    filters = FilterState(minimum_level="E")
    rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    controller.filters = filters
    controller.highlight_rules = rules
    controller._current_filters = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached filters")
    )
    controller._current_highlight_rules = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached highlight rules")
    )

    gui.LogcatToolGUI._poll_stream(controller)

    assert len(controller.raw_lines) == 3
    assert len(controller.visible_lines) == 3
    assert controller.filters is filters
    assert controller.highlight_rules is rules


def test_poll_stream_reuses_cached_filters_and_highlight_rules_across_line_batches() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    controller.filters = FilterState(minimum_level="E")
    controller.highlight_rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    controller.events.put(StreamEvent(kind="line", entry=make_entry("line 1")))
    controller.events.put(StreamEvent(kind="line", entry=make_entry("line 2")))

    controller._current_filters = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached filters")
    )
    controller._current_highlight_rules = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached highlight rules")
    )

    gui.LogcatToolGUI._poll_stream(controller)

    assert len(controller.raw_lines) == 2
    assert len(controller.visible_lines) == 2
    assert controller.filters.minimum_level == "E"
    assert controller.highlight_rules[0].name == "line"


def test_poll_stream_prepares_keyword_filter_once_for_line_batch() -> None:
    controller = make_controller()
    controller.status.stream_state = "streaming"
    controller.manual_stop = False
    for index in range(3):
        controller.events.put(StreamEvent(kind="line", entry=make_entry(f"crash {index}")))
    keyword = LowerCountingStr("CRASH")
    filters = FilterState(minimum_level="V", keyword=keyword)
    controller.filters = filters
    controller.highlight_rules = []

    gui.LogcatToolGUI._poll_stream(controller)

    assert keyword.lower_calls == 1
    assert len(controller.visible_lines) == 3


def test_handle_filter_trace_updates_cached_filters_before_refresh() -> None:
    controller = make_controller()
    controller.level_var.set("W")
    controller.keyword_var.set("timeout")

    gui.LogcatToolGUI._handle_filter_trace(controller)

    assert controller.filters.minimum_level == "W"
    assert controller.filters.keyword == "timeout"


def test_refresh_visible_entries_prepares_keyword_filter_once_for_raw_log_batch() -> None:
    controller = make_controller()
    for index in range(3):
        controller.raw_lines.append(make_entry(f"crash {index}"))
    keyword = LowerCountingStr("CRASH")
    filters = FilterState(minimum_level="V", keyword=keyword)

    controller.filters = filters
    controller.highlight_rules = []

    gui.LogcatToolGUI._refresh_visible_entries(controller)

    assert keyword.lower_calls == 1
    assert len(controller.visible_lines) == 3


def test_handle_highlight_trace_debounces_visible_rehighlight(monkeypatch) -> None:
    controller = make_controller()
    hidden_entry = make_entry("hidden line")
    visible_entry = make_entry("visible line")
    controller.raw_lines.extend([hidden_entry, visible_entry])
    controller.visible_lines.extend([visible_entry])
    controller.filters = FilterState(match_only=True)
    rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    calls: list[LogEntry] = []
    full_refreshes: list[str] = []
    renders: list[str] = []

    controller._current_highlight_rules = lambda: rules
    controller._refresh_visible_entries = lambda: full_refreshes.append("full")
    controller._render_visible = lambda: renders.append("render")

    def match_highlights(entry_arg: LogEntry, rules_arg: list[HighlightRule]) -> tuple[str, ...]:
        calls.append(entry_arg)
        return ("line",)

    monkeypatch.setattr(gui, "match_highlight_rules", match_highlights)

    gui.LogcatToolGUI._handle_highlight_trace(controller)

    assert renders == []
    assert controller.root.after_calls[0][0] == gui.FILTER_REFRESH_DELAY_MS

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert full_refreshes == []
    assert renders == ["render"]
    assert calls == [visible_entry]
    assert hidden_entry.highlight_keys == ()
    assert visible_entry.highlight_keys == ("line",)


def test_handle_highlight_trace_preserves_pending_full_refresh() -> None:
    controller = make_controller()
    refreshes: list[str] = []

    controller._refresh_visible_entries = lambda: refreshes.append("full")
    controller._refresh_highlight_entries = lambda: refreshes.append("highlight")

    gui.LogcatToolGUI._handle_filter_trace(controller)
    gui.LogcatToolGUI._handle_highlight_trace(controller)

    assert len(controller.root.after_calls) == 2
    assert controller.root.after_cancel_calls == ["after-1"]

    _first_delay, first_callback = controller.root.after_calls[0]
    _second_delay, second_callback = controller.root.after_calls[1]
    first_callback()
    second_callback()

    assert refreshes == ["full"]


def test_handle_auto_scroll_trace_scrolls_without_full_refresh() -> None:
    controller = make_controller()
    controller.auto_scroll_var.set(True)
    full_refreshes: list[str] = []
    controller._refresh_visible_entries = lambda: full_refreshes.append("full")

    gui.LogcatToolGUI._handle_auto_scroll_trace(controller)

    assert full_refreshes == []
    assert controller.filters.auto_scroll is True
    assert controller.text.see_calls == [gui.tk.END]


def test_handle_filter_trace_debounces_full_refresh() -> None:
    controller = make_controller()
    refreshes: list[str] = []
    controller._refresh_visible_entries = lambda: refreshes.append("refresh")

    gui.LogcatToolGUI._handle_filter_trace(controller)

    assert refreshes == []
    assert controller.root.after_calls[0][0] == gui.FILTER_REFRESH_DELAY_MS

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert refreshes == ["refresh"]


def test_handle_filter_trace_ignores_stale_debounced_callbacks() -> None:
    controller = make_controller()
    refreshes: list[str] = []
    controller._refresh_visible_entries = lambda: refreshes.append("refresh")

    gui.LogcatToolGUI._handle_filter_trace(controller)
    gui.LogcatToolGUI._handle_filter_trace(controller)

    assert len(controller.root.after_calls) == 2

    _first_delay, first_callback = controller.root.after_calls[0]
    _second_delay, second_callback = controller.root.after_calls[1]
    first_callback()
    second_callback()

    assert refreshes == ["refresh"]


def test_handle_filter_trace_cancels_previous_debounced_refresh() -> None:
    controller = make_controller()

    gui.LogcatToolGUI._handle_filter_trace(controller)
    gui.LogcatToolGUI._handle_filter_trace(controller)

    assert controller.root.after_cancel_calls == ["after-1"]


def test_manual_refresh_invalidates_pending_debounced_filter_refresh() -> None:
    controller = make_controller()
    renders: list[str] = []
    controller._render_visible = lambda: renders.append("render")

    gui.LogcatToolGUI._handle_filter_trace(controller)
    gui.LogcatToolGUI._refresh_visible_entries(controller)

    assert renders == ["render"]
    assert controller.root.after_cancel_calls == ["after-1"]

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert renders == ["render"]


def test_highlight_refresh_invalidates_pending_debounced_filter_refresh() -> None:
    controller = make_controller()
    visible_entry = make_entry("visible line")
    controller.visible_lines.extend([visible_entry])
    controller.highlight_rules = [
        HighlightRule(name="line", pattern="line", foreground="#fff")
    ]
    renders: list[str] = []
    controller._render_visible = lambda: renders.append("render")

    gui.LogcatToolGUI._handle_filter_trace(controller)
    gui.LogcatToolGUI._refresh_highlight_entries(controller)

    assert renders == ["render"]
    assert controller.root.after_cancel_calls == ["after-1"]

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert renders == ["render"]


def test_refresh_highlight_entries_reuses_cached_filters_and_rules() -> None:
    controller = make_controller()
    visible_entry = make_entry("visible line")
    controller.visible_lines.extend([visible_entry])
    controller.filters = FilterState(minimum_level="W")
    controller.highlight_rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    controller._current_filters = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached filters")
    )
    controller._current_highlight_rules = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached highlight rules")
    )

    gui.LogcatToolGUI._refresh_highlight_entries(controller)

    assert visible_entry.highlight_keys == ("line",)
    assert controller.filters.minimum_level == "W"
    assert controller.highlight_rules[0].name == "line"


def test_refresh_visible_entries_reuses_cached_filters_and_rules() -> None:
    controller = make_controller()
    visible_entry = make_entry("visible line")
    controller.raw_lines.extend([visible_entry])
    controller.filters = FilterState(minimum_level="W")
    controller.highlight_rules = [HighlightRule(name="line", pattern="line", foreground="#fff")]
    controller._current_filters = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached filters")
    )
    controller._current_highlight_rules = lambda: (_ for _ in ()).throw(
        AssertionError("should reuse cached highlight rules")
    )

    gui.LogcatToolGUI._refresh_visible_entries(controller)

    assert list(controller.visible_lines) == [visible_entry]
    assert visible_entry.highlight_keys == ("line",)
    assert controller.filters.minimum_level == "W"
    assert controller.highlight_rules[0].name == "line"


def test_load_named_preset_batches_filter_refreshes() -> None:
    controller = make_controller()
    refreshes: list[str] = []
    controller.named_presets = {
        "Errors": NamedPreset(
            filters=FilterState(
                minimum_level="E",
                tag_filters=("ActivityManager", "SystemUI"),
                keyword="crash",
                auto_scroll=False,
                match_only=True,
            ),
            highlight_patterns=("ANR", "crash"),
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
    controller.highlight_var = TriggeringVar("", trigger_filter_trace)
    controller.auto_scroll_var = TriggeringVar(True, trigger_filter_trace)
    controller.match_only_var = TriggeringVar(False, trigger_filter_trace)

    gui.LogcatToolGUI.load_named_preset(controller)

    assert refreshes == ["refresh"]
    assert controller.level_var.get() == "E"
    assert controller.tag_var.get() == "ActivityManager, SystemUI"
    assert controller.keyword_var.get() == "crash"
    assert controller.highlight_var.get() == "ANR, crash"
    assert controller.auto_scroll_var.get() is False
    assert controller.match_only_var.get() is True


def test_load_named_preset_updates_cached_filters_and_highlights_before_refresh() -> None:
    controller = make_controller()
    controller.named_presets = {
        "Errors": NamedPreset(
            filters=FilterState(
                minimum_level="E",
                tag_filters=("ActivityManager", "SystemUI"),
                keyword="crash",
                auto_scroll=False,
                match_only=True,
            ),
            highlight_patterns=("ANR", "crash"),
        )
    }
    controller.preset_var = DummyVar("Errors")
    controller.filters = FilterState(minimum_level="V")
    controller.highlight_rules = []
    seen: list[tuple[FilterState, tuple[str, ...]]] = []

    def refresh_visible_entries() -> None:
        seen.append(
            (
                controller.filters,
                tuple(rule.pattern for rule in controller.highlight_rules),
            )
        )

    controller._refresh_visible_entries = refresh_visible_entries

    gui.LogcatToolGUI.load_named_preset(controller)

    assert len(seen) == 1
    assert seen[0][0].minimum_level == "E"
    assert seen[0][0].tag_filters == ("ActivityManager", "SystemUI")
    assert seen[0][0].keyword == "crash"
    assert seen[0][0].auto_scroll is False
    assert seen[0][0].match_only is True
    assert seen[0][1] == ("ANR", "crash")


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


def test_stop_stream_clears_retry_state_when_stop_fails_during_reconnect() -> None:
    controller = make_controller()
    controller.session = FailingSession()
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 2
    controller.status.active_device_serial = "R58M12345"
    controller.reconnect_target_serial = "R58M12345"

    gui.LogcatToolGUI.stop_stream(controller)

    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "stop failed"


def test_stop_stream_surfaces_join_failures_instead_of_claiming_idle() -> None:
    controller = make_controller()
    controller.session = JoinFailingSession()
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = "R58M12345"

    gui.LogcatToolGUI.stop_stream(controller)

    assert controller.status.stream_state == "failed"
    assert controller.status.last_error == "logcat 后台线程在 2 秒内未能停止。"


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


def test_stop_stream_cancels_pending_poll_stream_callback() -> None:
    controller = make_controller()
    controller.session = None
    controller.status.stream_state = "streaming"
    controller._poll_stream_callback_id = "after-1"

    gui.LogcatToolGUI.stop_stream(controller)

    assert controller.root.after_cancel_calls == ["after-1"]
    assert controller._poll_stream_callback_id is None


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
    controller.status.adb_ready = True

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
    controller.status.adb_ready = True
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
    assert controller.root.after_calls[0][0] == gui.QUEUE_DRAIN_MS


def test_start_stream_cancels_stale_poll_callback_before_scheduling_new_one(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    controller.status.adb_ready = True
    controller._poll_stream_callback_id = "after-1"
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None
    controller._update_status = lambda: None

    class DummySession:
        def __init__(self, command: list[str], events: queue.Queue[StreamEvent]) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        gui,
        "build_logcat_command",
        lambda serial, filter_state: ["adb", "-s", serial, "logcat"],
    )
    monkeypatch.setattr(gui, "LogcatSession", DummySession)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(showwarning=lambda *args: None, showerror=lambda *args: None),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert controller.root.after_cancel_calls == ["after-1"]
    assert controller.root.after_calls[0][0] == gui.QUEUE_DRAIN_MS


def test_start_stream_warns_when_adb_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    warnings: list[tuple[str, str]] = []
    stop_calls: list[bool] = []

    controller.status.adb_ready = False
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: stop_calls.append(manual)

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert warnings == [("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")]
    assert stop_calls == []
    assert controller.status.stream_state == "idle"


def test_start_stream_offers_to_restart_adb_when_adb_is_not_ready_due_to_local_service_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.status.adb_ready = False
    controller.status.last_error = (
        "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
        "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
        "是否现在重启 ADB？"
    )
    controller.restart_adb = lambda: restart_calls.append("restart")
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: (_ for _ in ()).throw(
        AssertionError("should not stop session while adb is unavailable")
    )

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            showerror=lambda *args: None,
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.stream_state == "idle"
    assert controller.status.last_error == prompts[0][1]


def test_start_stream_fails_reconnect_when_adb_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    warnings: list[tuple[str, str]] = []

    controller.status.adb_ready = False
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 1
    controller.status.active_device_serial = selected_device.serial
    controller.reconnect_target_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert warnings == [("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")]
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连设备不可用：ADB 不可用。"


def test_start_stream_reconnect_offers_to_restart_adb_when_local_service_failure_is_cached(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.status.adb_ready = False
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 1
    controller.status.active_device_serial = selected_device.serial
    controller.reconnect_target_serial = selected_device.serial
    controller.status.last_error = (
        "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
        "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
        "是否现在重启 ADB？"
    )
    controller.restart_adb = lambda: restart_calls.append("restart")
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: (_ for _ in ()).throw(
                AssertionError("reconnect local adb service failures should use the recovery prompt")
            ),
            showerror=lambda *args: None,
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_start_stream_fails_reconnect_when_current_device_is_missing(monkeypatch) -> None:
    controller = make_controller()
    warnings: list[tuple[str, str]] = []

    controller.status.adb_ready = True
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 1
    controller.status.active_device_serial = "R58M12345"
    controller.reconnect_target_serial = "R58M12345"
    controller._current_device = lambda: (_ for _ in ()).throw(ValueError("未选择设备。"))
    controller._stop_active_session = lambda manual: None

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert warnings == [("需要选择设备", "未选择设备。")]
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连设备不可用：未选择设备。"


def test_start_stream_fails_reconnect_when_device_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    warnings: list[tuple[str, str]] = []
    selected_device = make_device("R58M12345", state="offline")

    controller.status.adb_ready = True
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 1
    controller.status.active_device_serial = selected_device.serial
    controller.reconnect_target_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert warnings == [("设备未就绪", "当前设备状态为 offline，请先选择已就绪的设备。")]
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连设备不可用：当前设备状态为 offline，请先选择已就绪的设备。"


def test_start_stream_clears_retry_state_when_reconnect_launch_fails(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    errors: list[tuple[str, str]] = []

    controller.status.adb_ready = True
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 2
    controller.status.active_device_serial = selected_device.serial
    controller.reconnect_target_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None
    controller._update_status = lambda: None

    class FailingSession:
        def __init__(self, command: list[str], events: queue.Queue[StreamEvent]) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("launch failed")

    monkeypatch.setattr(gui, "build_logcat_command", lambda serial, filter_state: ["adb", "-s", serial, "logcat"])
    monkeypatch.setattr(gui, "LogcatSession", FailingSession)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert errors == [("启动失败", "launch failed")]
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "launch failed"


def test_start_stream_offers_to_restart_adb_for_local_service_failures(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    selected_label = gui.device_label(selected_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(selected_label)
    controller.device_combo["values"] = [selected_label]
    controller.status.active_device_serial = selected_device.serial
    controller.status.adb_ready = True
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None
    controller.restart_adb = lambda: restart_calls.append("restart")

    class FailingSession:
        def __init__(self, command: list[str], events: queue.Queue[StreamEvent]) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError(
                "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
            )

    monkeypatch.setattr(gui, "build_logcat_command", lambda serial, filter_state: ["adb", "-s", serial, "logcat"])
    monkeypatch.setattr(gui, "LogcatSession", FailingSession)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.devices == [selected_device]
    assert controller.device_var.get() == selected_label
    assert controller.status.active_device_serial == selected_device.serial
    assert controller.status.stream_state == "failed"
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_start_stream_clears_retry_state_when_reconnect_stop_fails(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    errors: list[tuple[str, str]] = []

    controller.status.adb_ready = True
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 2
    controller.status.active_device_serial = selected_device.serial
    controller.reconnect_target_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: "stop failed"
    controller._update_status = lambda: None

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI.start_stream(controller)

    assert errors == [("停止失败", "stop failed")]
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "stop failed"


def test_refresh_devices_failure_preserves_stale_devices_and_selection(monkeypatch) -> None:
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

    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.device_combo.values == (stale_label,)
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_ready is False
    assert "adb unavailable" in controller.status.last_error


def test_refresh_devices_offers_to_switch_adb_path_for_launch_failures(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    def raise_refresh_error() -> list[DeviceInfo]:
        raise RuntimeError("无法启动 adb：[WinError 6] 句柄无效。")

    monkeypatch.setattr(gui, "list_devices", raise_refresh_error)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.refresh_devices(controller)

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_ready is False
    assert controller.status.last_error == prompts[0][1]


def test_refresh_devices_async_schedules_list_devices(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path("C:/platform-tools/adb.exe"))
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.refresh_devices_async(controller)

    assert captured["message"] == "正在刷新设备..."
    devices = captured["action"]()
    captured["on_success"](devices)

    assert controller.devices == [device]
    assert controller.device_var.get() == gui.device_label(device)
    assert controller.status.adb_path == "C:/platform-tools/adb.exe"


def test_refresh_devices_async_offers_to_switch_adb_path_for_launch_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []
    captured: dict[str, object] = {}

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.refresh_devices_async(controller)
    captured["on_error"](RuntimeError("无法启动 adb：[WinError 6] 句柄无效。"))

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_ready is False
    assert controller.status.last_error == prompts[0][1]


def test_refresh_devices_offers_to_restart_adb_for_local_service_failures(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.restart_adb = lambda: restart_calls.append("restart")

    def raise_refresh_error() -> list[gui.DeviceInfo]:
        raise RuntimeError(
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
        )

    monkeypatch.setattr(gui, "list_devices", raise_refresh_error)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.refresh_devices(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_ready is False
    assert controller.status.last_error == prompts[0][1]


def test_refresh_devices_offers_to_restart_adb_for_adb_server_ack_failures(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.restart_adb = lambda: restart_calls.append("restart")

    def raise_refresh_error() -> list[gui.DeviceInfo]:
        raise RuntimeError(
            "* daemon not running; starting now at tcp:5037\n"
            "ADB server didn't ACK\n"
            "Full server startup log: C:\\Users\\tester\\AppData\\Local\\Temp\\adb.log\n"
            "cannot bind listener: Permission denied"
        )

    monkeypatch.setattr(gui, "list_devices", raise_refresh_error)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb daemon startup failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.refresh_devices(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "* daemon not running; starting now at tcp:5037\n"
            "ADB server didn't ACK\n"
            "Full server startup log: C:\\Users\\tester\\AppData\\Local\\Temp\\adb.log\n"
            "cannot bind listener: Permission denied\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_ready is False
    assert controller.status.last_error == prompts[0][1]


def test_refresh_devices_async_offers_to_restart_adb_for_local_service_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []
    captured: dict[str, object] = {}

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.restart_adb = lambda: restart_calls.append("restart")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.refresh_devices_async(controller)
    captured["on_error"](
        RuntimeError(
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
        )
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_ready is False
    assert controller.status.last_error == prompts[0][1]


def test_apply_devices_aligns_selection_to_active_stream_target() -> None:
    controller = make_controller()
    active_device = make_device("USB123")
    other_device = make_device("USB456")
    controller.devices = [active_device, other_device]
    controller.device_var.set(gui.device_label(other_device))
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = active_device.serial

    gui.LogcatToolGUI._apply_devices(controller, [other_device, active_device])

    assert controller.device_var.get() == gui.device_label(active_device)
    assert controller.status.active_device_serial == active_device.serial


def test_apply_devices_keeps_stale_active_stream_target_during_refresh() -> None:
    controller = make_controller()
    active_device = make_device("USB123")
    other_device = make_device("USB456")
    controller.devices = [active_device]
    controller.device_var.set(gui.device_label(active_device))
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = active_device.serial

    gui.LogcatToolGUI._apply_devices(controller, [other_device])

    assert [device.serial for device in controller.devices] == ["USB456", "USB123"]
    assert controller.device_var.get() == gui.device_label(active_device)
    assert controller.device_combo.values == (
        gui.device_label(other_device),
        gui.device_label(active_device),
    )
    assert controller.status.active_device_serial == active_device.serial


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

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return "connected to 192.168.1.111:5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    assert captured["message"] == "正在连接 192.168.1.111:5555..."
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]
    assert controller.devices == [device]
    assert controller.status.last_error == "connected to 192.168.1.111:5555"


def test_connect_tcp_defaults_to_5555_when_port_is_omitted(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("192.168.1.111:5555")
    controller.connect_var.set("192.168.1.111")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return "connected to 192.168.1.111:5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    assert controller.connect_var.get() == "192.168.1.111:5555"
    assert captured["message"] == "正在连接 192.168.1.111:5555..."
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]
    assert controller.devices == [device]


def test_connect_tcp_uses_selected_usb_device_when_target_is_empty(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    warnings: list[tuple[str, str]] = []
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )
    controller._prepare_wireless_adb = lambda serial, port, preferred_target="": (
        "192.168.1.111:5555",
        "connected to 192.168.1.111:5555",
        [usb_device, tcp_device],
    )
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == []
    assert captured["message"] == "正在为 USB123 开启无线 ADB..."
    result = captured["action"]()
    captured["on_success"](result)
    assert controller.connect_var.get() == "192.168.1.111:5555"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == "connected to 192.168.1.111:5555"


def test_connect_tcp_accepts_port_only_input_for_selected_usb_device(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5556")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    controller.connect_var.set("5556")
    warnings: list[tuple[str, str]] = []
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )
    controller._prepare_wireless_adb = lambda serial, port, preferred_target="": (
        "192.168.1.111:5556",
        "connected to 192.168.1.111:5556",
        [usb_device, tcp_device],
    )
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == []
    assert captured["message"] == "正在为 USB123 开启无线 ADB..."
    result = captured["action"]()
    captured["on_success"](result)
    assert controller.connect_var.get() == "192.168.1.111:5556"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == "connected to 192.168.1.111:5556"


def test_connect_tcp_warns_when_host_text_is_invalid_for_selected_usb_device(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    controller.connect_var.set("bad-target:5556")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad-target")]
    assert background_calls == []


def test_connect_tcp_warns_when_host_without_port_is_invalid_for_selected_usb_device(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    controller.connect_var.set("bad-target")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad-target")]
    assert background_calls == []


def test_connect_tcp_empty_target_warns_when_selected_usb_device_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123", state="offline")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("设备未就绪", "当前设备状态为 offline，请先选择已就绪的 USB 设备。")]
    assert background_calls == []


def test_connect_tcp_empty_target_warns_when_no_device_is_selected(monkeypatch) -> None:
    controller = make_controller()
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.device_var.set("")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("需要选择设备", "未选择设备。")]
    assert background_calls == []


def test_connect_tcp_empty_target_warns_when_selected_device_is_not_usb(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("192.168.1.111:5555")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("需要 USB 设备", "请先选择通过 USB 连接的设备。")]
    assert background_calls == []


def test_connect_tcp_empty_target_warns_when_selected_device_is_android_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    selected_device = parse_devices_output(
        "List of devices attached\nemulator-5554\tdevice transport_id:9\n"
    )[0]
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("需要 USB 设备", "请先选择通过 USB 连接的设备。")]
    assert background_calls == []


def test_connect_tcp_port_only_warns_when_no_device_is_selected(monkeypatch) -> None:
    controller = make_controller()
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.device_var.set("")
    controller.connect_var.set("5555")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("需要选择设备", "未选择设备。")]
    assert background_calls == []


def test_connect_tcp_port_only_warns_when_selected_device_is_not_usb(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("192.168.1.111:5555")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller.connect_var.set("5555")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("需要 USB 设备", "请先选择通过 USB 连接的设备。")]
    assert background_calls == []


def test_connect_tcp_colon_port_only_warns_when_selected_device_is_not_usb(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("192.168.1.111:5555")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller.connect_var.set(":5555")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("需要 USB 设备", "请先选择通过 USB 连接的设备。")]
    assert background_calls == []


def test_connect_tcp_colon_port_only_warns_when_selected_usb_device_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123", state="offline")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller.connect_var.set(":5555")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("设备未就绪", "当前设备状态为 offline，请先选择已就绪的 USB 设备。")]
    assert background_calls == []


def test_connect_tcp_invalid_host_with_port_warns_instead_of_triggering_usb_fallback(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller.connect_var.set("bad host:5555")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad host")]
    assert background_calls == []


def test_connect_tcp_invalid_host_without_port_warns_instead_of_triggering_usb_fallback(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller.connect_var.set("bad host")
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.connect_tcp(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad host")]
    assert background_calls == []


def test_connect_tcp_retries_direct_tcp_connection(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("192.168.1.111:5555")
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return "connected to 192.168.1.111:5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]
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

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial


def test_connect_tcp_auto_enables_selected_usb_device_after_direct_connect_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set(tcp_device.serial)
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    connect_attempts = 0

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        nonlocal connect_attempts
        connect_attempts += 1
        calls.append(("connect", (target, attempts, delay_seconds)))
        if connect_attempts == 1:
            raise ADBCommandError("connection refused")
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "192.168.1.111")
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
        ("tcpip", ("USB123", 5555)),
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
    ]
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == (
        "首次直连失败，已自动为 USB123 开启无线 ADB；connected to 192.168.1.111:5555"
    )


def test_connect_tcp_retargets_after_tcpip_reveals_updated_usb_ip(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.222:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []
    route_ip_results = iter(["", "192.168.1.222"])

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_get_device_route_ip(serial: str) -> str:
        calls.append(("route_ip", serial))
        return next(route_ip_results)

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        if target == "192.168.1.111:5555":
            raise ADBCommandError("connection refused")
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "get_device_route_ip", fake_get_device_route_ip)
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    monkeypatch.setattr(gui.time, "sleep", lambda _seconds: None)
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
        ("route_ip", "USB123"),
        ("tcpip", ("USB123", 5555)),
        ("route_ip", "USB123"),
        ("connect", ("192.168.1.222:5555", 3, 1.0)),
    ]
    assert controller.connect_var.get() == "192.168.1.222:5555"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == (
        "首次直连 192.168.1.111:5555 失败，检测到 USB123 当前 IP 已变为 "
        "192.168.1.222，已自动改连 192.168.1.222:5555；connected to 192.168.1.222:5555"
    )


def test_connect_tcp_retargets_to_selected_usb_device_when_target_ip_mismatches(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.222:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []
    route_ip_lookups: list[str] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        if target == "192.168.1.111:5555":
            raise ADBCommandError("connection refused")
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(
        gui,
        "get_device_route_ip",
        lambda serial: route_ip_lookups.append(serial) or "192.168.1.222",
    )
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
        ("tcpip", ("USB123", 5555)),
        ("connect", ("192.168.1.222:5555", 3, 1.0)),
    ]
    assert route_ip_lookups == ["USB123"]
    assert controller.connect_var.get() == "192.168.1.222:5555"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == (
        "首次直连 192.168.1.111:5555 失败，检测到 USB123 当前 IP 已变为 "
        "192.168.1.222，已自动改连 192.168.1.222:5555；connected to 192.168.1.222:5555"
    )


def test_connect_tcp_does_not_fall_back_without_manual_usb_selection(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device]
    controller.connect_var.set(tcp_device.serial)
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        raise ADBCommandError("connection refused")

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device, tcp_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    with pytest.raises(ADBCommandError, match="connection refused"):
        captured["action"]()

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]
    assert controller.device_var.get() == ""


def test_connect_tcp_does_not_retry_usb_fallback_after_adb_launch_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        raise ADBCommandError("无法启动 adb：[WinError 6] 句柄无效。")

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    with pytest.raises(ADBCommandError, match="无法启动 adb：\\[WinError 6\\] 句柄无效。"):
        captured["action"]()

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]


def test_connect_tcp_does_not_retry_usb_fallback_after_local_adb_daemon_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["action"] = action

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        raise ADBCommandError(
            "无法连接 192.168.1.111:5555。"
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
        )

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    with pytest.raises(ADBCommandError, match="本机 ADB 服务异常"):
        captured["action"]()

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]


def test_connect_tcp_does_not_retry_usb_fallback_after_auth_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["action"] = action

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        raise ADBCommandError(
            "无法连接 192.168.1.111:5555。"
            "设备鉴权失败。请先解锁手机并在屏幕上允许 USB 调试授权；"
            "如果手机上没有弹出授权框，可先断开后重新插上 USB，或撤销 USB 调试授权后重试。"
        )

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    with pytest.raises(ADBCommandError, match="设备鉴权失败"):
        captured["action"]()

    assert calls == [("connect", ("192.168.1.111:5555", 3, 1.0))]


def test_connect_tcp_keeps_connected_target_when_device_refresh_fails(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    target = "192.168.1.111:5555"
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set(target)
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
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


def test_handle_connect_tcp_error_explains_usb_to_wireless_next_step(monkeypatch) -> None:
    controller = make_controller()
    errors: list[tuple[str, str]] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        ADBCommandError("无法连接 192.168.1.111:5555。原始错误：connection refused"),
    )

    assert errors == [
        (
            "连接失败",
            "无法连接 192.168.1.111:5555。原始错误：connection refused\n\n"
            "已先尝试直连目标地址。"
            "如果当前选中的是已授权的 USB 设备，程序也会自动尝试为它开启无线 ADB 后再重连；"
            "也可以手动点“USB 开启无线”。",
        )
    ]
    assert controller.status.last_error == errors[0][1]


def test_handle_connect_tcp_error_preserves_auth_failure_guidance_without_generic_usb_retry_hint(
    monkeypatch,
) -> None:
    controller = make_controller()
    errors: list[tuple[str, str]] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        ADBCommandError(
            "无法连接 192.168.1.111:5555。"
            "设备鉴权失败。请先解锁手机并在屏幕上允许 USB 调试授权；"
            "如果手机上没有弹出授权框，可先断开后重新插上 USB，或撤销 USB 调试授权后重试。"
        ),
    )

    assert errors == [
        (
            "连接失败",
            "无法连接 192.168.1.111:5555。"
            "设备鉴权失败。请先解锁手机并在屏幕上允许 USB 调试授权；"
            "如果手机上没有弹出授权框，可先断开后重新插上 USB，或撤销 USB 调试授权后重试。",
        )
    ]
    assert controller.status.last_error == errors[0][1]


def test_handle_connect_tcp_error_does_not_duplicate_usb_retry_guidance_after_auto_retry(
    monkeypatch,
) -> None:
    controller = make_controller()
    errors: list[tuple[str, str]] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        ADBCommandError(
            "failed to connect to 192.168.1.111:5555: Connection refused\n\n"
            "已尝试为当前 USB 设备 USB123 自动开启无线 ADB 后再连接，"
            "但仍失败：目标端口拒绝连接。通常是手机端还没监听该端口；"
            "请先用 USB 连上后点“USB 开启无线”，再重新连接。"
        ),
    )

    assert errors == [
        (
            "连接失败",
            "failed to connect to 192.168.1.111:5555: Connection refused\n\n"
            "已尝试为当前 USB 设备 USB123 自动开启无线 ADB 后再连接，"
            "但仍失败：目标端口拒绝连接。通常是手机端还没监听该端口；"
            "请先用 USB 连上后点“USB 开启无线”，再重新连接。",
        )
    ]
    assert controller.status.last_error == errors[0][1]


def test_handle_connect_tcp_error_offers_to_restart_adb_for_local_service_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("192.168.1.111:5555", state="offline")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.restart_adb = lambda: restart_calls.append("restart")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        ADBCommandError(
            "无法连接 192.168.1.111:5555。"
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
        ),
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "无法连接 192.168.1.111:5555。"
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_handle_connect_tcp_error_shows_selected_usb_ip_when_target_mismatches(monkeypatch) -> None:
    controller = make_controller()
    errors: list[tuple[str, str]] = []

    def fail_route_ip_lookup(serial: str) -> str:
        raise AssertionError("UI thread should not query device IP during error formatting")

    monkeypatch.setattr(gui, "get_device_route_ip", fail_route_ip_lookup)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    error = ADBCommandError("无法连接 192.168.1.111:5555。原始错误：connection refused")
    error.usb_ip_hint = "当前选中的 USB 设备 IP 是 192.168.1.222；可改连 192.168.1.222:5555。"
    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        error,
    )

    assert "当前选中的 USB 设备 IP 是 192.168.1.222" in errors[0][1]
    assert "可改连 192.168.1.222:5555" in errors[0][1]
    assert controller.status.last_error == errors[0][1]


def test_handle_connect_tcp_error_offers_to_switch_adb_path_for_launch_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("192.168.1.111:5555", state="offline")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        ADBCommandError("无法启动 adb：[WinError 6] 句柄无效。"),
    )

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_connect_tcp_retry_launch_failure_offers_to_switch_adb_path(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.configure_adb_path = lambda: configure_calls.append("configure")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    connect_attempts = 0

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        nonlocal connect_attempts
        connect_attempts += 1
        calls.append(("connect", (target, attempts, delay_seconds)))
        if connect_attempts == 1:
            raise ADBCommandError("connection refused")
        raise ADBCommandError("无法启动 adb：[WinError 6] 句柄无效。")

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "192.168.1.111")
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)

    with pytest.raises(ADBCommandError) as exc_info:
        captured["action"]()
    captured["on_error"](exc_info.value)

    assert calls == [
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
        ("tcpip", ("USB123", 5555)),
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
    ]
    assert len(prompts) == 1
    assert prompts[0][0] == "ADB 无法启动"
    assert "无法启动 adb：[WinError 6] 句柄无效。" in prompts[0][1]
    assert configure_calls == ["configure"]
    assert controller.status.last_error == prompts[0][1]


def test_start_stream_offers_to_switch_adb_path_for_launch_failures(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("R58M12345")
    selected_label = gui.device_label(selected_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [selected_device]
    controller.device_var.set(selected_label)
    controller.device_combo["values"] = [selected_label]
    controller.status.active_device_serial = selected_device.serial
    controller.status.adb_ready = True
    controller._current_device = lambda: selected_device
    controller._stop_active_session = lambda manual: None
    controller._update_status = lambda: None
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    class FailingSession:
        def __init__(self, command: list[str], events: queue.Queue[StreamEvent]) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("无法启动 adb：[WinError 6] 句柄无效。")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    monkeypatch.setattr(
        gui,
        "build_logcat_command",
        lambda serial, filter_state: ["adb", "-s", serial, "logcat"],
    )
    monkeypatch.setattr(gui, "LogcatSession", FailingSession)

    gui.LogcatToolGUI.start_stream(controller)

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.devices == [selected_device]
    assert controller.device_var.get() == selected_label
    assert controller.status.active_device_serial == selected_device.serial
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_connect_tcp_reports_retarget_failure_when_usb_device_ip_connect_still_fails(
    monkeypatch,
) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.connect_var.set("192.168.1.111:5555")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        if target == "192.168.1.111:5555":
            raise ADBCommandError("connection refused")
        raise ADBCommandError("timeout")

    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "192.168.1.222")
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [usb_device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.connect_tcp(controller)
    with pytest.raises(ADBCommandError) as exc_info:
        captured["action"]()

    assert calls == [
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
        ("tcpip", ("USB123", 5555)),
        ("connect", ("192.168.1.222:5555", 3, 1.0)),
    ]
    assert str(exc_info.value) == (
        "connection refused\n\n"
        "已检测到当前 USB 设备 USB123 的 IP 不再是 192.168.1.111:5555，"
        "程序已自动改连 192.168.1.222:5555，但仍失败：timeout"
    )


def test_handle_connect_tcp_error_ignores_usb_ip_hint_when_device_ip_matches_target(monkeypatch) -> None:
    controller = make_controller()
    errors: list[tuple[str, str]] = []

    def fail_route_ip_lookup(serial: str) -> str:
        raise AssertionError("UI thread should not query device IP during error formatting")

    monkeypatch.setattr(gui, "get_device_route_ip", fail_route_ip_lookup)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_connect_tcp_error(
        controller,
        ADBCommandError("无法连接 192.168.1.111:5555。原始错误：connection refused"),
    )

    assert "当前选中的 USB 设备 IP 是" not in errors[0][1]
    assert controller.status.last_error == errors[0][1]


def test_connect_tcp_selects_connected_tcp_device_when_usb_was_selected(monkeypatch) -> None:
    controller = make_controller()
    usb_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    controller.devices = [usb_device]
    controller.device_var.set(gui.device_label(usb_device))
    controller.status.active_device_serial = usb_device.serial
    controller.connect_var.set(tcp_device.serial)
    captured: dict[str, object] = {}

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
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


def test_handle_connect_tcp_success_updates_recent_target_history() -> None:
    controller = make_controller()
    current_device = make_device("192.168.1.111:5555")
    older_device = make_device("192.168.1.112:5555")
    controller.devices = [current_device, older_device]
    controller.recent_targets = ["192.168.1.112:5555", "192.168.1.113:5555"]

    gui.LogcatToolGUI._handle_connect_tcp_success(
        controller,
        (
            "192.168.1.111:5555",
            "connected to 192.168.1.111:5555",
            [current_device, older_device],
        ),
    )

    assert controller.recent_targets == [
        "192.168.1.111:5555",
        "192.168.1.112:5555",
        "192.168.1.113:5555",
    ]
    assert controller.connect_combo.values == (
        "192.168.1.111:5555",
        "192.168.1.112:5555",
        "192.168.1.113:5555",
    )


def test_save_session_state_persists_recent_target_history(monkeypatch) -> None:
    controller = make_controller()
    controller.state_file = Path("/tmp/state.json")
    controller.filters = FilterState(minimum_level="W")
    controller.highlight_rules = []
    controller.connect_var.set("192.168.1.111:5555")
    controller.recent_targets = ["192.168.1.112:5555"]
    controller._current_filters = lambda: controller.filters
    controller._current_highlight_rules = lambda: controller.highlight_rules
    controller._update_status = lambda: None
    captured: dict[str, object] = {}

    def fake_save_state(path, filters, rules, recent_target, recent_targets, manual_adb_path) -> None:
        captured["path"] = path
        captured["filters"] = filters
        captured["rules"] = rules
        captured["recent_target"] = recent_target
        captured["recent_targets"] = recent_targets
        captured["manual_adb_path"] = manual_adb_path

    monkeypatch.setattr(gui, "save_state", fake_save_state)
    monkeypatch.setattr(gui, "get_manual_adb_path", lambda: Path("C:/Android/platform-tools/adb.exe"))
    monkeypatch.setattr(gui, "messagebox", SimpleNamespace(showwarning=lambda *args: None, showerror=lambda *args: None))

    gui.LogcatToolGUI.save_session_state(controller)

    assert captured["path"] == controller.state_file
    assert captured["recent_target"] == "192.168.1.111:5555"
    assert captured["recent_targets"] == [
        "192.168.1.111:5555",
        "192.168.1.112:5555",
    ]
    assert captured["manual_adb_path"] == "C:/Android/platform-tools/adb.exe"
    assert controller.connect_combo.values == (
        "192.168.1.111:5555",
        "192.168.1.112:5555",
    )


def test_save_session_state_skips_invalid_recent_target_history(monkeypatch) -> None:
    controller = make_controller()
    controller.state_file = Path("/tmp/state.json")
    controller.filters = FilterState(minimum_level="W")
    controller.highlight_rules = []
    controller.connect_var.set("not-a-target")
    controller.recent_targets = ["192.168.1.112:5555"]
    controller._current_filters = lambda: controller.filters
    controller._current_highlight_rules = lambda: controller.highlight_rules
    controller._update_status = lambda: None
    captured: dict[str, object] = {}

    def fake_save_state(path, filters, rules, recent_target, recent_targets, manual_adb_path) -> None:
        captured["recent_target"] = recent_target
        captured["recent_targets"] = recent_targets
        captured["manual_adb_path"] = manual_adb_path

    monkeypatch.setattr(gui, "save_state", fake_save_state)
    monkeypatch.setattr(gui, "get_manual_adb_path", lambda: None)
    monkeypatch.setattr(gui, "messagebox", SimpleNamespace(showwarning=lambda *args: None, showerror=lambda *args: None))

    gui.LogcatToolGUI.save_session_state(controller)

    assert captured["recent_target"] == "not-a-target"
    assert captured["recent_targets"] == ["192.168.1.112:5555"]
    assert captured["manual_adb_path"] == ""
    assert controller.connect_combo.values == ("192.168.1.112:5555",)


def test_on_close_cancels_pending_filter_refresh_before_destroy() -> None:
    controller = make_controller()
    destroy_calls: list[str] = []
    close_steps: list[str] = []

    controller._pending_filter_refresh_id = "after-1"
    controller._poll_stream_callback_id = "after-2"
    controller._filter_refresh_version = 4
    controller.save_session_state = lambda: close_steps.append("save")
    controller._stop_active_session = lambda manual: close_steps.append(f"stop:{manual}") or None
    controller.root.destroy = lambda: destroy_calls.append("destroy")

    gui.LogcatToolGUI._on_close(controller)

    assert close_steps == ["save", "stop:True"]
    assert controller.root.after_cancel_calls == ["after-2", "after-1"]
    assert controller._poll_stream_callback_id is None
    assert controller._pending_filter_refresh_id is None
    assert controller._filter_refresh_version == 5
    assert destroy_calls == ["destroy"]


def test_on_close_ignores_already_scheduled_background_results(monkeypatch) -> None:
    controller = make_controller()
    successes: list[str] = []
    errors: list[Exception] = []
    destroy_calls: list[str] = []

    controller.save_session_state = lambda: None
    controller._stop_active_session = lambda manual: None
    controller.root.destroy = lambda: destroy_calls.append("destroy")

    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)

    gui.LogcatToolGUI._run_background_task(
        controller,
        "正在执行...",
        lambda: "ok",
        successes.append,
        errors.append,
    )

    assert len(controller.root.after_calls) == 1

    gui.LogcatToolGUI._on_close(controller)

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert successes == []
    assert errors == []
    assert destroy_calls == ["destroy"]


def test_restore_saved_manual_adb_path_applies_non_empty_path(monkeypatch, tmp_path: Path) -> None:
    controller = make_controller()
    captured: list[Path] = []
    adb_path = tmp_path / "platform-tools" / "adb.exe"
    adb_path.parent.mkdir(parents=True)
    adb_path.write_text("adb", encoding="utf-8")

    monkeypatch.setattr(gui, "set_manual_adb_path", lambda path: captured.append(path))

    message = gui.LogcatToolGUI._restore_saved_manual_adb_path(
        controller,
        str(adb_path),
    )

    assert captured == [adb_path]
    assert message == ""


def test_restore_saved_manual_adb_path_clears_empty_value(monkeypatch) -> None:
    controller = make_controller()
    captured: list[object] = []

    monkeypatch.setattr(gui, "set_manual_adb_path", lambda path: captured.append(path))

    message = gui.LogcatToolGUI._restore_saved_manual_adb_path(controller, "")

    assert captured == [None]
    assert message == ""


def test_restore_saved_manual_adb_path_warns_and_resets_when_path_is_missing(monkeypatch) -> None:
    controller = make_controller()
    captured: list[object] = []
    missing_path = Path("/tmp/missing-adb.exe")

    monkeypatch.setattr(gui, "set_manual_adb_path", lambda path: captured.append(path))

    message = gui.LogcatToolGUI._restore_saved_manual_adb_path(controller, str(missing_path))

    assert captured == [None]
    assert str(missing_path) in message
    assert "已恢复自动检测" in message


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
    controller.status.adb_ready = True

    controller._current_device = lambda: selected_device

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
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


def test_clear_device_logcat_warns_when_adb_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.status.adb_ready = False
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.clear_device_logcat(controller)

    assert warnings == [("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")]
    assert background_calls == []


def test_clear_device_logcat_offers_to_restart_adb_when_adb_is_not_ready_due_to_local_service_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []
    background_calls: list[str] = []

    controller.status.adb_ready = False
    controller.status.last_error = (
        "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
        "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
        "是否现在重启 ADB？"
    )
    controller.restart_adb = lambda: restart_calls.append("restart")
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            showerror=lambda *args: None,
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.clear_device_logcat(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert background_calls == []
    assert controller.status.last_error == prompts[0][1]


def test_clear_device_logcat_ignores_stale_failure_from_earlier_request(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    clear_results = iter([RuntimeError("old clear failed"), None])
    errors: list[tuple[str, str]] = []

    controller.status.adb_ready = True
    controller._current_device = lambda: selected_device

    def fake_clear_logcat(_serial: str) -> None:
        result = next(clear_results)
        if isinstance(result, Exception):
            raise result

    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(gui, "clear_logcat", fake_clear_logcat)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI.clear_device_logcat(controller)
    gui.LogcatToolGUI.clear_device_logcat(controller)

    assert len(controller.root.after_calls) == 2

    _first_delay, first_callback = controller.root.after_calls[0]
    _second_delay, second_callback = controller.root.after_calls[1]
    first_callback()
    second_callback()

    assert errors == []
    assert controller.status.last_error == "已清空设备 logcat。"


def test_handle_clear_logcat_error_offers_to_switch_adb_path_for_launch_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("USB123")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_clear_logcat_error(
        controller,
        RuntimeError("无法启动 adb：[WinError 6] 句柄无效。"),
    )

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_handle_clear_logcat_error_offers_to_restart_adb_for_local_service_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("USB123")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.restart_adb = lambda: restart_calls.append("restart")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_clear_logcat_error(
        controller,
        RuntimeError("本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"),
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_restart_adb_schedules_restart_and_refresh(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(gui, "restart_server", lambda: calls.append(("restart", None)))
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.restart_adb(controller)

    assert captured["message"] == "正在重启 ADB..."
    devices = captured["action"]()
    captured["on_success"](devices)

    assert calls == [("restart", None)]
    assert controller.devices == [device]
    assert controller.status.last_error == ""


def test_configure_adb_path_switches_to_selected_exe_and_refreshes_devices(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    selected_path = "C:/Android/platform-tools/adb.exe"
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            askyesnocancel=lambda title, message: True,
            showwarning=lambda *args: None,
            showerror=lambda *args: None,
        ),
    )
    monkeypatch.setattr(
        gui,
        "filedialog",
        SimpleNamespace(
            askopenfilename=lambda **kwargs: selected_path,
        ),
    )
    monkeypatch.setattr(gui, "get_manual_adb_path", lambda: None)
    monkeypatch.setattr(gui, "set_manual_adb_path", lambda path: calls.append(("set", str(path))))
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path(selected_path))
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.configure_adb_path(controller)

    assert captured["message"] == "正在切换 ADB..."
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [
        ("set", selected_path),
        ("set", "None"),
        ("set", selected_path),
    ]
    assert controller.devices == [device]
    assert controller.status.adb_path == selected_path
    assert controller.status.last_error == f"已切换 ADB：{selected_path}"


def test_configure_adb_path_resets_to_auto_detection(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    auto_path = "C:/platform-tools/adb.exe"
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []
    previous_manual_path = Path("C:/existing/adb.exe")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            askyesnocancel=lambda title, message: False,
            showwarning=lambda *args: None,
            showerror=lambda *args: None,
        ),
    )
    monkeypatch.setattr(
        gui,
        "filedialog",
        SimpleNamespace(
            askopenfilename=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("should not open file picker when resetting to auto")
            ),
        ),
    )
    monkeypatch.setattr(gui, "get_manual_adb_path", lambda: previous_manual_path)
    monkeypatch.setattr(gui, "set_manual_adb_path", lambda path: calls.append(("set", path)))
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path(auto_path))
    monkeypatch.setattr(gui, "list_devices", lambda: [device])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.configure_adb_path(controller)

    assert captured["message"] == "正在切换 ADB..."
    result = captured["action"]()
    captured["on_success"](result)

    assert calls == [("set", None), ("set", previous_manual_path), ("set", None)]
    assert controller.devices == [device]
    assert controller.status.last_error == f"已恢复自动检测 ADB：{auto_path}"


def test_configure_adb_path_reverts_temporary_path_when_result_becomes_stale(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    selected_path = Path("C:/Android/platform-tools/adb.exe")
    previous_path = Path("C:/existing/adb.exe")
    manual_path_state = {"value": previous_path}
    set_calls: list[Path | None] = []

    controller.stop_stream = lambda: None

    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            askyesnocancel=lambda title, message: True,
            showwarning=lambda *args: None,
            showerror=lambda *args: None,
        ),
    )
    monkeypatch.setattr(
        gui,
        "filedialog",
        SimpleNamespace(
            askopenfilename=lambda **kwargs: str(selected_path),
        ),
    )
    monkeypatch.setattr(gui, "get_manual_adb_path", lambda: manual_path_state["value"])
    monkeypatch.setattr(
        gui,
        "set_manual_adb_path",
        lambda path: set_calls.append(path) or manual_path_state.__setitem__("value", path),
    )
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: selected_path)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])

    gui.LogcatToolGUI.configure_adb_path(controller)

    assert manual_path_state["value"] == previous_path
    assert len(controller.root.after_calls) == 1

    controller._advance_background_task_version(gui.DEVICE_SYNC_TASK_KEY)

    _delay, callback = controller.root.after_calls[0]
    callback()

    assert manual_path_state["value"] == previous_path
    assert set_calls == [selected_path, previous_path]
    assert controller.devices == []
    assert controller.status.last_error == "正在切换 ADB..."


def test_restart_adb_continues_when_stream_stop_fails(monkeypatch) -> None:
    controller = make_controller()
    device = make_device("R58M12345")
    captured: dict[str, object] = {}
    controller.session = FailingSession()
    controller.status.stream_state = "streaming"
    controller.status.queue_depth = 2

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    controller._run_background_task = fake_run_background_task
    monkeypatch.setattr(gui, "restart_server", lambda: None)
    monkeypatch.setattr(gui, "list_devices", lambda: [device])

    gui.LogcatToolGUI.restart_adb(controller)

    assert captured["message"] == "正在重启 ADB..."
    assert controller.session is None
    assert controller.status.stream_state == "idle"
    assert controller.status.queue_depth == 0
    assert controller.status.last_error == "stop failed"


def test_configure_adb_path_continues_when_stream_stop_fails(monkeypatch) -> None:
    controller = make_controller()
    captured: dict[str, object] = {}
    controller.session = FailingSession()
    controller.status.stream_state = "streaming"
    controller.status.queue_depth = 2
    selected_path = "C:/Android/platform-tools/adb.exe"

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            askyesnocancel=lambda title, message: True,
            showwarning=lambda *args: None,
            showerror=lambda *args: None,
        ),
    )
    monkeypatch.setattr(
        gui,
        "filedialog",
        SimpleNamespace(
            askopenfilename=lambda **kwargs: selected_path,
        ),
    )
    monkeypatch.setattr(gui, "get_manual_adb_path", lambda: None)
    monkeypatch.setattr(gui, "set_manual_adb_path", lambda path: None)
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path(selected_path))
    monkeypatch.setattr(gui, "list_devices", lambda: [make_device("R58M12345")])
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI.configure_adb_path(controller)

    assert captured["message"] == "正在切换 ADB..."
    assert controller.session is None
    assert controller.status.stream_state == "idle"
    assert controller.status.queue_depth == 0
    assert controller.status.last_error == "stop failed"


def test_handle_restart_adb_error_marks_adb_unavailable(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    errors: list[tuple[str, str]] = []
    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_restart_adb_error(controller, RuntimeError("restart failed"))

    assert errors == [("ADB 重启失败", "restart failed")]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == "restart failed"


def test_build_highlight_text_tag_avoids_builtin_tag_collisions() -> None:
    assert gui.build_highlight_text_tag("E") != "E"
    assert gui.build_highlight_text_tag("filtered-out") != "filtered-out"


def test_handle_restart_adb_error_offers_to_switch_adb_path_for_launch_failures(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("192.168.1.111:5555", state="offline")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_restart_adb_error(
        controller,
        RuntimeError("adb.exe 启动后崩溃退出（0xC0000005）"),
    )

    assert prompts == [
        (
            "ADB 无法启动",
            "adb.exe 启动后崩溃退出（0xC0000005）\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_handle_restart_adb_error_offers_to_restart_adb_for_local_service_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("192.168.1.111:5555", state="offline")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.restart_adb = lambda: restart_calls.append("restart")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_restart_adb_error(
        controller,
        RuntimeError("本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"),
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_handle_configure_adb_path_error_offers_to_switch_adb_path_for_launch_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("192.168.1.111:5555", state="offline")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.configure_adb_path = lambda: configure_calls.append("configure")
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path("C:/Android/platform-tools/adb.exe"))
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_configure_adb_path_error(
        controller,
        RuntimeError("无法启动 adb：[WinError 6] 句柄无效。"),
    )

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_path == "C:/Android/platform-tools/adb.exe"
    assert controller.status.last_error == prompts[0][1]


def test_handle_configure_adb_path_error_offers_to_restart_adb_for_local_service_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("192.168.1.111:5555", state="offline")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.restart_adb = lambda: restart_calls.append("restart")
    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path("C:/Android/platform-tools/adb.exe"))
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_configure_adb_path_error(
        controller,
        RuntimeError("本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"),
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_path == "C:/Android/platform-tools/adb.exe"
    assert controller.status.last_error == prompts[0][1]


def test_handle_configure_adb_path_error_marks_adb_unavailable(monkeypatch) -> None:
    controller = make_controller()
    stale_device = make_device("R58M12345")
    stale_label = gui.device_label(stale_device)
    errors: list[tuple[str, str]] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True

    monkeypatch.setattr(gui, "resolve_adb_path", lambda: Path("C:/Android/platform-tools/adb.exe"))
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_configure_adb_path_error(controller, RuntimeError("switch failed"))

    assert errors == [("ADB 路径切换失败", "switch failed")]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.adb_path == "C:/Android/platform-tools/adb.exe"
    assert controller.status.last_error == "switch failed"


def test_retry_stream_uses_preserved_reconnect_target_after_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_device = make_device("target-serial")
    other_device = make_device("other-serial")
    started_with: list[str] = []
    captured: dict[str, object] = {}
    controller.reconnect_target_serial = target_device.serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = other_device.serial

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(gui, "list_devices", lambda: [other_device, target_device])
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: started_with.append(controller.device_var.get())

    gui.LogcatToolGUI._retry_stream(controller)
    devices = captured["action"]()
    captured["on_success"](devices)

    assert captured["message"] == "正在重连设备..."
    assert started_with == [gui.device_label(target_device)]


def test_retry_stream_reconnects_missing_tcp_target_before_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_device = make_device("192.168.1.111:5555")
    other_device = make_device("USB123")
    started_with: list[str] = []
    background_calls: list[dict[str, object]] = []
    connect_calls: list[tuple[str, float, float]] = []

    controller.reconnect_target_serial = target_device.serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_device.serial

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
                "task_key": task_key,
            }
        )

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        connect_calls.append((target, attempts, delay_seconds))
        return f"connected to {target}\n"

    lists = iter([[other_device], [other_device, target_device]])
    monkeypatch.setattr(gui, "list_devices", lambda: next(lists))
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: started_with.append(controller.device_var.get())

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    devices = first_call["action"]()
    first_call["on_success"](devices)

    assert len(background_calls) == 2
    second_call = background_calls[1]
    devices = second_call["action"]()
    second_call["on_success"](devices)

    assert connect_calls == [("192.168.1.111:5555", 2, 1.0)]
    assert started_with == [gui.device_label(target_device)]


def test_retry_stream_fails_when_tcp_target_reconnect_still_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_serial = "192.168.1.111:5555"
    other_device = make_device("USB123")
    background_calls: list[dict[str, object]] = []

    controller.status.adb_ready = True
    controller.reconnect_target_serial = target_serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_serial

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
            }
        )

    monkeypatch.setattr(gui, "list_devices", lambda: [other_device])
    monkeypatch.setattr(gui, "connect_device", lambda *args, **kwargs: (_ for _ in ()).throw(ADBCommandError("timeout")))
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: (_ for _ in ()).throw(AssertionError("should not start stream"))

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    devices = first_call["action"]()
    first_call["on_success"](devices)

    assert len(background_calls) == 2
    second_call = background_calls[1]
    with pytest.raises(ADBCommandError, match="timeout"):
        second_call["action"]()
    second_call["on_error"](ADBCommandError("timeout"))

    assert controller.status.stream_state == "failed"
    assert controller.status.adb_ready is True
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连设备不可用：timeout"


def test_retry_stream_tcp_reconnect_launch_failure_marks_adb_unavailable_and_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_serial = "192.168.1.111:5555"
    other_device = make_device("USB123")
    background_calls: list[dict[str, object]] = []
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [other_device]
    controller.status.adb_ready = True
    controller.reconnect_target_serial = target_serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_serial
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
            }
        )

    monkeypatch.setattr(gui, "list_devices", lambda: [other_device])
    monkeypatch.setattr(
        gui,
        "connect_device",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ADBCommandError("无法启动 adb：[WinError 6] 句柄无效。")
        ),
    )
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: (_ for _ in ()).throw(AssertionError("should not start stream"))

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    devices = first_call["action"]()
    first_call["on_success"](devices)

    assert len(background_calls) == 2
    second_call = background_calls[1]
    with pytest.raises(ADBCommandError, match="无法启动 adb：\\[WinError 6\\] 句柄无效。"):
        second_call["action"]()
    second_call["on_error"](ADBCommandError("无法启动 adb：[WinError 6] 句柄无效。"))

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_retry_stream_tcp_reconnect_local_service_failure_marks_adb_unavailable_and_prompts_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_serial = "192.168.1.111:5555"
    other_device = make_device("USB123")
    background_calls: list[dict[str, object]] = []
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [other_device]
    controller.status.adb_ready = True
    controller.reconnect_target_serial = target_serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_serial
    controller.restart_adb = lambda: restart_calls.append("restart")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
            }
        )

    monkeypatch.setattr(gui, "list_devices", lambda: [other_device])
    monkeypatch.setattr(
        gui,
        "connect_device",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ADBCommandError(
                "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"
            )
        ),
    )
    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: (_ for _ in ()).throw(AssertionError("should not start stream"))

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    devices = first_call["action"]()
    first_call["on_success"](devices)

    assert len(background_calls) == 2
    second_call = background_calls[1]
    with pytest.raises(ADBCommandError, match="本机 ADB 服务异常"):
        second_call["action"]()
    second_call["on_error"](
        ADBCommandError("本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。")
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_retry_stream_preserves_tcp_target_when_refresh_fails_after_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_device = make_device("192.168.1.111:5555")
    other_device = make_device("USB123")
    started_with: list[str] = []
    background_calls: list[dict[str, object]] = []
    connect_calls: list[tuple[str, float, float]] = []

    controller.devices = [other_device]
    controller.reconnect_target_serial = target_device.serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_device.serial

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
            }
        )

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        connect_calls.append((target, attempts, delay_seconds))
        return f"connected to {target}\n"

    lists = iter([[other_device]])

    def fake_list_devices() -> list[DeviceInfo]:
        try:
            return next(lists)
        except StopIteration as exc:
            raise RuntimeError("[WinError 6] 句柄无效。") from exc

    monkeypatch.setattr(gui, "list_devices", fake_list_devices)
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: started_with.append(controller.device_var.get())

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    devices = first_call["action"]()
    first_call["on_success"](devices)

    assert len(background_calls) == 2
    second_call = background_calls[1]
    devices = second_call["action"]()
    second_call["on_success"](devices)

    assert connect_calls == [("192.168.1.111:5555", 2, 1.0)]
    assert [device.serial for device in controller.devices] == ["USB123", "192.168.1.111:5555"]
    assert started_with == [gui.device_label(target_device)]


def test_retry_stream_attempts_tcp_reconnect_after_transient_refresh_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_device = make_device("192.168.1.111:5555")
    other_device = make_device("USB123")
    started_with: list[str] = []
    background_calls: list[dict[str, object]] = []
    connect_calls: list[tuple[str, float, float]] = []

    controller.devices = [other_device]
    controller.reconnect_target_serial = target_device.serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_device.serial

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
                "task_key": task_key,
            }
        )

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        connect_calls.append((target, attempts, delay_seconds))
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "list_devices", lambda: [other_device, target_device])
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: started_with.append(controller.device_var.get())

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    first_call["on_error"](RuntimeError("temporary refresh failure"))

    assert len(background_calls) == 2
    second_call = background_calls[1]
    devices = second_call["action"]()
    second_call["on_success"](devices)

    assert connect_calls == [("192.168.1.111:5555", 2, 1.0)]
    assert started_with == [gui.device_label(target_device)]


def test_retry_stream_refresh_local_service_failure_prompts_restart_instead_of_retrying_tcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    target_serial = "192.168.1.111:5555"
    stale_device = make_device("USB123")
    background_calls: list[dict[str, object]] = []
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(gui.device_label(stale_device))
    controller.status.adb_ready = True
    controller.reconnect_target_serial = target_serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = target_serial
    controller.status.reconnect_attempt = 1
    controller.restart_adb = lambda: restart_calls.append("restart")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        background_calls.append(
            {
                "message": message,
                "action": action,
                "on_success": on_success,
                "on_error": on_error,
            }
        )

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )
    controller._run_background_task = fake_run_background_task

    gui.LogcatToolGUI._retry_stream(controller)
    assert len(background_calls) == 1

    first_call = background_calls[0]
    first_call["on_error"](
        RuntimeError("本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。")
    )

    assert len(background_calls) == 1
    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_retry_stream_preserves_refresh_failure_reason() -> None:
    controller = make_controller()
    captured: dict[str, object] = {}
    stale_target = make_device("target-serial")
    controller.devices = [stale_target]
    controller.device_var.set(gui.device_label(stale_target))
    controller.status.adb_ready = True
    controller.reconnect_target_serial = "target-serial"
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = "target-serial"
    controller.status.reconnect_attempt = 1

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: None

    gui.LogcatToolGUI._retry_stream(controller)
    captured["on_error"](RuntimeError("adb unavailable"))

    assert controller.status.stream_state == "failed"
    assert controller.status.adb_ready is False
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert "重连设备不可用" in controller.status.last_error
    assert "adb unavailable" in controller.status.last_error


def test_retry_stream_offers_to_switch_adb_path_for_launch_failures(monkeypatch) -> None:
    controller = make_controller()
    captured: dict[str, object] = {}
    stale_target = make_device("target-serial")
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_target]
    controller.device_var.set(gui.device_label(stale_target))
    controller.status.adb_ready = True
    controller.reconnect_target_serial = "target-serial"
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = "target-serial"
    controller.status.reconnect_attempt = 1
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: None

    gui.LogcatToolGUI._retry_stream(controller)
    captured["on_error"](RuntimeError("无法启动 adb：[WinError 6] 句柄无效。"))

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.stream_state == "failed"
    assert controller.status.adb_ready is False
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == prompts[0][1]


def test_retry_stream_fails_when_reconnect_target_is_missing() -> None:
    controller = make_controller()
    controller.status.stream_state = "reconnecting"
    controller.status.reconnect_attempt = 1
    controller.status.active_device_serial = ""
    controller.reconnect_target_serial = ""

    gui.LogcatToolGUI._retry_stream(controller)

    assert controller.status.stream_state == "failed"
    assert controller.status.reconnect_attempt == 0
    assert controller.reconnect_target_serial == ""
    assert controller.status.last_error == "重连设备不可用：缺少重连目标。"


def test_retry_stream_ignores_stale_timer_after_stream_has_resumed() -> None:
    controller = make_controller()
    background_calls: list[str] = []
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = "target-serial"
    controller.reconnect_target_serial = "target-serial"
    controller.status.last_error = "stream healthy"
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    gui.LogcatToolGUI._retry_stream(controller)

    assert background_calls == []
    assert controller.status.last_error == "stream healthy"


def test_retry_stream_does_not_restart_from_stale_devices_after_refresh_failure() -> None:
    controller = make_controller()
    stale_target = make_device("target-serial")
    captured: dict[str, object] = {}
    controller.devices = [stale_target]
    controller.reconnect_target_serial = stale_target.serial
    controller.status.stream_state = "reconnecting"
    controller.status.active_device_serial = stale_target.serial
    started: list[str] = []

    def fake_run_background_task(message, action, on_success, on_error, task_key=None) -> None:
        captured["message"] = message
        captured["action"] = action
        captured["on_success"] = on_success
        captured["on_error"] = on_error

    controller._run_background_task = fake_run_background_task
    controller.start_stream = lambda: started.append("started")

    gui.LogcatToolGUI._retry_stream(controller)
    captured["on_error"](RuntimeError("adb unavailable"))

    assert started == []
    assert controller.status.stream_state == "failed"
    assert "重连设备不可用" in controller.status.last_error
    assert "adb unavailable" in controller.status.last_error


def test_enable_wireless_adb_enables_tcpip_and_connects_discovered_ip(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    calls: list[tuple[str, object]] = []
    controller.status.adb_ready = True

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )
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


def test_enable_wireless_adb_retries_ip_discovery_after_enabling_tcpip(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    calls: list[tuple[str, object]] = []
    controller.status.adb_ready = True

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )
    controller._update_status = lambda: calls.append(("status", None))

    route_ip_results = iter(["", "192.168.1.111"])

    def fake_get_device_route_ip(serial: str) -> str:
        calls.append(("route_ip", serial))
        return next(route_ip_results)

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
        ("route_ip", "USB123"),
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
    ]
    assert controller.connect_var.get() == "192.168.1.111:5555"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == "connected to 192.168.1.111:5555"


def test_enable_wireless_adb_waits_briefly_for_route_ip_after_tcpip(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    tcp_device = make_device("192.168.1.111:5555")
    calls: list[tuple[str, object]] = []
    sleeps: list[float] = []
    controller.status.adb_ready = True

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )
    controller._update_status = lambda: calls.append(("status", None))

    route_ip_results = iter(["", "", "", "192.168.1.111"])

    def fake_get_device_route_ip(serial: str) -> str:
        calls.append(("route_ip", serial))
        return next(route_ip_results)

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
    monkeypatch.setattr(gui.time, "sleep", lambda seconds: sleeps.append(seconds))

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    adb_calls = [call for call in calls if call[0] != "status"]
    assert adb_calls == [
        ("route_ip", "USB123"),
        ("tcpip", ("USB123", 5555)),
        ("route_ip", "USB123"),
        ("route_ip", "USB123"),
        ("route_ip", "USB123"),
        ("connect", ("192.168.1.111:5555", 3, 1.0)),
    ]
    assert sleeps == [0.5, 0.5]
    assert controller.connect_var.get() == "192.168.1.111:5555"
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == tcp_device.serial
    assert controller.status.last_error == "connected to 192.168.1.111:5555"


def test_enable_wireless_adb_uses_entered_target_when_ip_discovery_stays_unknown(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    target = "192.168.1.111:5555"
    tcp_device = make_device(target)
    calls: list[tuple[str, object]] = []
    sleeps: list[float] = []
    controller.status.adb_ready = True
    controller.connect_var.set(target)

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )
    controller._update_status = lambda: calls.append(("status", None))

    def fake_get_device_route_ip(serial: str) -> str:
        calls.append(("route_ip", serial))
        return ""

    def fake_enable_tcpip(serial: str, port: int) -> str:
        calls.append(("tcpip", (serial, port)))
        return "restarting in TCP mode port: 5555\n"

    def fake_connect_device(target: str, attempts: int = 1, delay_seconds: float = 0.0) -> str:
        calls.append(("connect", (target, attempts, delay_seconds)))
        return f"connected to {target}\n"

    monkeypatch.setattr(gui, "get_device_route_ip", fake_get_device_route_ip)
    monkeypatch.setattr(gui, "enable_tcpip", fake_enable_tcpip)
    monkeypatch.setattr(gui, "connect_device", fake_connect_device)
    monkeypatch.setattr(gui, "list_devices", lambda: [selected_device, tcp_device])
    monkeypatch.setattr(gui.time, "sleep", lambda seconds: sleeps.append(seconds))

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    adb_calls = [call for call in calls if call[0] != "status"]
    assert adb_calls == [
        ("route_ip", "USB123"),
        ("tcpip", ("USB123", 5555)),
        ("route_ip", "USB123"),
        ("route_ip", "USB123"),
        ("route_ip", "USB123"),
        ("route_ip", "USB123"),
        ("connect", (target, 3, 1.0)),
    ]
    assert sleeps == [0.5, 0.5, 0.5]
    assert controller.connect_var.get() == target
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == target
    assert controller.status.last_error == f"connected to {target}"


def test_enable_wireless_adb_accepts_port_only_input(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    target = "192.168.1.111:5556"
    tcp_device = make_device(target)
    calls: list[tuple[str, object]] = []
    controller.status.adb_ready = True
    controller.connect_var.set("5556")

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )
    controller._update_status = lambda: calls.append(("status", None))

    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "192.168.1.111")
    monkeypatch.setattr(
        gui,
        "enable_tcpip",
        lambda serial, port: calls.append(("tcpip", (serial, port))) or "restarting in TCP mode port: 5556\n",
    )
    monkeypatch.setattr(
        gui,
        "connect_device",
        lambda target, attempts, delay_seconds: calls.append(("connect", (target, attempts, delay_seconds)))
        or f"connected to {target}\n",
    )
    monkeypatch.setattr(gui, "list_devices", lambda: [selected_device, tcp_device])

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    adb_calls = [call for call in calls if call[0] != "status"]
    assert adb_calls == [
        ("tcpip", ("USB123", 5556)),
        ("connect", (target, 3, 1.0)),
    ]
    assert controller.connect_var.get() == target
    assert controller.device_var.get() == gui.device_label(tcp_device)
    assert controller.status.active_device_serial == target
    assert controller.status.last_error == f"connected to {target}"


def test_enable_wireless_adb_warns_when_host_with_port_is_invalid(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []
    controller.status.adb_ready = True
    controller.connect_var.set("bad-target:5556")

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad-target")]
    assert background_calls == []


def test_enable_wireless_adb_warns_when_port_text_is_invalid(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []
    controller.status.adb_ready = True
    controller.connect_var.set("bad-target:not-a-port")

    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("TCP 端口无效", "无效的 TCP 端口：not-a-port")]
    assert background_calls == []


def test_enable_wireless_adb_warns_when_host_text_is_invalid(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []
    controller.status.adb_ready = True
    controller.connect_var.set("bad host:5555")

    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad host")]
    assert background_calls == []


def test_enable_wireless_adb_warns_when_host_without_port_is_invalid(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []
    controller.status.adb_ready = True
    controller.connect_var.set("bad host")

    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("TCP 目标无效", "无效的 TCP IP 地址：bad host")]
    assert background_calls == []


def test_enable_wireless_adb_warns_when_adb_is_not_ready(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.status.adb_ready = False
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("ADB 不可用", "当前 ADB 不可用，请先刷新设备或重启 ADB。")]
    assert background_calls == []


def test_enable_wireless_adb_warns_when_selected_device_is_not_usb_even_if_offline(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("192.168.1.111:5555", state="offline", transport="tcp")
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.status.adb_ready = True
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("需要 USB 设备", "请先选择通过 USB 连接的设备。")]
    assert background_calls == []


def test_enable_wireless_adb_warns_when_selected_device_is_android_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = make_controller()
    selected_device = parse_devices_output(
        "List of devices attached\nemulator-5554\tdevice transport_id:9\n"
    )[0]
    warnings: list[tuple[str, str]] = []
    background_calls: list[str] = []

    controller.status.adb_ready = True
    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda title, message: warnings.append((title, message)),
            showerror=lambda *args: None,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert warnings == [("需要 USB 设备", "请先选择通过 USB 连接的设备。")]
    assert background_calls == []


def test_enable_wireless_adb_offers_to_restart_adb_when_adb_is_not_ready_due_to_local_service_failure(
    monkeypatch,
) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []
    background_calls: list[str] = []

    controller.status.adb_ready = False
    controller.status.last_error = (
        "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
        "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
        "是否现在重启 ADB？"
    )
    controller.restart_adb = lambda: restart_calls.append("restart")
    controller._current_device = lambda: selected_device
    controller._run_background_task = lambda *args, **kwargs: background_calls.append("background")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            showerror=lambda *args: None,
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert background_calls == []
    assert controller.status.last_error == prompts[0][1]


def test_enable_wireless_adb_keeps_connected_target_when_device_refresh_fails(monkeypatch) -> None:
    controller = make_controller()
    selected_device = make_device("USB123")
    target = "192.168.1.111:5555"
    controller.status.adb_ready = True

    controller.devices = [selected_device]
    controller.device_var.set(gui.device_label(selected_device))
    controller.status.active_device_serial = selected_device.serial
    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )

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
    sleeps: list[float] = []
    controller.status.adb_ready = True

    controller._current_device = lambda: selected_device
    controller._run_background_task = (
        lambda _message, action, on_success, _on_error, task_key=None: on_success(action())
    )

    monkeypatch.setattr(gui, "get_device_route_ip", lambda serial: "")
    monkeypatch.setattr(gui, "enable_tcpip", lambda serial, port: "restarting in TCP mode port: 5555\n")
    monkeypatch.setattr(gui, "list_devices", lambda: [])
    monkeypatch.setattr(gui.time, "sleep", lambda seconds: sleeps.append(seconds))

    gui.LogcatToolGUI.enable_wireless_adb(controller)

    assert sleeps == [0.5, 0.5, 0.5]
    assert "手机 IP:5555" in controller.status.last_error


def test_handle_wireless_adb_error_explains_usb_checks(monkeypatch) -> None:
    controller = make_controller()
    errors: list[tuple[str, str]] = []

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda title, message: errors.append((title, message)),
        ),
    )

    gui.LogcatToolGUI._handle_wireless_adb_error(
        controller,
        RuntimeError("device offline"),
    )

    assert errors == [
        (
            "USB 开启无线失败",
            "device offline\n\n"
            "请确认当前选择的是已授权 USB 调试的设备，并保持数据线连接稳定后再试。",
        )
    ]
    assert controller.status.last_error == errors[0][1]


def test_handle_wireless_adb_error_offers_to_switch_adb_path_for_launch_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("USB123")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    configure_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.configure_adb_path = lambda: configure_calls.append("configure")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("adb launch failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_wireless_adb_error(
        controller,
        RuntimeError("无法启动 adb：[WinError 6] 句柄无效。"),
    )

    assert prompts == [
        (
            "ADB 无法启动",
            "无法启动 adb：[WinError 6] 句柄无效。\n\n"
            "可直接点界面里的“ADB 路径”切换到外部 adb.exe；"
            "如果你在 Windows 7 / 8.0 上运行，请改用 Releases 里的 "
            "logcat-tool-for-win-legacy-win7.zip。\n\n"
            "是否现在切换 ADB 路径？",
        )
    ]
    assert configure_calls == ["configure"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]


def test_handle_wireless_adb_error_offers_to_restart_adb_for_local_service_failures(
    monkeypatch,
) -> None:
    controller = make_controller()
    stale_device = make_device("USB123")
    stale_label = gui.device_label(stale_device)
    prompts: list[tuple[str, str]] = []
    restart_calls: list[str] = []

    controller.devices = [stale_device]
    controller.device_var.set(stale_label)
    controller.device_combo["values"] = [stale_label]
    controller.status.active_device_serial = stale_device.serial
    controller.status.adb_ready = True
    controller.restart_adb = lambda: restart_calls.append("restart")

    monkeypatch.setattr(
        gui,
        "messagebox",
        SimpleNamespace(
            showwarning=lambda *args: None,
            showerror=lambda *args: (_ for _ in ()).throw(
                AssertionError("local adb service failures should use the recovery prompt")
            ),
            askyesno=lambda title, message: prompts.append((title, message)) or True,
        ),
    )

    gui.LogcatToolGUI._handle_wireless_adb_error(
        controller,
        RuntimeError("本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。"),
    )

    assert prompts == [
        (
            "ADB 服务异常",
            "本机 ADB 服务异常。可先点界面的“重启 ADB”，或手动执行 adb kill-server / adb start-server。\n\n"
            "可直接点界面里的“重启 ADB”尝试恢复。\n\n"
            "是否现在重启 ADB？",
        )
    ]
    assert restart_calls == ["restart"]
    assert controller.status.adb_ready is False
    assert controller.devices == [stale_device]
    assert controller.device_var.get() == stale_label
    assert controller.status.active_device_serial == stale_device.serial
    assert controller.status.last_error == prompts[0][1]
