from __future__ import annotations

from collections import deque
import queue
from types import SimpleNamespace

import logcat_tool_for_win.gui as gui
from logcat_tool_for_win.models import AppStatus, DeviceInfo, FilterState, LogEntry, StreamEvent


def test_build_summary_text_reports_total_and_visible_counts() -> None:
    assert gui.build_summary_text(120, 24, "streaming") == "Lines: 120 | Visible: 24 | State: streaming"


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
    assert "attempt 2" in text


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


class FailingSession:
    def stop(self) -> None:
        raise RuntimeError("stop failed")

    def join(self) -> None:
        return None


def make_device(serial: str, state: str = "device") -> DeviceInfo:
    return DeviceInfo(
        serial=serial,
        display_name=serial,
        transport="usb",
        state=state,
        model="Pixel",
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
    controller = gui.LogcatToolGUI.__new__(gui.LogcatToolGUI)
    controller.status = AppStatus()
    controller.manual_stop = False
    controller.events = queue.Queue()
    controller.root = DummyRoot()
    controller.status_var = DummyVar("")
    controller.summary_var = DummyVar("")
    controller.device_var = DummyVar("")
    controller.device_combo = DummyCombo()
    controller.raw_lines = deque()
    controller.visible_lines = deque()
    controller.filters = FilterState()
    controller.highlight_rules = []
    controller.auto_scroll_var = DummyVar(False)
    controller.session = None
    controller.devices = []
    return controller


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


def test_stop_stream_surfaces_stop_failures_instead_of_claiming_idle() -> None:
    controller = make_controller()
    controller.session = FailingSession()
    controller.status.stream_state = "streaming"
    controller.status.active_device_serial = "R58M12345"

    gui.LogcatToolGUI.stop_stream(controller)

    assert controller.status.stream_state == "failed"
    assert "stop failed" in controller.status.last_error


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
