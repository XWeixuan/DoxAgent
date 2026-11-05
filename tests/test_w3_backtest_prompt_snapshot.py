import json

import pytest

from doxagent.w3_backtest.cli import execute_run


@pytest.mark.parametrize("model", [None, "gpt-6.1-sol"])
def test_prompt_snapshot_preserves_contract_and_rejects_changed_marker(
    tmp_path, monkeypatch, model
):
    from doxagent.w3_backtest import dataset, report, runner

    prompts = tmp_path / "prompts"
    (prompts / "skills").mkdir(parents=True)
    for name in ("AGENTS.md", "skills/skill.md"):
        (prompts / name).write_text("fixture", encoding="utf-8")
    marker = prompts / "contract.json"
    marker.write_text('{"w3_contract_version":"persistent-runtime.w3.v5"}', encoding="utf-8")
    monkeypatch.setattr(dataset, "load_cases", lambda _: [{"case_key": "C1", "ticker": "MU"}])
    monkeypatch.setattr(dataset, "input_fingerprint", lambda _: "unchanged-inputs")

    def fake_run(*args, prompt_root, **kwargs):
        version = json.loads((prompt_root / "contract.json").read_text())["w3_contract_version"]
        assert version.endswith("v5")
        assert kwargs["model"] == model
        assert kwargs["effort"] == ("medium" if model else None)
        return {"case_key": "C1", "status": "SUCCEEDED"}

    monkeypatch.setattr(runner, "run_case", fake_run)
    monkeypatch.setattr(report, "render_report", lambda *_: {"cases": 1})
    (tmp_path / "dataset").mkdir()
    (tmp_path / "dataset" / "manifest.json").write_text(
        '{"schema_version":"w3-backtest.dataset.v1"}'
    )
    run = tmp_path / "run"
    execute_run(
        tmp_path / "dataset",
        run,
        prompt_root=prompts,
        model=model,
        effort="medium" if model else None,
    )
    assert (run / "prompt_snapshot/contract.json").read_bytes() == marker.read_bytes()
    assert "contract.json" in json.loads((run / "run_manifest.json").read_text())["prompt_hashes"]
    with pytest.raises(ValueError, match="new --run-id"):
        execute_run(
            tmp_path / "dataset", run, prompt_root=prompts, model="gpt-6-luna", effort="max"
        )
    marker.write_text('{"w3_contract_version":"persistent-runtime.w3.v4"}', encoding="utf-8")
    with pytest.raises(ValueError, match="new --run-id"):
        execute_run(tmp_path / "dataset", run, prompt_root=prompts)


@pytest.mark.parametrize("enabled", [False, True])
def test_curated_time_awareness_identity_and_resume(tmp_path, monkeypatch, enabled):
    from doxagent.w3_backtest import cli, dataset, decision_runner, report, runner

    prompts = tmp_path / "prompts"
    for name in ("AGENTS.md", "skills/skill.md", "backtest.md", "contract.json"):
        path = prompts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    source = tmp_path / "dataset"
    source.mkdir()
    (source / "manifest.json").write_text('{"schema_version":"w3-backtest.dataset.v2"}')
    monkeypatch.setattr(dataset, "load_cases", lambda _: [{"case_key": "C1"}])
    monkeypatch.setattr(dataset, "input_fingerprint", lambda _: "same-inputs")
    monkeypatch.setattr(report, "render_report", lambda *_: {"cases": 1})
    binary = dict(path="patched-test", sha256="binary-one")
    monkeypatch.setattr(runner, "time_awareness_runtime", lambda: dict(binary))
    selected = []

    class Replay:
        def __init__(self, *args, **kwargs):
            selected.append(kwargs["time_awareness_enabled"])

        def run_case(self, *args, **kwargs):
            return dict(case_key="C1", status="SUCCEEDED")

        def close(self):
            pass

    monkeypatch.setattr(decision_runner, "DecisionReplayRunner", Replay)
    root = tmp_path / "run"
    execute_run(source, root, prompt_root=prompts, time_awareness_enabled=enabled)
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert selected == [enabled]
    assert manifest["time_awareness_enabled"] is enabled
    if enabled:
        assert manifest["time_awareness_runtime"] == binary
        binary["sha256"] = "changed-binary"
        with pytest.raises(ValueError, match="new --run-id"):
            execute_run(source, root, prompt_root=prompts, time_awareness_enabled=True)
        binary["sha256"] = "binary-one"
    else:
        assert "time_awareness_runtime" not in manifest
        # Pre-upgrade manifests did not declare the disabled feature.
        manifest.pop("time_awareness_enabled")
        manifest_path.write_text(json.dumps(manifest))
        execute_run(source, root, prompt_root=prompts)
    with pytest.raises(ValueError, match="new --run-id"):
        execute_run(source, root, prompt_root=prompts, time_awareness_enabled=not enabled)
    captured = []
    monkeypatch.setattr(cli, "execute_run", lambda *args, **kwargs: captured.append(kwargs))
    cli.main(["resume", "--run-id", str(root)])
    assert captured[0]["time_awareness_enabled"] is enabled


