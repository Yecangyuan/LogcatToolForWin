from __future__ import annotations

import queue
import re
import subprocess
import threading
from typing import Callable

from logcat_tool_for_win.models import LogEntry, StreamEvent

THREADTIME_RE = re.compile(
    r"^(?P<stamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"\d+\s+\d+\s+(?P<level>[VDIWEF])\s+(?P<tag>[^:]+):\s(?P<message>.*)$"
)


def parse_threadtime_line(line: str) -> LogEntry:
    raw_line = line.rstrip("\n")
    match = THREADTIME_RE.match(raw_line)
    if match is None:
        return LogEntry(
            timestamp_text="",
            level="I",
            tag="raw",
            message=raw_line,
            raw_line=raw_line,
        )
    return LogEntry(
        timestamp_text=match.group("stamp"),
        level=match.group("level"),
        tag=match.group("tag").strip(),
        message=match.group("message"),
        raw_line=raw_line,
    )


class LogcatSession:
    def __init__(
        self,
        command: list[str],
        events: queue.Queue[StreamEvent],
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.command = command
        self.events = events
        self.popen_factory = popen_factory
        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None

    def start(self) -> None:
        self.process = self.popen_factory(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.events.put(StreamEvent(kind="started"))
        self.worker = threading.Thread(target=self._pump, daemon=True)
        self.worker.start()

    def _pump(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        stderr_text: list[str] = []

        def _drain_stderr() -> None:
            stderr = self.process.stderr
            if stderr is None:
                return
            text = stderr.read()
            if text:
                stderr_text.append(text.rstrip("\n"))

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        for line in self.process.stdout:
            self.events.put(StreamEvent(kind="line", entry=parse_threadtime_line(line)))

        stderr_thread.join()
        if stderr_text:
            self.events.put(StreamEvent(kind="stderr", message=stderr_text[0]))

        self.events.put(StreamEvent(kind="stopped"))

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        self.process.wait(timeout=5)

    def join(self) -> None:
        if self.worker is not None:
            self.worker.join(timeout=2)
