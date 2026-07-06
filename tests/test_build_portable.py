import zipfile
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_portable import build_portable


def test_build_portable_creates_zip_with_exe_and_platform_tools(tmp_path: Path) -> None:
    built_exe = tmp_path / "dist" / "logcat-tool-for-win.exe"
    built_exe.parent.mkdir(parents=True)
    built_exe.write_text("exe", encoding="utf-8")

    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()
    (platform_tools / "adb.exe").write_text("adb", encoding="utf-8")
    (platform_tools / "AdbWinApi.dll").write_text("dll", encoding="utf-8")

    readme = tmp_path / "README.md"
    readme.write_text("# Portable", encoding="utf-8")

    zip_path = build_portable(tmp_path / "dist", platform_tools, readme, tmp_path / "artifacts")

    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "logcat-tool-for-win/logcat-tool-for-win.exe" in names
    assert "logcat-tool-for-win/README.md" in names
    assert "logcat-tool-for-win/platform-tools/adb.exe" in names
    assert "logcat-tool-for-win/platform-tools/AdbWinApi.dll" in names


def test_build_portable_rejects_missing_adb_exe(tmp_path: Path) -> None:
    built_exe = tmp_path / "dist" / "logcat-tool-for-win.exe"
    built_exe.parent.mkdir(parents=True)
    built_exe.write_text("exe", encoding="utf-8")

    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()

    readme = tmp_path / "README.md"
    readme.write_text("# Portable", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match=r"Missing adb executable: .*/platform-tools/adb\.exe"):
        build_portable(tmp_path / "dist", platform_tools, readme, tmp_path / "artifacts")


def test_build_portable_rejects_output_root_overlap(tmp_path: Path) -> None:
    built_exe = tmp_path / "dist" / "logcat-tool-for-win.exe"
    built_exe.parent.mkdir(parents=True)
    built_exe.write_text("exe", encoding="utf-8")

    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()
    (platform_tools / "adb.exe").write_text("adb", encoding="utf-8")

    readme = tmp_path / "README.md"
    readme.write_text("# Portable", encoding="utf-8")

    with pytest.raises(ValueError, match="output_root must not overlap"):
        build_portable(tmp_path / "dist", platform_tools, readme, tmp_path / "dist")


def test_build_portable_rejects_nested_output_root(tmp_path: Path) -> None:
    built_exe = tmp_path / "dist" / "logcat-tool-for-win.exe"
    built_exe.parent.mkdir(parents=True)
    built_exe.write_text("exe", encoding="utf-8")

    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()
    (platform_tools / "adb.exe").write_text("adb", encoding="utf-8")

    readme = tmp_path / "README.md"
    readme.write_text("# Portable", encoding="utf-8")

    with pytest.raises(ValueError, match="output_root must not overlap"):
        build_portable(
            tmp_path / "dist",
            platform_tools,
            readme,
            tmp_path / "dist" / "logcat-tool-for-win",
        )


def test_build_portable_rejects_missing_built_executable(tmp_path: Path) -> None:
    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()
    (platform_tools / "adb.exe").write_text("adb", encoding="utf-8")

    readme = tmp_path / "README.md"
    readme.write_text("# Portable", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match=r"Missing built executable: .*/dist/logcat-tool-for-win\.exe"):
        build_portable(tmp_path / "dist", platform_tools, readme, tmp_path / "artifacts")
