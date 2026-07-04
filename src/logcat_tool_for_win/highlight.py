from __future__ import annotations

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
            pattern = rule.pattern.lower()
        if pattern in source:
            matches.append(rule.name)
    return tuple(matches)
