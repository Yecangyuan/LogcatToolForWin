from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logcat_tool_for_win.models import FilterState, HighlightRule


def _filters_to_payload(filters: FilterState) -> dict[str, object]:
    return {
        "minimum_level": filters.minimum_level,
        "tag_filters": list(filters.tag_filters),
        "keyword": filters.keyword,
        "match_only": filters.match_only,
        "auto_scroll": filters.auto_scroll,
    }


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _filters_from_payload(payload: object) -> FilterState:
    if not isinstance(payload, dict):
        return FilterState()

    raw_tags = payload.get("tag_filters", [])
    tag_filters: tuple[str, ...] = ()
    if isinstance(raw_tags, (list, tuple)):
        tag_filters = tuple(str(item) for item in raw_tags if item is not None)

    minimum_level = payload.get("minimum_level", "V")
    keyword = payload.get("keyword", "")
    return FilterState(
        minimum_level=minimum_level if isinstance(minimum_level, str) and minimum_level else "V",
        tag_filters=tag_filters,
        keyword=keyword if isinstance(keyword, str) else "",
        match_only=_coerce_bool(payload.get("match_only", False), False),
        auto_scroll=_coerce_bool(payload.get("auto_scroll", True), True),
    )


def _highlight_rules_from_payload(payload: object) -> list[HighlightRule]:
    if not isinstance(payload, list):
        return []

    rules: list[HighlightRule] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        pattern = item.get("pattern")
        foreground = item.get("foreground")
        background = item.get("background", "")

        if not isinstance(name, str) or not name:
            continue
        if not isinstance(pattern, str) or not pattern:
            continue
        if not isinstance(foreground, str) or not foreground:
            continue
        if not isinstance(background, str):
            background = ""

        rules.append(
            HighlightRule(
                name=name,
                pattern=pattern,
                foreground=foreground,
                background=background,
                case_sensitive=_coerce_bool(item.get("case_sensitive", False), False),
            )
        )

    return rules


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object payload.")
    return payload


def save_preset(path: Path, name: str, filters: FilterState) -> None:
    presets = load_presets(path)
    presets[name] = filters
    payload = {preset_name: _filters_to_payload(state) for preset_name, state in presets.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_presets(path: Path) -> dict[str, FilterState]:
    if not path.exists():
        return {}
    try:
        payload = _read_json_object(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
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

    try:
        payload = _read_json_object(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return FilterState(), [], ""

    filters = _filters_from_payload(payload.get("filters", {}))
    rules = _highlight_rules_from_payload(payload.get("highlight_rules", []))
    recent_target = payload.get("recent_target", "")
    if not isinstance(recent_target, str):
        recent_target = ""
    return filters, rules, recent_target
