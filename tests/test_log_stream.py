import io
import queue
import subprocess
import threading

from logcat_tool_for_win.log_stream import LogcatSession, parse_threadtime_line


class FakePopen:
    def __init__(self) -> None:
        self.stdout = io.StringIO(
            "06-18 12:00:00.000  1234  1235 I MyApp: boot complete\n"
        )
        self.stderr = io.StringIO("device offline")
        self.returncode = 0
        self.terminated = False
        self.wait_timeout = None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeout = timeout
        return self.returncode


class StubbornPopen:
    def __init__(self) -> None:
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.terminated = False
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd=["adb", "logcat"], timeout=timeout)
        return 0


class RaisingFactory:
    def __call__(self, *args, **kwargs):
        raise RuntimeError("launch failed")


class BlockingStdout:
    def __init__(self, release_stderr: threading.Event) -> None:
        self.release_stderr = release_stderr
        self.state = 0

    def __iter__(self) -> "BlockingStdout":
        return self

    def __next__(self) -> str:
        if self.state == 0:
            self.state = 1
            return "06-18 12:00:00.000  1234  1235 I MyApp: boot complete\n"
        if self.state == 1:
            if not self.release_stderr.wait(timeout=0.2):
                raise RuntimeError("stderr was not drained while stdout was active")
            self.state = 2
            return "06-18 12:00:01.000  1234  1235 I MyApp: still running\n"
        raise StopIteration


class BlockingPopen:
    def __init__(self) -> None:
        self.release_stderr = threading.Event()
        self.stdout = BlockingStdout(self.release_stderr)
        self.stderr = io.StringIO("device offline")
        self.returncode = 0

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class DeferredStderrPopen:
    def __init__(self) -> None:
        self.release_stderr = threading.Event()
        self.stdout = BlockingStdout(self.release_stderr)
        self.stderr = self
        self.returncode = 0
        self.stderr_read = False

    def read(self) -> str:
        self.stderr_read = True
        self.release_stderr.set()
        return "device offline"

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class RaisingStdout:
    def __init__(self) -> None:
        self.state = 0

    def __iter__(self) -> "RaisingStdout":
        return self

    def __next__(self) -> str:
        if self.state == 0:
            self.state = 1
            return "06-18 12:00:00.000  1234  1235 I MyApp: boot complete\n"
        raise OSError("stdout read failed")


class StdoutErrorPopen:
    def __init__(self) -> None:
        self.stdout = RaisingStdout()
        self.stderr = io.StringIO("")
        self.returncode = 0

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class RaisingStderr:
    def read(self) -> str:
        raise OSError("stderr read failed")


class StderrErrorPopen:
    def __init__(self) -> None:
        self.stdout = io.StringIO("")
        self.stderr = RaisingStderr()
        self.returncode = 0

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def test_parse_threadtime_line_extracts_fields() -> None:
    entry = parse_threadtime_line("06-18 12:00:00.000  1234  1235 E MyApp: crash")
    assert entry.level == "E"
    assert entry.tag == "MyApp"
    assert entry.message == "crash"


def test_parse_threadtime_line_allows_colons_inside_tag() -> None:
    entry = parse_threadtime_line("06-18 12:00:00.000  1234  1235 I My:App: boot complete")

    assert entry.level == "I"
    assert entry.tag == "My:App"
    assert entry.message == "boot complete"


def test_parse_threadtime_line_returns_raw_fallback_for_unmatched_lines() -> None:
    entry = parse_threadtime_line("not a log line\n")
    assert entry.timestamp_text == ""
    assert entry.level == "I"
    assert entry.tag == "raw"
    assert entry.message == "not a log line"
    assert entry.raw_line == "not a log line"


def test_session_emits_no_started_event_when_launch_fails() -> None:
    events: queue.Queue = queue.Queue()
    session = LogcatSession(["adb", "logcat"], events, RaisingFactory())

    try:
        session.start()
    except RuntimeError:
        pass

    assert events.empty()


