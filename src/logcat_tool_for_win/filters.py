from __future__ import annotations

from typing import Optional

from logcat_tool_for_win.models import FilterState, LogEntry

LEVEL_ORDER = ("V", "D", "I", "W", "E", "F")


def _normalize_level(level: str) -> str:
    return level.upper()


def _level_rank(level: str) -> Optional[int]:
    normalized = _normalize_level(level)
    if normalized not in LEVEL_ORDER:
        return None
    return LEVEL_ORDER.index(normalized)


def normalize_tag_filters(raw: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in raw.split(",") if item.strip()}))


def build_logcat_filter_spec(minimum_level: str, tag_filters: tuple[str, ...]) -> list[str]:
    level = _normalize_level(minimum_level)
    if tag_filters:
        return [f"{tag}:{level}" for tag in tag_filters] + ["*:S"]
    return [f"*:{level}"]


def entry_matches(entry: LogEntry, state: FilterState) -> bool:
    entry_level = _level_rank(entry.level)
    minimum_level = _level_rank(state.minimum_level)
    if entry_level is None or minimum_level is None:
        return False

    level_ok = entry_level >= minimum_level
    tag_ok = not state.tag_filters or entry.tag in state.tag_filters
    if not level_ok or not tag_ok:
        return False

    if not state.keyword:
        return True

    haystack = " ".join([entry.tag, entry.message, entry.raw_line]).lower()
    return state.keyword.lower() in haystack
