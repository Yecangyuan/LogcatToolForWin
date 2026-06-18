import io
import queue

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


def test_parse_threadtime_line_extracts_fields() -> None:
    entry = parse_threadtime_line("06-18 12:00:00.000  1234  1235 E MyApp: crash")
    assert entry.level == "E"
    assert entry.tag == "MyApp"
    assert entry.message == "crash"


def test_parse_threadtime_line_returns_raw_fallback_for_unmatched_lines() -> None:
    entry = parse_threadtime_line("not a log line\n")
    assert entry.timestamp_text == ""
    assert entry.level == "I"
    assert entry.tag == "raw"
    assert entry.message == "not a log line"
    assert entry.raw_line == "not a log line"


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