def test_session_does_not_inherit_invalid_gui_stdin() -> None:
    events: queue.Queue = queue.Queue()
    captured_kwargs = {}

    def popen_factory(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return FakePopen()

    session = LogcatSession(["adb", "logcat"], events, popen_factory)

    session.start()
    session.join()

    assert captured_kwargs["stdin"] == subprocess.DEVNULL


def test_session_hides_windows_adb_process_with_startupinfo(fake_windows_startupinfo) -> None:
    events: queue.Queue = queue.Queue()
    captured_kwargs = {}

    def popen_factory(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return FakePopen()

    session = LogcatSession(["adb", "logcat"], events, popen_factory)

    session.start()
    session.join()

    startupinfo = captured_kwargs["startupinfo"]
    assert captured_kwargs["creationflags"] == 0x08000000
    assert isinstance(startupinfo, fake_windows_startupinfo)
    assert startupinfo.dwFlags & 0x00000001
    assert startupinfo.wShowWindow == 0


def test_session_retries_invalid_windows_handle_without_startupinfo(fake_windows_startupinfo) -> None:
    events: queue.Queue = queue.Queue()
    captured_kwargs: list[dict[str, object]] = []

    def popen_factory(*args, **kwargs):
        captured_kwargs.append(kwargs)
        if len(captured_kwargs) < 3:
            exc = OSError("[WinError 6] 句柄无效。")
            exc.winerror = 6
            raise exc
        return FakePopen()

    session = LogcatSession(["adb", "logcat"], events, popen_factory)

    session.start()
    session.join()

    assert "startupinfo" in captured_kwargs[0]
    assert "startupinfo" in captured_kwargs[1]
    assert "startupinfo" not in captured_kwargs[2]
    assert "creationflags" not in captured_kwargs[2]


def test_session_stop_kills_process_when_terminate_times_out() -> None:
    events: queue.Queue = queue.Queue()
    process = StubbornPopen()
    session = LogcatSession(["adb", "logcat"], events, lambda *args, **kwargs: process)

    session.start()
    session.stop()

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_timeouts == [5, 5]


def test_session_drains_stderr_while_stdout_is_still_active() -> None:
    events: queue.Queue = queue.Queue()
    session = LogcatSession(["adb", "logcat"], events, lambda *args, **kwargs: DeferredStderrPopen())

    session.start()
    session.join()

    kinds = []
    messages = []
    while not events.empty():
        event = events.get()
        kinds.append(event.kind)
        messages.append(event.message)

    assert kinds == ["started", "line", "line", "stderr", "stopped"]
    assert messages[3] == "device offline"


def test_session_emits_stopped_event_when_stdout_read_fails() -> None:
    events: queue.Queue = queue.Queue()
    session = LogcatSession(["adb", "logcat"], events, lambda *args, **kwargs: StdoutErrorPopen())

    session.start()
    session.join()

    received = []
    while not events.empty():
        event = events.get()
        received.append((event.kind, event.message))

    assert received == [
        ("started", ""),
        ("line", ""),
        ("stderr", "stdout read failed"),
        ("stopped", ""),
    ]


def test_session_emits_stderr_event_when_stderr_read_fails() -> None:
    events: queue.Queue = queue.Queue()
    session = LogcatSession(["adb", "logcat"], events, lambda *args, **kwargs: StderrErrorPopen())

    session.start()
    session.join()

    received = []
    while not events.empty():
        event = events.get()
        received.append((event.kind, event.message))

    assert received == [
        ("started", ""),
        ("stderr", "stderr read failed"),
        ("stopped", ""),
    ]


def test_session_emits_started_line_and_stderr_events() -> None:
    events: queue.Queue = queue.Queue()
    session = LogcatSession(["adb", "logcat"], events, lambda *args, **kwargs: FakePopen())

    session.start()
    session.join()

    kinds = []
    while not events.empty():
        kinds.append(events.get().kind)

    assert kinds[:3] == ["started", "line", "stderr"]
    assert kinds[-1] == "stopped"
