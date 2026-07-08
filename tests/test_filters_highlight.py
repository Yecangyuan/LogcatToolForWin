from logcat_tool_for_win import filters as filters_module
from logcat_tool_for_win.filters import (
    build_logcat_filter_spec,
    entry_matches,
    normalize_tag_filters,
)
from logcat_tool_for_win.highlight import (
    DEFAULT_LEVEL_COLORS,
    build_highlight_rule_cache_key,
    match_highlight_rules,
)
from logcat_tool_for_win.models import FilterState, HighlightRule, LogEntry


class LowerCountingStr(str):
    def __new__(cls, value: str) -> "LowerCountingStr":
        instance = super().__new__(cls, value)
        instance.lower_calls = 0
        return instance

    def lower(self) -> str:
        self.lower_calls += 1
        return super().lower()


class ContainsCountingStr(str):
    def __new__(cls, value: str) -> "ContainsCountingStr":
        instance = super().__new__(cls, value)
        instance.contains_calls = 0
        return instance

    def __contains__(self, item: object) -> bool:
        self.contains_calls += 1
        return super().__contains__(item)


class ExplodingIndexLevelOrder(tuple):
    def index(self, value: object, start: int = 0, stop: int = 9223372036854775807) -> int:
        raise AssertionError("LEVEL_ORDER.index should not be used in the log filter hot path")


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


def test_entry_matches_uses_constant_level_rank_lookup(monkeypatch) -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(minimum_level="W", tag_filters=("MyApp",))
    monkeypatch.setattr(
        filters_module,
        "LEVEL_ORDER",
        ExplodingIndexLevelOrder(("V", "D", "I", "W", "E", "F")),
    )

    assert entry_matches(entry, state) is True


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


def test_entry_matches_populates_lowered_search_text_on_first_keyword_match() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
    )
    state = FilterState(minimum_level="W", tag_filters=("MyApp",), keyword="crash")

    assert entry_matches(entry, state) is True
    assert entry.lowered_search_text == "myapp fatal crash happened raw fatal crash happened"


def test_entry_matches_prepared_reuses_cached_lowered_search_text_when_present() -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="E",
        tag="MyApp",
        message="fatal crash happened",
        raw_line="raw fatal crash happened",
        lowered_search_text="cached only token",
    )
    prepared = filters_module.prepare_filter_state(FilterState(minimum_level="W", keyword="token"))

    assert filters_module.entry_matches_prepared(entry, prepared) is True


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


def test_match_highlight_rules_reuses_lowered_insensitive_pattern_across_entries() -> None:
    pattern = LowerCountingStr("UNIQUE_CRASH_PATTERN_CACHE")
    rules = [HighlightRule(name="crash", pattern=pattern, foreground="#ff6b6b")]

    for index in range(3):
        entry = LogEntry(
            timestamp_text="06-18 10:00:00.000",
            level="E",
            tag="MyApp",
            message=f"unique_crash_pattern_cache happened {index}",
            raw_line=f"unique_crash_pattern_cache happened {index}",
        )
        assert match_highlight_rules(entry, rules) == ("crash",)

    assert pattern.lower_calls == 1


def test_match_highlight_rules_reuses_lowered_raw_line_across_repeated_matches() -> None:
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
    ]

    assert match_highlight_rules(entry, rules) == ("ANR", "crash")
    assert match_highlight_rules(entry, rules) == ("ANR", "crash")
    assert raw_line.lower_calls == 1


def test_match_highlight_rules_reuses_empty_lowered_raw_line_across_rule_changes() -> None:
    raw_line = LowerCountingStr("")
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="",
        raw_line=raw_line,
    )
    first_rules = [
        HighlightRule(name="ANR", pattern="anr", foreground="#ffcc00"),
    ]
    second_rules = [
        HighlightRule(name="crash", pattern="CRASH", foreground="#ff6b6b"),
    ]

    assert match_highlight_rules(entry, first_rules) == ()
    assert match_highlight_rules(entry, second_rules) == ()
    assert raw_line.lower_calls == 1


def test_match_highlight_rules_reuses_cached_matches_for_same_rule_set() -> None:
    raw_line = ContainsCountingStr("ANR detected")
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="ANR detected",
        raw_line=raw_line,
    )
    rules = [
        HighlightRule(
            name="ANR",
            pattern="ANR",
            foreground="#ffcc00",
            case_sensitive=True,
        )
    ]

    assert match_highlight_rules(entry, rules) == ("ANR",)
    assert match_highlight_rules(entry, rules) == ("ANR",)
    assert raw_line.contains_calls == 1


def test_match_highlight_rules_invalidates_cache_when_rule_set_changes() -> None:
    raw_line = ContainsCountingStr("ANR detected")
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="ANR detected",
        raw_line=raw_line,
    )
    first_rules = [
        HighlightRule(
            name="ANR",
            pattern="ANR",
            foreground="#ffcc00",
            case_sensitive=True,
        )
    ]
    second_rules = [
        HighlightRule(
            name="detected",
            pattern="detected",
            foreground="#ffaa00",
            case_sensitive=True,
        )
    ]

    assert match_highlight_rules(entry, first_rules) == ("ANR",)
    assert match_highlight_rules(entry, second_rules) == ("detected",)
    assert raw_line.contains_calls == 2


def test_match_highlight_rules_reuses_explicit_rule_cache_key_without_rebuilding(monkeypatch) -> None:
    entry = LogEntry(
        timestamp_text="06-18 10:00:00.000",
        level="W",
        tag="ActivityManager",
        message="ANR detected",
        raw_line="ANR detected",
    )
    rules = [HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00")]
    rule_cache_key = build_highlight_rule_cache_key(rules)

    monkeypatch.setattr(
        "logcat_tool_for_win.highlight._rules_cache_key",
        lambda _rules: (_ for _ in ()).throw(
            AssertionError("explicit rule cache key should skip rebuilding")
        ),
    )

    assert match_highlight_rules(entry, rules, rule_cache_key=rule_cache_key) == ("ANR",)


def test_default_level_colors_include_error_red() -> None:
    assert DEFAULT_LEVEL_COLORS["E"] == "#ff6b6b"


def test_normalize_tag_filters_removes_blanks_and_duplicates() -> None:
    assert normalize_tag_filters("MyApp, ActivityManager, MyApp , ") == (
        "ActivityManager",
        "MyApp",
    )
