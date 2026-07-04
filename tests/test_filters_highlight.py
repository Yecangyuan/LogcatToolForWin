from logcat_tool_for_win.filters import (
    build_logcat_filter_spec,
    entry_matches,
    normalize_tag_filters,
)
from logcat_tool_for_win.highlight import DEFAULT_LEVEL_COLORS, match_highlight_rules
from logcat_tool_for_win.models import FilterState, HighlightRule, LogEntry


class LowerCountingStr(str):
    def __new__(cls, value: str) -> "LowerCountingStr":
        instance = super().__new__(cls, value)
        instance.lower_calls = 0
        return instance

    def lower(self) -> str:
        self.lower_calls += 1
        return super().lower()


def test_build_logcat_filter_spec_uses_exact_tag_filters() -> None:
    assert build_logcat_filter_spec("I", ("ActivityManager", "MyApp")) == [
        "ActivityManager:I",
        "MyApp:I",
        "*:S",
    ]


def test_entry_matches_applies_keyword_and_level() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(
        minimum_level="W",
        tag_filters=("MyApp",),
        keyword="crash",
        match_only=True,
    )

    assert entry_matches(entry, state) is True


def test_entry_matches_normalizes_lowercase_levels() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="e",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(minimum_level="w", tag_filters=("MyApp",), keyword="crash")

    assert entry_matches(entry, state) is True


def test_entry_matches_rejects_unknown_entry_level_without_crashing() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="?",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(minimum_level="W", tag_filters=("MyApp",), keyword="crash")

    assert entry_matches(entry, state) is False


def test_entry_matches_rejects_unknown_minimum_level_without_crashing() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(minimum_level="?", tag_filters=("MyApp",), keyword="crash")

    assert entry_matches(entry, state) is False


def test_entry_matches_skips_keyword_work_when_level_or_tag_rejects_entry() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="D",
        tag="OtherApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    keyword = LowerCountingStr("crash")
    state = FilterState(minimum_level="E", tag_filters=("MyApp",), keyword=keyword)

    assert entry_matches(entry, state) is False
    assert keyword.lower_calls == 0


def test_match_highlight_rules_returns_matching_names() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="ANR detected",
        raw_line="ANR detected",
    )
    rules = [HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00")]

    assert "ANR" in match_highlight_rules(entry, rules)


def test_match_highlight_rules_lowers_raw_line_once_for_insensitive_rules() -> None:
    raw_line = LowerCountingStr("ANR detected and crash happened")
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="ANR detected",
        raw_line=raw_line,
    )
    rules = [
        HighlightRule(name="ANR", pattern="anr", foreground="#ffcc00"),
        HighlightRule(name="crash", pattern="CRASH", foreground="#ff6b6b"),
        HighlightRule(name="exact", pattern="ANR detected", foreground="#ffffff", case_sensitive=True),
    ]

    assert match_highlight_rules(entry, rules) == ("ANR", "crash", "exact")
    assert raw_line.lower_calls == 1


def test_default_level_colors_include_error_red() -> None:
    assert DEFAULT_LEVEL_COLORS["E"] == "#ff6b6b"


def test_normalize_tag_filters_removes_blanks_and_duplicates() -> None:
    assert normalize_tag_filters("MyApp, ActivityManager, MyApp , ") == (
        "ActivityManager",
        "MyApp",
    )
