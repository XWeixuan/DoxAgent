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
    assert manager.retire('incident-1')
    assert not workspace.path.exists()
    assert git('--git-dir',str(manager.bare),'rev-parse','codex/init-repair/incident-1',cwd=source)==commit
    # Production can deploy an audited release branch before MAIN convergence.
    git('checkout','-b','codex/new-release',cwd=source)
    (source/'later.txt').write_text('new release')
    git('add','later.txt',cwd=source)
    git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','release',cwd=source)
    later=git('rev-parse','HEAD',cwd=source)
    next_workspace=manager.prepare('incident-2',later)
    assert (next_workspace.path/'later.txt').read_text()=='new release'
    assert git('--git-dir',str(manager.bare),'rev-parse','codex/init-repair/incident-1',cwd=source)==commit
