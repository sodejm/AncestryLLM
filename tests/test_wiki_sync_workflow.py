"""Contract checks for the workflow that publishes validated documentation."""

from __future__ import annotations

from pathlib import Path


def test_sync_workflow_commits_only_changed_wiki_content() -> None:
    workflow = (Path(__file__).parents[1] / ".github/workflows/sync-wiki.yml").read_text(
        encoding="utf-8"
    )

    assert "python scripts/sync_wiki_docs.py" in workflow
    assert "--source docs" in workflow
    assert '--destination "$WIKI_WORKTREE"' in workflow
    assert "python scripts/commit_wiki_changes.py" in workflow
    assert '--repository "$WIKI_WORKTREE"' in workflow
    assert '--source-sha "$SOURCE_SHA"' in workflow
    assert '--output "$GITHUB_OUTPUT"' in workflow
    assert "if: steps.wiki_commit.outputs.committed == 'true'" in workflow
    commit_step = workflow.split("- name: Commit wiki changes", maxsplit=1)[1].split(
        "- name: Push wiki changes", maxsplit=1
    )[0]
    assert "GITHUB_TOKEN" not in commit_step
    assert "git commit" not in workflow
    assert "git config user." not in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "http.https://github.com/.extraheader" in workflow
    assert "push origin HEAD:master" in workflow
