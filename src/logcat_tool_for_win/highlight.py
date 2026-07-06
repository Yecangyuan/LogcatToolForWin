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


def _rules_cache_key(rules: list[HighlightRule]) -> tuple[tuple[str, str, bool], ...]:
    return tuple((rule.name, rule.pattern, rule.case_sensitive) for rule in rules)


def match_highlight_rules(entry: LogEntry, rules: list[HighlightRule]) -> tuple[str, ...]:
    cache_key = _rules_cache_key(rules)
    if entry.highlight_match_cache_key == cache_key:
        return entry.cached_highlight_keys

    matches: list[str] = []
    lowered_raw_line: Optional[str] = entry.lowered_raw_line or None
    for rule in rules:
        if not rule.pattern:
            continue
        if rule.case_sensitive:
            source = entry.raw_line
            pattern = rule.pattern
        else:
            if lowered_raw_line is None:
                lowered_raw_line = entry.raw_line.lower()
                entry.lowered_raw_line = lowered_raw_line
            source = lowered_raw_line
            pattern = _lower_pattern(rule.pattern)
        if pattern in source:
            matches.append(rule.name)
    entry.highlight_match_cache_key = cache_key
    entry.cached_highlight_keys = tuple(matches)
    return entry.cached_highlight_keys
