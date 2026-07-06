import json
from pathlib import Path

import pytest

from logcat_tool_for_win.export import export_lines
from logcat_tool_for_win.models import FilterState, HighlightRule, NamedPreset
from logcat_tool_for_win.presets import load_presets, load_state, save_preset, save_state


def test_save_and_load_state_round_trip(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    filters = FilterState(
        minimum_level="W",
        tag_filters=("MyApp",),
        keyword="crash",
        match_only=True,
    )
    rules = [HighlightRule(name="Crash", pattern="crash", foreground="#ff6b6b")]

    save_state(state_file, filters, rules, "10.0.0.5:5555")
    loaded_filters, loaded_rules, recent_target = load_state(state_file)

    assert loaded_filters.minimum_level == "W"
    assert loaded_filters.tag_filters == ("MyApp",)
    assert loaded_rules[0].name == "Crash"
    assert recent_target == "10.0.0.5:5555"


def test_save_and_load_named_presets_round_trip(tmp_path: Path) -> None:
    presets_file = tmp_path / "presets.json"
    save_preset(
        presets_file,
        "Errors",
        FilterState(minimum_level="E", tag_filters=("MyApp",)),
        [HighlightRule(name="Crash", pattern="crash", foreground="#ff6b6b")],
    )
    save_preset(
        presets_file,
        "Warnings",
        FilterState(minimum_level="W", keyword="slow"),
        [HighlightRule(name="ANR", pattern="ANR", foreground="#ffcc00")],
    )

    presets = load_presets(presets_file)

    assert sorted(presets) == ["Errors", "Warnings"]
    assert presets["Errors"].filters.minimum_level == "E"
    assert presets["Errors"].highlight_patterns == ("crash",)
    assert presets["Warnings"].filters.keyword == "slow"
    assert presets["Warnings"].highlight_patterns == ("ANR",)


def test_load_presets_returns_empty_dict_for_bad_payload(tmp_path: Path) -> None:
    presets_file = tmp_path / "presets.json"
    presets_file.write_text(json.dumps(["invalid"]), encoding="utf-8")

    assert load_presets(presets_file) == {}


def test_load_presets_normalizes_loaded_filter_values(tmp_path: Path) -> None:
    presets_file = tmp_path / "presets.json"
    presets_file.write_text(
        json.dumps(
            {
                "Noisy": {
                    "filters": {
                        "minimum_level": "?",
                        "tag_filters": ["", " MyApp ", "MyApp", None, "ActivityManager"],
                        "keyword": "crash",
                    },
                    "highlight_patterns": ["", " ANR ", "ANR", None, "crash"],
                }
            }
        ),
        encoding="utf-8",
    )

    presets = load_presets(presets_file)

    assert presets["Noisy"].filters.minimum_level == "V"
    assert presets["Noisy"].filters.tag_filters == ("ActivityManager", "MyApp")
    assert presets["Noisy"].highlight_patterns == ("ANR", "crash")


def test_load_presets_supports_legacy_filter_only_payloads(tmp_path: Path) -> None:
    presets_file = tmp_path / "presets.json"
    presets_file.write_text(
        json.dumps(
            {
                "Legacy": {
                    "minimum_level": "E",
                    "tag_filters": ["MyApp"],
                    "keyword": "fatal",
                }
            }
        ),
        encoding="utf-8",
    )

    presets = load_presets(presets_file)

    assert presets == {
        "Legacy": NamedPreset(
            filters=FilterState(minimum_level="E", tag_filters=("MyApp",), keyword="fatal"),
            highlight_patterns=(),
        )
    }


def test_load_state_returns_defaults_for_bad_payload(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{not-json", encoding="utf-8")

    loaded_filters, loaded_rules, recent_target = load_state(state_file)

    assert loaded_filters == FilterState()
    assert loaded_rules == []
    assert recent_target == ""


def test_load_state_skips_invalid_highlight_rules(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "filters": {"minimum_level": "I", "match_only": "false"},
                "highlight_rules": [
                    {"name": "Good", "pattern": "ok", "foreground": "#00ff00"},
                    {"name": "MissingForeground", "pattern": "oops"},
                    "bad-entry",
                ],
                "recent_target": 1234,
            }
        ),
        encoding="utf-8",
    )

    loaded_filters, loaded_rules, recent_target = load_state(state_file)

    assert loaded_filters.minimum_level == "I"
    assert loaded_filters.match_only is False
    assert [rule.name for rule in loaded_rules] == ["Good"]
    assert recent_target == ""


def test_load_state_normalizes_loaded_filter_values(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "filters": {
                    "minimum_level": "?",
                    "tag_filters": ["", " MyApp ", "MyApp", None, "ActivityManager"],
                },
                "highlight_rules": [],
                "recent_target": "192.168.1.111:5555",
            }
        ),
        encoding="utf-8",
    )

    loaded_filters, _loaded_rules, recent_target = load_state(state_file)

    assert loaded_filters.minimum_level == "V"
    assert loaded_filters.tag_filters == ("ActivityManager", "MyApp")
    assert recent_target == "192.168.1.111:5555"


def test_export_lines_writes_text_file_and_creates_parent_directories(tmp_path: Path) -> None:
    output = tmp_path / "exports" / "device-a" / "logs.txt"
    export_lines(output, ["line one", "line two"])
    assert output.read_text(encoding="utf-8").splitlines() == ["line one", "line two"]


def test_export_lines_rejects_empty_exports(tmp_path: Path) -> None:
    output = tmp_path / "logs.txt"

    with pytest.raises(ValueError, match="没有可导出的日志。"):
        export_lines(output, [])
