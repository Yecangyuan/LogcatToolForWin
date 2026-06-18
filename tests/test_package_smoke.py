from logcat_tool_for_win import __version__
from logcat_tool_for_win.__main__ import main


def test_package_smoke() -> None:
    assert __version__ == "0.1.0"
    assert callable(main)
