from __future__ import annotations

import queue
import re
import subprocess
import threading
from typing import Callable, Optional

from logcat_tool_for_win.adb import _is_invalid_windows_handle, iter_adb_process_kwargs
from logcat_tool_for_win.models import LogEntry, StreamEvent

THREADTIME_RE = re.compile(
    r"^(?P<stamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"\d+\s+\d+\s+(?P<level>[VDIWEF])\s+(?P<tag>.+?):\s(?P<message>.*)$"
)


def parse_threadtime_line(line: str) -> LogEntry:
    raw_line = line.rstrip("\r\n")
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
        stderr_join_timeout: float = 2.0,
    ) -> None:
        self.command = command
        self.events = events
        self.popen_factory = popen_factory
        self.stderr_join_timeout = stderr_join_timeout
        self.process: Optional[subprocess.Popen[str]] = None
        self.worker: Optional[threading.Thread] = None

    def start(self) -> None:
        launch_kwargs = list(iter_adb_process_kwargs(bufsize=1))
        for attempt_index, process_kwargs in enumerate(launch_kwargs):
            try:
                self.process = self.popen_factory(self.command, **process_kwargs)
                break
            except OSError as exc:
                if attempt_index + 1 < len(launch_kwargs) and _is_invalid_windows_handle(exc):
                    continue
                raise
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
            try:
                text = stderr.read()
            except Exception as exc:
                stderr_text.append(str(exc) or exc.__class__.__name__)
                return
            if text:
                stderr_text.append(text.rstrip("\n"))

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        pump_error = ""
        try:
            for line in self.process.stdout:
                self.events.put(StreamEvent(kind="line", entry=parse_threadtime_line(line)))
        except Exception as exc:
            pump_error = str(exc) or exc.__class__.__name__
        finally:
            stderr_thread.join(timeout=self.stderr_join_timeout)
            stderr_messages = [message for message in stderr_text if message]
            if stderr_thread.is_alive():
                stderr_messages.append("stderr read timed out")
            if pump_error:
                stderr_messages.append(pump_error)
            if stderr_messages:
                self.events.put(StreamEvent(kind="stderr", message="\n".join(stderr_messages)))

            self.events.put(StreamEvent(kind="stopped"))

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            self.process.terminate()
        except OSError as exc:
            if _is_invalid_windows_handle(exc):
                return
            raise
        try:
            self.process.wait(timeout=5)
        except OSError as exc:
            if _is_invalid_windows_handle(exc):
                return
            raise
        except subprocess.TimeoutExpired:
            try:
                self.process.kill()
            except OSError as exc:
                if _is_invalid_windows_handle(exc):
                    return
                raise
            try:
                self.process.wait(timeout=5)
            except OSError as exc:
                if _is_invalid_windows_handle(exc):
                    return
                raise

    def join(self) -> None:
        if self.worker is not None:
            self.worker.join(timeout=2)
