from pathlib import Path


def test_ci_workflow_publishes_main_and_tagged_windows_builds_to_release_assets() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    publish_job = workflow.split("publish-release:", 1)[1]

    assert "tags:" in workflow
    assert '- "v*"' in workflow
    assert "contents: write" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "github.ref == 'refs/heads/main'" in publish_job
    assert "actions/download-artifact@v7" in publish_job
    assert 'release_tag="latest"' in publish_job
    assert 'git push --force origin "refs/tags/$release_tag"' in publish_job
    assert 'gh release edit "$release_tag" --title "Latest Build" --notes "$notes"' in publish_job
    assert "gh release create" in publish_job
    assert 'gh release upload "$release_tag" release-artifacts/logcat-tool-for-win/logcat-tool-for-win.zip --clobber' in publish_job
    assert 'gh release upload "$release_tag" release-artifacts/logcat-tool-for-win-legacy-win7/logcat-tool-for-win-legacy-win7.zip --clobber' in publish_job


def test_ci_workflow_builds_best_effort_legacy_windows_release_asset() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    legacy_job = workflow.split("build-windows-legacy:", 1)[1].split("publish-release:", 1)[0]

    assert "build-windows-legacy:" in workflow
    assert "runs-on: windows-2022" in legacy_job
    assert "python-version: '3.8'" in legacy_job
    assert "platform-tools_r28.0.2-windows.zip" in legacy_job
    assert "logcat-tool-for-win-legacy-win7.zip" in legacy_job
