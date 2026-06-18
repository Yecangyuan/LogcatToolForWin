from __future__ import annotations

from logcat_tool_for_win.models import FilterState, LogEntry

LEVEL_ORDER = ("V", "D", "I", "W", "E", "F")


def normalize_tag_filters(raw: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in raw.split(",") if item.strip()}))


def build_logcat_filter_spec(minimum_level: str, tag_filters: tuple[str, ...]) -> list[str]:
    level = minimum_level.upper()
    if tag_filters:
        return [f"{tag}:{level}" for tag in tag_filters] + ["*:S"]
    return [f"*:{level}"]


def entry_matches(entry: LogEntry, state: FilterState) -> bool:
    level_ok = LEVEL_ORDER.index(entry.level) >= LEVEL_ORDER.index(state.minimum_level)
    tag_ok = not state.tag_filters or entry.tag in state.tag_filters
    if not state.keyword:
        keyword_ok = True
    else:
        haystack = " ".join([entry.tag, entry.message, entry.raw_line]).lower()
        keyword_ok = state.keyword.lower() in haystack
    return level_ok and tag_ok and keyword_ok
