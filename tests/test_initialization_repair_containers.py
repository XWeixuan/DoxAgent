from __future__ import annotations

import pytest

from doxagent.initialization_repair.containers import (
    ContainerError,
    ContainerSpec,
    DockerRuntime,
)


class InspectRuntime(DockerRuntime):
    def __init__(self, payload):
        self.payload = payload

    def inspect(self, name):
        return self.payload


def test_production_template_allowlists_environment_and_excludes_socket():
    runtime = InspectRuntime(
        {
            "Image": "sha256:base",
            "Config": {
                "Image": "production:tag",
                "User": "1000:1000",
                "Env": [
                    "DOXAGENT_CODEX_WORKER_BASE_URL=http://codex-worker:8791",
                    "DOXAGENT_CODEX_WORKER_BEARER_TOKEN=secret",
                    "CDECR_SQLITE_PATH=/data/cdecr/cdecr.sqlite3",
                    "CDECR_M2_PROVIDER=deepseek",
                    "CDECR_SUPABASE_PUBLISHABLE_KEY=cdecr-secret",
                    "DASHSCOPE_API_KEY=dashscope-secret",
                    "DASHSCOPE_FALLBACK_API_KEY=dashscope-fallback",
                    "DASHSCOPE_FALLBACK_API_KEYS=first,second",
                    "DEEPSEEK_API_KEY=deepseek-secret",
                    "PATH=/usr/bin",
                    "UNRELATED=value",
                ],
            },
            "Mounts": [
                {"Source": "/data", "Destination": "/data", "RW": True},
                {
                    "Source": "/var/run/docker.sock",
                    "Destination": "/var/run/docker.sock",
                    "RW": True,
                },
                {"Source": "/safety", "Destination": "/safety", "RW": False},
            ],
            "NetworkSettings": {"Networks": {"doxagent_default": {}}},
        }
    )
    template = runtime.production_template("production")
    assert template["environment"] == {
        "DOXAGENT_CODEX_WORKER_BASE_URL": "http://codex-worker:8791",
        "DOXAGENT_CODEX_WORKER_BEARER_TOKEN": "secret",
        "CDECR_SQLITE_PATH": "/data/cdecr/cdecr.sqlite3",
        "CDECR_M2_PROVIDER": "deepseek",
        "CDECR_SUPABASE_PUBLISHABLE_KEY": "cdecr-secret",
        "DASHSCOPE_API_KEY": "dashscope-secret",
        "DASHSCOPE_FALLBACK_API_KEY": "dashscope-fallback",
        "DASHSCOPE_FALLBACK_API_KEYS": "first,second",
        "DEEPSEEK_API_KEY": "deepseek-secret",
    }
    assert template["volumes"] == ["/data:/data", "/safety:/safety:ro"]
    assert template["networks"] == ["doxagent_default"]
    assert template["user"] == "1000:1000"


def test_existing_container_identity_mismatch_is_rejected():
    runtime = InspectRuntime(
        {
            "Id": "container",
            "Config": {"Labels": {"doxagent.repair.round": "different"}},
            "State": {"Status": "exited", "Running": False, "ExitCode": 1},
        }
    )
    with pytest.raises(ContainerError, match="labels"):
        runtime.ensure_started(
            ContainerSpec(
                name="repair-round",
                image="candidate",
                command=["true"],
                labels={"doxagent.repair.round": "expected"},
            )
        )


def test_existing_container_resource_boundary_mismatch_is_rejected():
    runtime = InspectRuntime(
        {
            "Id": "container",
            "Config": {"Labels": {"doxagent.repair.round": "expected"}},
            "HostConfig": {
                "CgroupParent": "doxagent-app.slice",
                "Memory": 2 * 1024**3,
                "MemorySwap": 3 * 1024**3,
                "PidsLimit": 64,
            },
            "State": {"Status": "running", "Running": True, "ExitCode": 0},
        }
    )
    with pytest.raises(ContainerError, match="Memory=.*expected"):
        runtime.ensure_started(
            ContainerSpec(
                name="repair-round",
                image="candidate",
                command=["true"],
                labels={"doxagent.repair.round": "expected"},
                memory="4g",
                memory_swap="5g",
                pids_limit=64,
            )
        )


def test_repair_agent_can_select_namespace_seccomp_profile():

    class CaptureRuntime(DockerRuntime):
        def inspect(self, name):
            return None

        def _run(self, args, **kwargs):
            self.args = args
            raise ContainerError("captured create")

    runtime = CaptureRuntime()
    with pytest.raises(ContainerError, match="captured create"):
        runtime.ensure_started(ContainerSpec(
            name="repair-agent", image="agent", command=["true"], labels={},
            seccomp_profile="/etc/doxagent/repair-seccomp.json",
            apparmor_profile="doxagent-initialization-repair"))
    assert runtime.args[runtime.args.index("--security-opt") + 1] == (
        "seccomp=/etc/doxagent/repair-seccomp.json")
    assert "apparmor=doxagent-initialization-repair" in runtime.args
    assert "--privileged" not in runtime.args


def test_existing_agent_with_different_sandbox_policy_is_rejected(tmp_path):
    import json
    profile = tmp_path / "seccomp.json"
    profile.write_text(json.dumps({"defaultAction": "SCMP_ACT_ERRNO", "syscalls": []}))
    runtime = InspectRuntime({
        "Id": "container", "Config": {"Labels": {}},
        "HostConfig": {"CgroupParent": "doxagent-app.slice", "SecurityOpt": []},
        "State": {"Status": "running", "Running": True},
    })
    with pytest.raises(ContainerError, match="AppArmor=.*expected"):
        runtime.ensure_started(ContainerSpec(
            name="repair-agent", image="agent", command=["true"], labels={},
            apparmor_profile="doxagent-initialization-repair", seccomp_profile=str(profile)))
