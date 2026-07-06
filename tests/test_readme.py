from pathlib import Path


def test_readme_documents_auto_usb_wireless_fallback_for_tcp_connect() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "首次直连失败" in readme
    assert "自动尝试为当前选中的 USB 设备开启无线 ADB 后再重连" in readme


def test_readme_documents_latest_release_assets_for_main_pushes() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "rolling `latest` GitHub Release" in readme
    assert "https://github.com/Yecangyuan/LogcatToolForWin/releases/tag/latest" in readme


def test_readme_documents_adb_crash_fallback_guidance() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "0xc0000005" in readme
    assert "automatically fall back to another detected `adb.exe`" in readme


def test_readme_documents_portable_zip_includes_external_platform_tools() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "portable ZIP now also includes `platform-tools/` beside the executable" in readme
