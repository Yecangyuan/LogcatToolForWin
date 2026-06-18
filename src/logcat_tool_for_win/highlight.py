from __future__ import annotations

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
    for rule in rules:
        source = entry.raw_line if rule.case_sensitive else entry.raw_line.lower()
        pattern = rule.pattern if rule.case_sensitive else rule.pattern.lower()
        if pattern and pattern in source:
            matches.append(rule.name)
    return tuple(matches)
