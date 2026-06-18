from logcat_tool_for_win.filters import (
    build_logcat_filter_spec,
    entry_matches,
    normalize_tag_filters,
)
from logcat_tool_for_win.highlight import DEFAULT_LEVEL_COLORS, match_highlight_rules
from logcat_tool_for_win.models import FilterState, HighlightRule, LogEntry


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
    assert DEFAULT_LEVEL_COLORS["E"] == "#ff6b6b"


def test_normalize_tag_filters_removes_blanks_and_duplicates() -> None:
    assert normalize_tag_filters("MyApp, ActivityManager, MyApp , ") == (
        "ActivityManager",
        "MyApp",
    )