def test_legacy_dataset_rejects_time_awareness_before_dispatch(tmp_path, monkeypatch):
    from doxagent.w3_backtest import dataset

    (tmp_path / "manifest.json").write_text('{"schema_version":"w3-backtest.dataset.v1"}')
    monkeypatch.setattr(dataset, "load_cases", lambda _: [])
    with pytest.raises(ValueError, match="curated decision-only"):
        execute_run(tmp_path, tmp_path / "run", time_awareness_enabled=True)
    assert not (tmp_path / "run").exists()


def test_time_awareness_requires_available_runtime(tmp_path, monkeypatch):
    from doxagent.w3_backtest.runner import local_worker

    monkeypatch.delenv("DOXAGENT_CODEX_RUNTIME_BIN", raising=False)
    with pytest.raises(ValueError, match="DOXAGENT_CODEX_RUNTIME_BIN"):
        local_worker(tmp_path, time_awareness_enabled=True)
    assert not list(tmp_path.iterdir())


def test_time_awareness_records_binary_and_rejects_linux_on_windows(tmp_path, monkeypatch):
    import hashlib
    import os

    from doxagent.w3_backtest.runner import time_awareness_runtime

    binary = tmp_path / ("codex.exe" if os.name == "nt" else "codex")
    binary.write_bytes(b"MZsynthetic" if os.name == "nt" else b"\x7fELFsynthetic")
    binary.chmod(0o755)
    monkeypatch.setenv("DOXAGENT_CODEX_RUNTIME_BIN", str(binary))
    with pytest.raises(ValueError, match="requires executable sibling"):
        time_awareness_runtime()
    companion = binary.with_name(
        "codex-code-mode-host.exe" if os.name == "nt" else "codex-code-mode-host"
    )
    companion.write_bytes(b"synthetic-helper")
    companion.chmod(0o755)
    assert time_awareness_runtime() == dict(
        path=str(binary.resolve()), sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        companions={companion.name: hashlib.sha256(companion.read_bytes()).hexdigest()},
    )
    if os.name == "nt":
        binary = tmp_path / "codex"
        binary.write_bytes(b"\x7fELFsynthetic")
        monkeypatch.setenv("DOXAGENT_CODEX_RUNTIME_BIN", str(binary))
        with pytest.raises(ValueError, match="Linux-only"):
            time_awareness_runtime()


def test_replay_fork_and_decision_share_patched_runtime(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from doxagent.codex_worker import jobs, sdk_runtime
    from doxagent.w3_backtest import runner

    captured = {}

    def runtime(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    def manager(runtime, store, **kwargs):
        return SimpleNamespace(thread_lifecycle=SimpleNamespace())

    monkeypatch.setattr(runner, "time_awareness_runtime", lambda: dict(path="patched-test"))
    monkeypatch.setattr(runner, "private_home", lambda _: tmp_path / "private")
    monkeypatch.setattr(runner, "_LocalWorker", lambda value: value)
    monkeypatch.setattr(sdk_runtime, "OpenAICodexRuntime", runtime)
    monkeypatch.setattr(jobs, "WorkerJobManager", manager)
    worker, _ = runner.local_worker(tmp_path / "worker", time_awareness_enabled=True)
    assert captured["config"].codex_bin == "patched-test"
    assert worker.thread_lifecycle.transport.config is captured["config"]
    assert captured["settings"].w3_time_awareness_enabled is True
    assert captured["settings"].ibkr_tws_enabled is False
    assert captured["config"].env["CODEX_HOME"] == str((tmp_path / "private").resolve())
