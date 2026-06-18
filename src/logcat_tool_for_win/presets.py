from __future__ import annotations

import json
from pathlib import Path

from logcat_tool_for_win.models import FilterState, HighlightRule


def _filters_to_payload(filters: FilterState) -> dict[str, object]:
    return {
        "minimum_level": filters.minimum_level,
        "tag_filters": list(filters.tag_filters),
        "keyword": filters.keyword,
        "match_only": filters.match_only,
        "auto_scroll": filters.auto_scroll,
    }


def _filters_from_payload(payload: dict[str, object]) -> FilterState:
    return FilterState(
        minimum_level=str(payload.get("minimum_level", "V")),
        tag_filters=tuple(payload.get("tag_filters", [])),
        keyword=str(payload.get("keyword", "")),
        match_only=bool(payload.get("match_only", False)),
        auto_scroll=bool(payload.get("auto_scroll", True)),
    )


def save_preset(path: Path, name: str, filters: FilterState) -> None:
    presets = load_presets(path)
    presets[name] = filters
    payload = {preset_name: _filters_to_payload(state) for preset_name, state in presets.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_presets(path: Path) -> dict[str, FilterState]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {name: _filters_from_payload(state) for name, state in payload.items()}


def save_state(
    path: Path,
    filters: FilterState,
    rules: list[HighlightRule],
    recent_target: str,
) -> None:
    payload = {
        "filters": _filters_to_payload(filters),
        "highlight_rules": [
            {
                "name": rule.name,
                "pattern": rule.pattern,
                "foreground": rule.foreground,
                "background": rule.background,
                "case_sensitive": rule.case_sensitive,
            }
            for rule in rules
        ],
        "recent_target": recent_target,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_state(path: Path) -> tuple[FilterState, list[HighlightRule], str]:
    if not path.exists():
        return FilterState(), [], ""

    payload = json.loads(path.read_text(encoding="utf-8"))
    filters = _filters_from_payload(payload.get("filters", {}))
    rules = [HighlightRule(**item) for item in payload.get("highlight_rules", [])]
    recent_target = payload.get("recent_target", "")
    return filters, rules, recent_target
