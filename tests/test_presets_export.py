from pathlib import Path

from logcat_tool_for_win.export import export_lines
from logcat_tool_for_win.models import FilterState, HighlightRule
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
    )
    save_preset(presets_file, "Warnings", FilterState(minimum_level="W", keyword="slow"))

    presets = load_presets(presets_file)

    assert sorted(presets) == ["Errors", "Warnings"]
    assert presets["Errors"].minimum_level == "E"
    assert presets["Warnings"].keyword == "slow"


def test_export_lines_writes_text_file(tmp_path: Path) -> None:
    output = tmp_path / "logs.txt"
    export_lines(output, ["line one", "line two"])
    assert output.read_text(encoding="utf-8").splitlines() == ["line one", "line two"]
