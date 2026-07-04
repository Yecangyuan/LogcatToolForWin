from __future__ import annotations

from functools import lru_cache
from typing import Optional

from logcat_tool_for_win.models import HighlightRule, LogEntry

DEFAULT_LEVEL_COLORS = {
    "V": "#94a3b8",
    "D": "#60a5fa",
    "I": "#22c55e",
    "W": "#facc15",
    "E": "#ff6b6b",
    "F": "#ff3b30",
}


@lru_cache(maxsize=512)
def _lower_pattern(pattern: str) -> str:
    return pattern.lower()


def match_highlight_rules(entry: LogEntry, rules: list[HighlightRule]) -> tuple[str, ...]:
    matches: list[str] = []
    lowered_raw_line: Optional[str] = None
    for rule in rules:
        if not rule.pattern:
            continue
        if rule.case_sensitive:
            source = entry.raw_line
            pattern = rule.pattern
        else:
            if lowered_raw_line is None:
                lowered_raw_line = entry.raw_line.lower()
            source = lowered_raw_line
            pattern = _lower_pattern(rule.pattern)
        if pattern in source:
            matches.append(rule.name)
    return tuple(matches)
