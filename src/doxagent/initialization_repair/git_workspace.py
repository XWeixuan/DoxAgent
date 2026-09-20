"""Incident-owned Git worktrees isolated from the production checkout."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class IncidentWorkspace:
    incident_id: str
    branch: str
    path: Path
    source_revision: str


class GitWorkspaceManager:
    def __init__(self, root: Path, source_repository: str) -> None:
        self.root = root.resolve()
        self.source_repository = source_repository
        self.bare = self.root / "repository.git"

    @staticmethod
    def _run(args: list[str], *, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-4000:]
            raise GitWorkspaceError(f"git command failed ({completed.returncode}): {detail}")
        return completed.stdout.strip()

    def ensure_mirror(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.bare.exists():
            self._run(["git", "clone", "--bare", self.source_repository, str(self.bare)])
        elif not (self.bare / "HEAD").is_file():
            raise GitWorkspaceError("repair repository exists but is not a bare Git repository")
        self._run(["git", "--git-dir", str(self.bare), "fetch", "--prune", "origin"])

    def prepare(self, incident_id: str, source_revision: str) -> IncidentWorkspace:
        if not source_revision.strip() or source_revision == "unversioned":
            raise GitWorkspaceError("a verified deployed source revision is required")
        self.ensure_mirror()
        resolved = self._run(
            ["git", "--git-dir", str(self.bare), "rev-parse", f"{source_revision}^{{commit}}"]
        )
        branch = f"codex/init-repair/{incident_id}"
        path = self.root / "incidents" / incident_id / "worktree"
        if path.exists():
            actual = self._run(["git", "branch", "--show-current"], cwd=path)
            if actual != branch:
                raise GitWorkspaceError(
                    f"incident worktree branch mismatch: expected {branch}, got {actual}"
                )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            branches = self._run(["git", "--git-dir", str(self.bare), "branch", "--list", branch])
            args = ["git", "--git-dir", str(self.bare), "worktree", "add"]
            if branches.strip():
                args.extend([str(path), branch])
            else:
                args.extend(["-b", branch, str(path), resolved])
            self._run(args)
        return IncidentWorkspace(incident_id, branch, path, resolved)

    def changed_files(self, workspace: IncidentWorkspace) -> list[str]:
        output = self._run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace.path
        )
        return [line[3:] for line in output.splitlines() if len(line) > 3]

    def source_sha256(self, workspace: IncidentWorkspace) -> str:
        """Hash the committed Git archive used as the candidate build source."""

        completed = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=workspace.path,
            check=False,
            capture_output=True,
        )
        if completed.returncode:
            detail = completed.stderr.decode(errors="replace").strip()[-4000:]
            raise GitWorkspaceError(f"git archive failed ({completed.returncode}): {detail}")
        return hashlib.sha256(completed.stdout).hexdigest()

    def revision_sha256(self, revision: str) -> str:
        """Hash one fetched source revision without consulting a mutable checkout."""

        self.ensure_mirror()
        completed = subprocess.run(
            ["git", "--git-dir", str(self.bare), "archive", "--format=tar", revision],
            check=False,
            capture_output=True,
        )
        if completed.returncode:
            detail = completed.stderr.decode(errors="replace").strip()[-4000:]
            raise GitWorkspaceError(f"git archive failed ({completed.returncode}): {detail}")
        return hashlib.sha256(completed.stdout).hexdigest()

    def commit(self, workspace: IncidentWorkspace, message: str) -> str:
        if not message.strip():
            raise ValueError("repair commit message required")
        if not self.changed_files(workspace):
            return self._run(["git", "rev-parse", "HEAD"], cwd=workspace.path)
        self._run(["git", "add", "--all"], cwd=workspace.path)
        self._run(
            [
                "git",
                "-c",
                "user.name=DoxAgent Initialization Guardian",
                "-c",
                "user.email=guardian@doxagent.invalid",
                "commit",
                "-m",
                message,
            ],
            cwd=workspace.path,
        )
        return self._run(["git", "rev-parse", "HEAD"], cwd=workspace.path)
