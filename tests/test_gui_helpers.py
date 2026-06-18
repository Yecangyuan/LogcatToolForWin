from logcat_tool_for_win.gui import (
    build_highlight_rules,
    build_summary_text,
    format_status_text,
)
from logcat_tool_for_win.models import AppStatus


def test_build_summary_text_reports_total_and_visible_counts() -> None:
    assert build_summary_text(120, 24, "streaming") == "Lines: 120 | Visible: 24 | State: streaming"


def test_format_status_text_includes_reconnect_attempt() -> None:
    status = AppStatus(
        adb_ready=True,
        active_device_serial="R58M12345",
        stream_state="reconnecting",
        queue_depth=9,
        last_error="device offline",
        reconnect_attempt=2,
    )

    text = format_status_text(status)

    assert "R58M12345" in text
    assert "attempt 2" in text


def test_build_highlight_rules_creates_rules_from_csv_text() -> None:
    rules = build_highlight_rules("ANR, crash , ")

    assert [rule.name for rule in rules] == ["ANR", "crash"]
