from __future__ import annotations

from logcat_tool_for_win.gui import main as gui_main


def main() -> int:
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
