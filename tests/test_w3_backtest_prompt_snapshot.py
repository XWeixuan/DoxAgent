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
    run = tmp_path / "run"
    execute_run(tmp_path / "dataset", run, prompt_root=prompts,
                model=model, effort="medium" if model else None)
    assert (run / "prompt_snapshot/contract.json").read_bytes() == marker.read_bytes()
    assert "contract.json" in json.loads((run / "run_manifest.json").read_text())["prompt_hashes"]
    with pytest.raises(ValueError, match="new --run-id"):
        execute_run(tmp_path / "dataset", run, prompt_root=prompts,
                    model="gpt-6-luna", effort="max")
    marker.write_text('{"w3_contract_version":"persistent-runtime.w3.v4"}', encoding="utf-8")
    with pytest.raises(ValueError, match="new --run-id"):
        execute_run(tmp_path / "dataset", run, prompt_root=prompts)
