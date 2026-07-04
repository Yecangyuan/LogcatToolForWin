from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from logcat_tool_for_win.models import FilterState, LogEntry

LEVEL_ORDER = ("V", "D", "I", "W", "E", "F")
LEVEL_RANKS = {level: rank for rank, level in enumerate(LEVEL_ORDER)}


def _normalize_level(level: str) -> str:
    return level.upper()


def _level_rank(level: str) -> Optional[int]:
    return LEVEL_RANKS.get(_normalize_level(level))


def normalize_tag_filters(raw: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in raw.split(",") if item.strip()}))


def build_logcat_filter_spec(minimum_level: str, tag_filters: tuple[str, ...]) -> list[str]:
    level = _normalize_level(minimum_level)
    if tag_filters:
        return [f"{tag}:{level}" for tag in tag_filters] + ["*:S"]
    return [f"*:{level}"]


@dataclass(frozen=True)
class PreparedFilterState:
    minimum_rank: Optional[int]
    tag_filter_set: frozenset[str]
    keyword_lower: str


def prepare_filter_state(state: FilterState) -> PreparedFilterState:
    return PreparedFilterState(
        minimum_rank=_level_rank(state.minimum_level),
        tag_filter_set=frozenset(state.tag_filters),
        keyword_lower=state.keyword.lower() if state.keyword else "",
    )


def entry_matches_prepared(entry: LogEntry, state: PreparedFilterState) -> bool:
    entry_level = _level_rank(entry.level)
    if entry_level is None or state.minimum_rank is None:
        return False

    level_ok = entry_level >= state.minimum_rank
    tag_ok = not state.tag_filter_set or entry.tag in state.tag_filter_set
    if not level_ok or not tag_ok:
        return False

    if not state.keyword_lower:
        return True

    haystack = " ".join([entry.tag, entry.message, entry.raw_line]).lower()
    return state.keyword_lower in haystack


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
