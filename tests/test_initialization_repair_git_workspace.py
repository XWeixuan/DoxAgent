from __future__ import annotations

import subprocess

from doxagent.initialization_repair.git_workspace import GitWorkspaceManager


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_incident_worktree_and_branch_do_not_mutate_source_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git("init", cwd=source)
    (source / "tracked.txt").write_text("base\n", encoding="utf-8")
    git("add", "tracked.txt", cwd=source)
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
        cwd=source,
    )
    revision = git("rev-parse", "HEAD", cwd=source)
    manager = GitWorkspaceManager(tmp_path / "repair", str(source))
    workspace = manager.prepare("incident-1", revision)
    source_hash = manager.revision_sha256(revision)
    assert manager.source_sha256(workspace) == source_hash
    (workspace.path / "tracked.txt").write_text("fixed\n", encoding="utf-8")
    commit = manager.commit(workspace, "fix test")
    assert commit != revision
    assert manager.source_sha256(workspace) != source_hash
    assert manager.revision_sha256(revision) == source_hash
    assert workspace.branch == "codex/init-repair/incident-1"
    assert (source / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    assert git("rev-parse", "HEAD", cwd=source) == revision
    assert git("status", "--porcelain", cwd=source) == ""
    assert manager.prepare("incident-1", revision).path == workspace.path

    # The deployed hotfix branch can be created after the bare clone. A plain
    # bare-repo fetch updates FETCH_HEAD only and used to miss this commit.
    git("checkout", "-b", "codex/new-deployment", cwd=source)
    (source / "tracked.txt").write_text("deployed\n", encoding="utf-8")
    git("add", "tracked.txt", cwd=source)
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-m", "deployment", cwd=source)
    deployed = git("rev-parse", "HEAD", cwd=source)
    git("checkout", "-", cwd=source)
    next_workspace = manager.prepare("incident-2", deployed)
    assert (next_workspace.path / "tracked.txt").read_text(encoding="utf-8") == "deployed\n"
    assert git("rev-parse", "HEAD", cwd=source) == revision

    # Retiring the source hotfix must prune only the remote-tracking ref, never
    # the live incident branch or the commit needed by its worktree.
    git("branch", "-D", "codex/new-deployment", cwd=source)
    manager.ensure_mirror()
    assert git("rev-parse", "HEAD", cwd=next_workspace.path) == deployed
    assert git("rev-parse", "HEAD", cwd=workspace.path) == commit
    assert git("--git-dir", str(manager.bare), "branch", "-r", "--list",
               "origin/codex/new-deployment", cwd=source) == ""

    # Storage exit removes only a clean terminal checkout; fetched repair refs
    # and another still-dirty incident remain available after remote pruning.
    assert manager.retire("incident-1")
    assert not workspace.path.exists()
    assert git("--git-dir",str(manager.bare),"rev-parse",workspace.branch,cwd=source)==commit
    (next_workspace.path / "tracked.txt").write_text("uncommitted repair\n",encoding="utf-8")
    assert not manager.retire("incident-2")
    assert next_workspace.path.is_dir()
    assert git("rev-parse","HEAD",cwd=next_workspace.path)==deployed
