"""Docker boundary for transient repair-agent and initialization executor jobs."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MEMORY_VALUE = re.compile(r"^(?P<value>[0-9]+)(?P<unit>[kmgt]?)b?$", re.IGNORECASE)


class ContainerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    command: list[str]
    labels: dict[str, str]
    environment: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    user: str | None = None
    workdir: str | None = None
    cgroup_parent: str = "doxagent-app.slice"
    memory: str | None = None
    memory_swap: str | None = None
    pids_limit: int | None = None


@dataclass(frozen=True)
class ContainerState:
    identity: str
    name: str
    status: str
    exit_code: int | None
    labels: dict[str, str]


class DockerRuntime:
    SAFE_ENV_PREFIXES = (
        "DOXAGENT_",
        "CDECR_",
        "PYTHON",
        "PLAYWRIGHT_",
        "TZ",
        "LANG",
        "SSL_CERT_",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    )
    SAFE_ENV_KEYS = {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_FALLBACK_API_KEY",
        "DASHSCOPE_FALLBACK_API_KEYS",
        "DEEPSEEK_API_KEY",
    }

    def __init__(self, executable: str = "docker") -> None:
        self.executable = executable

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.executable, *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if check and result.returncode:
            detail = (result.stderr or result.stdout).strip()[-4000:]
            raise ContainerError(f"docker command failed ({result.returncode}): {detail}")
        return result

    @staticmethod
    def _validate_identity(value: str, label: str) -> None:
        if not _IDENTITY.fullmatch(value):
            raise ValueError(f"invalid {label}")

    def inspect(self, name: str) -> dict[str, Any] | None:
        self._validate_identity(name, "container name")
        result = self._run(["inspect", name], check=False)
        if result.returncode:
            if "No such" in result.stderr:
                return None
            raise ContainerError(result.stderr.strip()[-4000:])
        payload = json.loads(result.stdout)
        if len(payload) != 1:
            raise ContainerError("unexpected docker inspect response")
        return dict(payload[0])

    def state(self, name: str) -> ContainerState | None:
        payload = self.inspect(name)
        if payload is None:
            return None
        state = payload.get("State", {})
        return ContainerState(
            identity=str(payload["Id"]),
            name=name,
            status=str(state.get("Status", "unknown")),
            exit_code=int(state["ExitCode"]) if state.get("Running") is False else None,
            labels={str(k): str(v) for k, v in payload.get("Config", {}).get("Labels", {}).items()},
        )

    @staticmethod
    def _memory_bytes(value: str) -> int:
        match = _MEMORY_VALUE.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"invalid memory limit: {value}")
        scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
        return int(match.group("value")) * scale[match.group("unit").lower()]

    def _verify_resources(self, payload: dict[str, Any], spec: ContainerSpec) -> None:
        """Reject reuse when Docker's effective HostConfig is not the requested boundary."""
        host = payload.get("HostConfig")
        if not isinstance(host, dict):
            return
        expected: dict[str, object] = {}
        actual: dict[str, object] = {}
        if spec.cgroup_parent:
            expected["CgroupParent"] = spec.cgroup_parent.lstrip("/")
            actual["CgroupParent"] = str(host.get("CgroupParent") or "").lstrip("/")
        if spec.memory:
            expected["Memory"] = self._memory_bytes(spec.memory)
            actual["Memory"] = int(host.get("Memory") or 0)
        if spec.memory_swap:
            expected["MemorySwap"] = self._memory_bytes(spec.memory_swap)
            actual["MemorySwap"] = int(host.get("MemorySwap") or 0)
        if spec.pids_limit is not None:
            expected["PidsLimit"] = spec.pids_limit
            actual["PidsLimit"] = int(host.get("PidsLimit") or 0)
        mismatches = [key for key, value in expected.items() if actual.get(key) != value]
        if mismatches:
            details = ", ".join(
                f"{key}={actual.get(key)!r} expected {expected[key]!r}" for key in mismatches
            )
            raise ContainerError(f"existing container resource boundary mismatch: {details}")

    def ensure_started(self, spec: ContainerSpec) -> ContainerState:
        self._validate_identity(spec.name, "container name")
        for key, value in spec.labels.items():
            self._validate_identity(key, "label key")
            self._validate_identity(value, "label value")
        existing = self.state(spec.name)
        if existing is not None:
            if any(existing.labels.get(key) != value for key, value in spec.labels.items()):
                raise ContainerError("existing container labels do not match repair identity")
            self._verify_resources(self.inspect(spec.name) or {}, spec)
            if existing.status == "created":
                payload = self.inspect(spec.name) or {}
                connected = set(payload.get("NetworkSettings", {}).get("Networks", {}))
                for network in spec.networks:
                    if network not in connected:
                        self._run(["network", "connect", network, spec.name])
                self._run(["start", spec.name])
                started = self.state(spec.name)
                if started is None:
                    raise ContainerError("repair container disappeared while starting")
                return started
            return existing
        args = ["create", "--name", spec.name, "--restart", "no"]
        if spec.cgroup_parent:
            self._validate_identity(spec.cgroup_parent, "cgroup parent")
            args.extend(["--cgroup-parent", spec.cgroup_parent])
        if spec.memory:
            args.extend(["--memory", spec.memory])
        if spec.memory_swap:
            args.extend(["--memory-swap", spec.memory_swap])
        if spec.pids_limit is not None:
            if spec.pids_limit < 1:
                raise ValueError("pids limit must be positive")
            args.extend(["--pids-limit", str(spec.pids_limit)])
        for key, value in sorted(spec.labels.items()):
            args.extend(["--label", f"{key}={value}"])
        for key, value in sorted(spec.environment.items()):
            if not key or "=" in key or "\x00" in value:
                raise ValueError("invalid container environment")
            args.extend(["--env", f"{key}={value}"])
        for volume in spec.volumes:
            if "\x00" in volume:
                raise ValueError("invalid volume")
            args.extend(["--volume", volume])
        for network in spec.networks:
            self._validate_identity(network, "network")
        if spec.networks:
            args.extend(["--network", spec.networks[0]])
        if spec.user:
            args.extend(["--user", spec.user])
        if spec.workdir:
            args.extend(["--workdir", spec.workdir])
        args.extend([spec.image, *spec.command])
        created = self._run(args).stdout.strip()
        for network in spec.networks[1:]:
            self._run(["network", "connect", network, spec.name])
        self._run(["start", spec.name])
        state = self.state(spec.name)
        if state is None:
            raise ContainerError(f"created container disappeared: {created[:12]}")
        self._verify_resources(self.inspect(spec.name) or {}, spec)
        return state

    def logs(self, name: str) -> str:
        self._validate_identity(name, "container name")
        result = self._run(["logs", name], check=False)
        return result.stdout + result.stderr

    def remove(self, name: str) -> None:
        state = self.state(name)
        if state is None:
            return
        if state.status == "running":
            raise ContainerError("refusing to remove a running repair container")
        self._run(["rm", name])

    def stop(self, name: str, *, timeout_seconds: int = 30) -> None:
        state = self.state(name)
        if state is not None and state.status == "running":
            self._run(["stop", "--time", str(max(1, timeout_seconds)), name])

    def build(
        self,
        *,
        context: Path,
        dockerfile: Path,
        tag: str,
        build_args: dict[str, str],
        labels: dict[str, str],
    ) -> str:
        args = ["build", "--file", str(dockerfile), "--tag", tag]
        for key, value in sorted(build_args.items()):
            args.extend(["--build-arg", f"{key}={value}"])
        for key, value in sorted(labels.items()):
            args.extend(["--label", f"{key}={value}"])
        args.append(str(context))
        self._run(args)
        inspected = self._run(["image", "inspect", tag]).stdout
        images = json.loads(inspected)
        if len(images) != 1:
            raise ContainerError("unexpected docker image inspect response")
        actual_labels = images[0].get("Config", {}).get("Labels", {}) or {}
        if any(actual_labels.get(key) != value for key, value in labels.items()):
            raise ContainerError("built candidate image labels do not match repair identity")
        return str(images[0]["Id"])

    def verify_image(
        self,
        reference: str,
        *,
        expected_id: str,
        labels: dict[str, str],
    ) -> None:
        result = self._run(["image", "inspect", reference], check=False)
        if result.returncode:
            raise ContainerError(f"repair candidate image is unavailable: {reference}")
        images = json.loads(result.stdout)
        if len(images) != 1 or str(images[0].get("Id")) != expected_id:
            raise ContainerError("repair candidate image identity changed")
        actual_labels = images[0].get("Config", {}).get("Labels", {}) or {}
        if any(actual_labels.get(key) != value for key, value in labels.items()):
            raise ContainerError("repair candidate image labels do not match persisted round")

    def production_template(self, container_name: str) -> dict[str, Any]:
        payload = self.inspect(container_name)
        if payload is None:
            raise ContainerError(f"production initialization container not found: {container_name}")
        config = payload.get("Config", {})
        environment: dict[str, str] = {}
        for pair in config.get("Env", []):
            key, separator, value = str(pair).partition("=")
            if separator and (
                key.startswith(self.SAFE_ENV_PREFIXES) or key in self.SAFE_ENV_KEYS
            ):
                environment[key] = value
        mounts = [
            f"{item['Source']}:{item['Destination']}" + (":ro" if not item.get("RW", True) else "")
            for item in payload.get("Mounts", [])
            if item.get("Destination") != "/var/run/docker.sock"
        ]
        networks = list(payload.get("NetworkSettings", {}).get("Networks", {}))
        image = str(config.get("Image") or payload.get("Image"))
        return {
            "environment": environment,
            "volumes": mounts,
            "networks": networks,
            "user": str(config.get("User") or "") or None,
            "image": image,
        }
