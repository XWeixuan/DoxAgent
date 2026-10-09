"""Trusted Docker executor; the Worker never receives this object/socket."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .schema import RepairRound
from .settings import MaintenanceSettings


class Docker:
    def __init__(self, settings: MaintenanceSettings):
        self.settings = settings

    def run(self, args: list[str], *, timeout: float = 60, check=True, include_stderr=False) -> str:
        try:
            result = subprocess.run(
                [self.settings.docker_executable, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RuntimeError("bounded maintenance Docker operation unavailable") from exc
        if check and result.returncode:
            raise RuntimeError("maintenance Docker operation failed: " + result.stderr[-2000:])
        return result.stdout + (result.stderr if include_stderr else "")

    def inspect(self, name: str) -> dict | None:
        text = self.run(["inspect", name], check=False)
        return json.loads(text)[0] if text.strip() else None

    def host_path(self, path: Path) -> Path:
        root = self.settings.worker_host_root or self.settings.root
        return root.resolve() / path.resolve().relative_to(self.settings.root.resolve())

    @staticmethod
    def worker_name(round_: RepairRound) -> str:
        feedback = json.dumps(round_.feedback, sort_keys=True) if round_.feedback else ""
        suffix = hashlib.sha256(feedback.encode()).hexdigest()[:10]
        return f"source-maintenance-{round_.round_id}-{suffix}"

    def agent(self, round_: RepairRound) -> dict | None:
        name = self.worker_name(round_)
        state = self.inspect(name)
        path = Path(round_.worktree)
        meta = path / ".maintenance"
        if state:
            if state["Config"]["Labels"].get("doxagent.source-round") != round_.round_id:
                raise RuntimeError("unexpected existing Worker container")
            if state["State"]["Running"]:
                return None
            result = meta / "receipt.json"
            if state["State"]["ExitCode"] != 0 or not result.is_file():
                raise RuntimeError("source Worker exited without valid receipt")
            return json.loads(result.read_text())
        if not self.settings.worker_proxy or not self.settings.auth_file:
            raise ValueError("isolated model proxy and auth template must be configured")
        network = json.loads(self.run(["network", "inspect", self.settings.worker_network]))[0]
        if not network.get("Internal"):
            raise ValueError("Worker network must be internal; provider egress via allowlist proxy")
        home = self.settings.root / "codex-home"
        home.mkdir(parents=True, exist_ok=True)
        # Only auth.json is copied, never business config/MCP/plugin configuration.
        auth = home / "auth.json"
        if not auth.exists():
            auth.write_bytes(self.settings.auth_file.read_bytes())
            auth.chmod(0o600)
        config = home / "config.toml"
        config.write_text("[features]\nplugins = false\n[ mcp_servers ]\n", encoding="utf-8")
        import os

        if hasattr(os, "getuid") and os.getuid() == 0:
            for owned in (path, home):
                for child in [owned, *owned.rglob("*")]:
                    if not child.is_symlink():
                        os.chown(child, 1000, 1000)
        if round_.feedback:
            (meta / "feedback.json").write_text(json.dumps(round_.feedback))
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--restart",
            "no",
            "--init",
            "--user",
            "1000:1000",
            "--memory",
            "2g",
            "--memory-swap",
            "2g",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,size=128m",
            "--network",
            self.settings.worker_network,
            "--label",
            f"doxagent.source-round={round_.round_id}",
            "--volume",
            f"{self.host_path(path)}:/candidate:rw",
            "--volume",
            f"{self.host_path(home)}:/codex-home:rw",
            "--env",
            "CODEX_HOME=/codex-home",
            "--env",
            "PYTHONPATH=/opt/maintenance/src",
            "--env",
            "HTTP_PROXY=" + self.settings.worker_proxy,
            "--env",
            "HTTPS_PROXY=" + self.settings.worker_proxy,
            "--workdir",
            "/candidate",
            self.settings.agent_image,
            "python",
            "-m",
            "doxagent.source_maintenance.cli",
            "agent",
            "--worktree",
            "/candidate",
            "--model",
            self.settings.model,
            "--effort",
            self.settings.effort,
            "--round-id",
            round_.round_id,
        ]
        if round_.thread_id:
            args += ["--thread-id", round_.thread_id]
        self.run(args)
        round_.receipt["container"] = name
        return None

    def stop_worker(self, round_: RepairRound) -> None:
        name = round_.receipt.get("container") or self.worker_name(round_)
        self.run(["stop", "--time", "10", name], check=False)

    def verify(self, path: Path, tests: list[str]) -> dict:
        # Fixed tool commands, no host execution of candidate tests or model commands.
        base = [
            "run",
            "--rm",
            "--init",
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--memory",
            "2g",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:rw,size=128m",
            "--volume",
            f"{self.host_path(path)}:/candidate:ro",
            "--workdir",
            "/candidate",
            "--env",
            "PYTHONPATH=/candidate/src",
            self.settings.verify_image,
        ]
        logs = []
        candidate_tests = [t for t in tests if not t.startswith(".maintenance/frozen-tests/")]
        frozen_tests = [t for t in tests if t.startswith(".maintenance/frozen-tests/")]
        commands = [
            ["python", "-m", "pytest", "--offline", "-p", "no:cacheprovider", *candidate_tests]
        ]
        if frozen_tests:
            commands.append(
                [
                    "python",
                    "-m",
                    "pytest",
                    "--offline",
                    "-p",
                    "no:cacheprovider",
                    "--confcutdir",
                    "/candidate/.maintenance/frozen-tests",
                    *frozen_tests,
                ]
            )
        commands.append(
            ["ruff", "check", "src/doxagent/message_bus_v2", "src/doxagent/content_enrichment"],
        )
        for command in commands:
            try:
                logs.append(self.run([*base, *command], timeout=180))
            except RuntimeError as exc:
                return {"passed": False, "log": str(exc)[-4000:]}
        return {"passed": True, "log": "\n".join(logs)[-8000:]}

    def compose(self, override: Path, services: list[str]) -> None:
        s = self.settings
        if not s.compose_files or not s.compose_project_directory:
            raise ValueError("exact existing Compose topology required")
        args = ["compose", "--project-directory", str(s.compose_project_directory)]
        if s.compose_env_file:
            args += ["--env-file", s.compose_env_file]
        for file in [*s.compose_files, str(override)]:
            args += ["-f", file]
        self.run([*args, "up", "-d", "--no-deps", "--no-build", *services], timeout=120)
