from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = "LogcatToolForWin"
RAW_LOG_CAP = 20_000
VISIBLE_LOG_CAP = 5_000
QUEUE_DRAIN_MS = 100


def get_config_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    path = root / APP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_state_file() -> Path:
    return get_config_dir() / "state.json"


def get_presets_file() -> Path:
    return get_config_dir() / "presets.json"
