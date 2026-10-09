"""Prepare exact deployed code, including unapplied-to-main overlay patches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .schema import DeploymentManifest


def git(args: list[str], cwd: Path | None = None) -> str:
    if cwd and (cwd.parent / "baseline.json").is_file():
        expected = json.loads((cwd.parent / "baseline.json").read_text()).get("git_marker")
        if expected is not None and (
            not (cwd / ".git").is_file() or (cwd / ".git").read_text() != expected
        ):
            raise ValueError("candidate Git metadata changed")
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "diff.external=",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError("candidate git operation failed: " + result.stderr[-2000:])
    return result.stdout.strip()


def safe_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if relative.startswith(("/", "\\")) or root.resolve() not in path.parents:
        raise ValueError("path outside candidate")
    if path.is_symlink() or any(
        p.is_symlink() for p in (root / relative).parents if p != root.parent
    ):
        raise ValueError("symlink not allowed")
    return path


class Workspaces:
    def __init__(self, root: Path, source_repository: str):
        self.root, self.source = root.resolve(), source_repository
        self.bare = self.root / "repository.git"

    def prepare(self, identity: str, manifest: DeploymentManifest) -> Path:
        if not re.fullmatch(r"incident_[a-f0-9]{32}", identity):
            raise ValueError("invalid incident id")
        if not re.fullmatch(r"[a-f0-9]{40,64}", manifest.source_commit):
            raise ValueError("exact source commit required; HEAD is not a production baseline")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.bare.exists():
            git(["clone", "--bare", self.source, str(self.bare)])
        git(["--git-dir", str(self.bare), "fetch", "origin"])
        path = self.root / "incidents" / identity / "worktree"
        branch = "codex/source-maintenance/" + identity
        baseline_path = path.parent / "baseline.json"
        fingerprint = hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
        if path.exists():
            saved = json.loads(baseline_path.read_text())
            if saved["fingerprint"] != fingerprint:
                raise RuntimeError("deployment baseline changed: explicit new worktree required")
            if git(["branch", "--show-current"], path) != branch:
                raise RuntimeError("candidate branch mismatch")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        git(
            [
                "--git-dir",
                str(self.bare),
                "worktree",
                "add",
                "-b",
                branch,
                str(path),
                manifest.source_commit,
            ]
        )
        # Materialize precisely recorded overlay bytes, never blindly copy current checkout.
        for relative, expected in manifest.file_hashes.items():
            target = safe_file(path, relative)
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
                continue
            if not manifest.overlay_root:
                raise RuntimeError("deployed overlay source missing: " + relative)
            source = safe_file(Path(manifest.overlay_root), relative)
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise RuntimeError("deployed overlay hash mismatch: " + relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        base = self.commit(path, "Materialize exact deployed source baseline")
        baseline_path.write_text(
            json.dumps(
                {
                    "fingerprint": fingerprint,
                    "commit": base,
                    "git_marker": (path / ".git").read_text(),
                }
            )
            + "\n"
        )
        return path

    @staticmethod
    def commit(path: Path, message: str) -> str:
        git(["add", "--all"], path)
        if git(["diff", "--cached", "--name-only"], path):
            git(
                [
                    "-c",
                    "user.name=DoxAgent Source Maintenance",
                    "-c",
                    "user.email=source-maintenance@doxagent.invalid",
                    "commit",
                    "-m",
                    message,
                ],
                path,
            )
        return git(["rev-parse", "HEAD"], path)

    @staticmethod
    def changed(path: Path) -> list[str]:
        baseline = json.loads((path.parent / "baseline.json").read_text())["commit"]
        tracked = git(["diff", "--name-only", baseline], path).splitlines()
        new = git(["ls-files", "--others", "--exclude-standard"], path).splitlines()
        return sorted(set(tracked + new))

    @staticmethod
    def original(path: Path, relative: str) -> str:
        base = json.loads((path.parent / "baseline.json").read_text())["commit"]
        result = subprocess.run(
            ["git", "show", f"{base}:{relative}"], cwd=path, capture_output=True, timeout=30
        )
        return result.stdout.decode() if result.returncode == 0 else ""
