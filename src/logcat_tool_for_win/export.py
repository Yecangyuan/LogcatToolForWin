from __future__ import annotations

from pathlib import Path


def export_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        raise ValueError("没有可导出的日志。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
