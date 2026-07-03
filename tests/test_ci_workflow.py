from pathlib import Path


def test_ci_workflow_publishes_tagged_windows_builds_to_release_assets() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "tags:" in workflow
    assert '- "v*"' in workflow
    assert "contents: write" in workflow
    assert "Publish release asset" in workflow
    assert "gh release create" in workflow
    assert "gh release upload" in workflow


def test_ci_workflow_builds_best_effort_legacy_windows_release_asset() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    legacy_job = workflow.split("build-windows-legacy:", 1)[1]

    assert "build-windows-legacy:" in workflow
    assert "runs-on: windows-2022" in legacy_job
    assert "python-version: '3.8'" in legacy_job
    assert "platform-tools_r28.0.2-windows.zip" in legacy_job
    assert "logcat-tool-for-win-legacy-win7.zip" in legacy_job
