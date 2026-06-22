from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class DeviceInfo:
    serial: str
    display_name: str
    transport: str
    state: str
    model: str
    product: str
    raw_descriptor: str


@dataclass(slots=True)
class FilterState:
    minimum_level: str = "V"
    tag_filters: tuple[str, ...] = ()
    keyword: str = ""
    match_only: bool = False
    auto_scroll: bool = True


@dataclass(slots=True)
class HighlightRule:
    name: str
    pattern: str
    foreground: str
    background: str = ""
    case_sensitive: bool = False


@dataclass(slots=True)
class LogEntry:
    timestamp_text: str
    level: str
    tag: str
    message: str
    raw_line: str
    matches_filters: bool = True
    highlight_keys: tuple[str, ...] = ()


@dataclass(slots=True)
class AppStatus:
    adb_ready: bool = False
    active_device_serial: str = ""
    stream_state: str = "idle"
    queue_depth: int = 0
    last_error: str = ""
    reconnect_attempt: int = 0


@dataclass(slots=True)
class StreamEvent:
    kind: str
    entry: Optional[LogEntry] = None
    message: str = ""
