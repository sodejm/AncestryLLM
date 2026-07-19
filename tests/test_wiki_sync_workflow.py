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
    assert workflow.index("git add --all") < workflow.index("if git diff --cached --quiet; then")
    assert 'git config user.name "github-actions[bot]"' in workflow
    assert (
        'git config user.email "41898282+github-actions[bot]@users.noreply.github.com"' in workflow
    )
    assert 'git commit -m "docs: synchronize from ${SOURCE_SHA}"' in workflow
    assert "GITHUB_TOKEN: ${{ github.token }}" in workflow
    assert "http.https://github.com/.extraheader" in workflow
    assert "push origin HEAD:master" in workflow
