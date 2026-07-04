from __future__ import annotations

from typing import Type

import pytest


class FakeStartupInfo:
    def __init__(self) -> None:
        self.dwFlags = 0
        self.wShowWindow = None


@pytest.fixture
def fake_windows_startupinfo(monkeypatch: pytest.MonkeyPatch) -> Type[FakeStartupInfo]:
    monkeypatch.setattr("logcat_tool_for_win.adb._is_windows", lambda: True, raising=False)
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(
        "logcat_tool_for_win.adb.subprocess.STARTF_USESHOWWINDOW",
        0x00000001,
        raising=False,
    )
    monkeypatch.setattr("logcat_tool_for_win.adb.subprocess.SW_HIDE", 0, raising=False)
    monkeypatch.setattr(
        "logcat_tool_for_win.adb.subprocess.STARTUPINFO",
        FakeStartupInfo,
        raising=False,
    )
    return FakeStartupInfo
