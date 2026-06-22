from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


def build_portable(
    dist_root: Path,
    platform_tools_dir: Path,
    readme_path: Path,
    output_root: Path,
) -> Path:
    resolved_dist_root = dist_root.resolve()
    resolved_output_root = output_root.resolve()
    if (
        resolved_output_root == resolved_dist_root
        or resolved_dist_root in resolved_output_root.parents
        or resolved_output_root in resolved_dist_root.parents
    ):
        raise ValueError("output_root must not overlap the built app directory")

    built_exe = dist_root / "logcat-tool-for-win.exe"
    if not built_exe.exists():
        raise FileNotFoundError(f"Missing built executable: {built_exe}")
    if not platform_tools_dir.exists():
        raise FileNotFoundError(f"Missing platform-tools directory: {platform_tools_dir}")
    adb_exe = platform_tools_dir / "adb.exe"
    if not adb_exe.exists():
        raise FileNotFoundError(f"Missing adb executable: {adb_exe}")
    if not readme_path.exists():
        raise FileNotFoundError(f"Missing README file: {readme_path}")

    output_root.mkdir(parents=True, exist_ok=True)

    release_dir = output_root / "logcat-tool-for-win"
    if release_dir.exists():
        shutil.rmtree(release_dir)
    zip_path = output_root / "logcat-tool-for-win.zip"
    if zip_path.exists():
        zip_path.unlink()

    release_dir.mkdir(parents=True)
    shutil.copy2(built_exe, release_dir / built_exe.name)
    shutil.copy2(readme_path, release_dir / "README.md")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in release_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(output_root))

    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the portable Windows release ZIP.")
    parser.add_argument("--dist-root", type=Path, default=Path("dist"))
    parser.add_argument(
        "--platform-tools-dir",
        type=Path,
        default=Path("src/logcat_tool_for_win/resources/platform-tools"),
    )
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts"))
    args = parser.parse_args()

    build_portable(args.dist_root, args.platform_tools_dir, args.readme, args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
