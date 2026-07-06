from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logcat_tool_for_win.adb import normalize_tcp_target
from logcat_tool_for_win.filters import LEVEL_ORDER, normalize_tag_filters
from logcat_tool_for_win.models import FilterState, HighlightRule, NamedPreset


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


def _coerce_minimum_level(value: object) -> str:
    if not isinstance(value, str):
        return "V"
    level = value.upper()
    return level if level in LEVEL_ORDER else "V"


def _coerce_tag_filters(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return normalize_tag_filters(",".join(str(item) for item in value if item is not None))


def _filters_from_payload(payload: object) -> FilterState:
    if not isinstance(payload, dict):
        return FilterState()

    keyword = payload.get("keyword", "")
    return FilterState(
        minimum_level=_coerce_minimum_level(payload.get("minimum_level", "V")),
        tag_filters=_coerce_tag_filters(payload.get("tag_filters", [])),
        keyword=keyword if isinstance(keyword, str) else "",
        match_only=_coerce_bool(payload.get("match_only", False), False),
        auto_scroll=_coerce_bool(payload.get("auto_scroll", True), True),
    )


def _normalize_highlight_patterns(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()

    patterns: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        pattern = item.strip()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return tuple(patterns)


def _coerce_recent_target(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return normalize_tcp_target(value)
    except ValueError:
        return ""


def _normalize_recent_targets(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    recent_targets: list[str] = []
    seen: set[str] = set()
    for item in value:
        target = _coerce_recent_target(item)
        if not target or target in seen:
            continue
        seen.add(target)
        recent_targets.append(target)
    return recent_targets


def _merge_recent_targets(recent_target: str, recent_targets: object) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for target in (_coerce_recent_target(recent_target), *_normalize_recent_targets(recent_targets)):
        if not target or target in seen:
            continue
        seen.add(target)
        merged.append(target)
    return merged


def _named_preset_to_payload(preset: NamedPreset) -> dict[str, object]:
    return {
        "filters": _filters_to_payload(preset.filters),
        "highlight_patterns": list(preset.highlight_patterns),
    }


def _named_preset_from_payload(payload: object) -> NamedPreset:
    if not isinstance(payload, dict):
        return NamedPreset(filters=FilterState())

    filters_payload = payload.get("filters")
    if isinstance(filters_payload, dict):
        return NamedPreset(
            filters=_filters_from_payload(filters_payload),
            highlight_patterns=_normalize_highlight_patterns(payload.get("highlight_patterns", [])),
        )

    return NamedPreset(
        filters=_filters_from_payload(payload),
        highlight_patterns=(),
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


def save_preset(
    path: Path,
    name: str,
    filters: FilterState,
    highlight_rules: list[HighlightRule],
) -> None:
    presets = load_presets(path)
    presets[name] = NamedPreset(
        filters=filters,
        highlight_patterns=_normalize_highlight_patterns(
            [rule.pattern for rule in highlight_rules]
        ),
    )
    payload = {
        preset_name: _named_preset_to_payload(preset)
        for preset_name, preset in presets.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_presets(path: Path) -> dict[str, NamedPreset]:
    if not path.exists():
        return {}
    try:
        payload = _read_json_object(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return {name: _named_preset_from_payload(state) for name, state in payload.items()}


def save_state(
    path: Path,
    filters: FilterState,
    rules: list[HighlightRule],
    recent_target: str,
    recent_targets: object = (),
) -> None:
    merged_recent_targets = _merge_recent_targets(recent_target, recent_targets)
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
        "recent_target": merged_recent_targets[0] if merged_recent_targets else "",
        "recent_targets": merged_recent_targets,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_state(path: Path) -> tuple[FilterState, list[HighlightRule], str, list[str]]:
    if not path.exists():
        return FilterState(), [], "", []

    try:
        payload = _read_json_object(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return FilterState(), [], "", []

    filters = _filters_from_payload(payload.get("filters", {}))
    rules = _highlight_rules_from_payload(payload.get("highlight_rules", []))
    recent_targets = _merge_recent_targets(
        payload.get("recent_target", ""),
        payload.get("recent_targets", []),
    )
    recent_target = recent_targets[0] if recent_targets else ""
    return filters, rules, recent_target, recent_targets
