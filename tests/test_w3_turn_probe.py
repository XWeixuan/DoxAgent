import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.sdk_runtime import _SdkTurnHandle
from doxagent.codex_worker.w3_turn_probe import (
    TurnProbe,
    classify,
    control_root,
    maintenance,
    project_native,
    prune,
    read_control,
    read_native,
    report,
    union_ms,
)
from tests.test_codex_turn_usage import usage


def record(second, kind, **payload):
    return {"timestamp": f"2026-10-17T01:00:{second:02d}Z", "type": kind, "payload": payload}


def native():
    return [
        record(0, "event_msg", type="task_started", turn_id="turn"),
        record(
            3,
            "response_item",
            type="custom_tool_call",
            call_id="a",
            name="exec",
            input='await tools.exec_command({cmd:"cat context/research/v/d1/001.md"})',
        ),
        record(
            4,
            "token_usage_record",
            thread_id="thread",
            turn_id="turn",
            response_id="r1",
            usage=usage(100),
        ),
        record(5, "response_item", type="custom_tool_call_output", call_id="a", output="hello中文"),
        record(
            7,
            "response_item",
            type="custom_tool_call",
            call_id="b",
            name="exec",
            input='await tools.web__run({search_query:[{q:"earliest original source"}]})',
        ),
        record(8, "response_item", type="custom_tool_call_output", call_id="b", output="web"),
        record(
            10,
            "token_usage_record",
            thread_id="thread",
            turn_id="turn",
            response_id="r2",
            usage=usage(900),
        ),
        record(
            12,
            "response_item",
            type="custom_tool_call",
            call_id="c",
            name="exec",
            input='await tools.exec_command({cmd:"cat context/research/v/d1/001.md"})',
        ),
        record(
            14, "response_item", type="custom_tool_call_output", call_id="c", output="hello中文"
        ),
        record(
            18,
            "token_usage_record",
            thread_id="thread",
            turn_id="turn",
            response_id="r3",
            usage=usage(950),
        ),
    ]


def enable(tmp_path):
    root = control_root(tmp_path)
    root.mkdir()
    config = {
        "enabled": True,
        "until_utc": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "max_samples": 5,
        "max_megabytes": 1,
        "retention_days": 1,
    }
    (root / "control.json").write_text(json.dumps(config), encoding="utf-8")
    return root, config


def test_round_growth_output_bytes_and_mixed_business_steps():
    rows = native()
    rows.insert(5, rows[2])  # Duplicate response is not an extra model round.
    rows.append(
        record(
            19,
            "token_usage_record",
            thread_id="child",
            turn_id="turn",
            response_id="child",
            usage=usage(9999),
        )
    )
    result = project_native(rows, "thread", "turn")
    assert result["response_count"] == 3
    assert result["tool_union_ms"] == 5000
    assert result["tools"][0]["output_bytes"] == len("hello中文".encode())
    assert result["repeat_candidates"] == ["c"]
    r2 = result["responses"][1]
    assert r2["input_growth_tokens"] == 800
    assert r2["preceding_tool_call_ids"] == ["a", "b"]
    assert r2["after_last_tool_return_ms"] == 2000
    assert r2["observed_round_tool_ms"] == 2000
    assert r2["observed_round_residual_ms"] == 4000
    assert "FIRST_SOURCE_SEARCH" in r2["action_business_labels"]
    assert "D1_RESEARCH" in r2["after_business_labels"]
    encoded = json.dumps(result)
    assert "hello" not in encoded and "cat context" not in encoded
    assert result["tools"][0]["research_resources"] == ["d1/001.md"]
    assert result["tools"][0]["nested_tool_names"] == ["exec_command"]


def test_generic_web_is_not_invented_original_source_intent():
    assert classify("web_search", "Intel news")["business_labels"] == ["WEB_RESEARCH"]
    result = classify("exec", "cat AGENTS.md skills/skill.md index.json d2/a.json d3/p.json")
    assert {"CONTROL_CONTRACT", "RESEARCH_NAVIGATION", "D2_EXPECTATIONS", "D3_POLICY"} <= set(
        result["business_labels"]
    )
    assert union_ms([(0, 5), (2, 7), (8, 9)]) == 8


def test_incomplete_call_and_compaction_are_partial():
    rows = native()[:3] + [record(9, "compacted")]
    value = project_native(rows, "thread", "turn")
    assert value["tools"][0].get("duration_ms") is None
    assert value["compactions"][0]["measurement_quality"] == "completion_only"


def test_selected_turn_excludes_other_turns_and_reports_truncation(tmp_path, monkeypatch):
    path = tmp_path / "rollout.jsonl"
    rows = [
        record(0, "event_msg", type="task_started", turn_id="old"),
        *native(),
        record(22, "event_msg", type="task_started", turn_id="next"),
        record(25, "response_item", type="custom_tool_call", call_id="next", name="exec"),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    selected, flags = read_native(path, "turn")
    assert flags["turn_start_found"]
    assert project_native(selected, "thread", "turn")["tool_count"] == 3
    monkeypatch.setattr("doxagent.codex_worker.w3_turn_probe.MAX_NATIVE_BYTES", 100)
    _, flags = read_native(path, "turn")
    assert flags["native_scan_truncated"] and not flags["turn_start_found"]


def test_control_expiry_phase_scope_and_fail_open(tmp_path):
    cwd = tmp_path / "case"
    cwd.mkdir()
    request = SimpleNamespace(
        task_phase="trade_decision", run_id="case", attempt_id="a", model="gpt-6-luna", effort="max"
    )
    assert TurnProbe.create(cwd, request, "thread", "turn") is None
    root, config = enable(tmp_path)
    assert TurnProbe.create(cwd, request, "thread", "turn") is not None
    request.task_phase = "post_decision_operations"
    assert TurnProbe.create(cwd, request, "thread", "turn") is None
    config["until_utc"] = "2000-01-01T00:00:00Z"
    (root / "control.json").write_text(json.dumps(config))
    assert not read_control(root)
    (root / "control.json").write_text("[]")
    assert not read_control(root)


def test_receipts_live_outside_case_gc_and_bounded_retention(tmp_path):
    root, config = enable(tmp_path)
    receipts = root / "receipts"
    receipts.mkdir()
    for n in range(8):
        p = receipts / f"{n}.json"
        p.write_text("{}")
        os.utime(p, (100 + n, 100 + n))
    prune(root, config)
    assert not list(receipts.glob("*.json"))  # Expired even with collection disabled.
    for n in range(8):
        (receipts / f"{n}.json").write_text("{}")
    maintenance(tmp_path)
    assert len(list(receipts.glob("*.json"))) == 5


@pytest.mark.asyncio
async def test_real_stream_surface_probe_and_failure_receipt(tmp_path):
    from openai_codex.generated.v2_all import (
        ItemCompletedNotification,
        ItemStartedNotification,
        ThreadItem,
        Turn,
        TurnCompletedNotification,
    )

    root, _ = enable(tmp_path)
    cwd = tmp_path / "case"
    audit = cwd / "attempts" / "a" / "audit"
    audit.mkdir(parents=True)
    (audit / "w3_identity.json").write_text('{"case_id":"case_original"}')
    (audit.parent / "task.json").write_text('{"input":{"case_type":"POLICY_HIT_REVIEW"}}')
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in native()), encoding="utf-8")
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id,rollout_path)")
        db.execute("INSERT INTO threads VALUES(?,?)", ("thread", str(path)))
    request = SimpleNamespace(
        task_phase="trade_decision", run_id="case", attempt_id="a", model="gpt-6-luna", effort="max"
    )

    class Handle:
        id = "turn"
        fail = False

        async def stream(self):
            item = ThreadItem.model_validate({"type": "contextCompaction", "id": "compact"})
            for kind in (ItemStartedNotification, ItemCompletedNotification):
                yield SimpleNamespace(
                    payload=kind.model_construct(thread_id="thread", turn_id="turn", item=item)
                )
            if self.fail:
                raise RuntimeError("stream failure")
            yield SimpleNamespace(
                payload=TurnCompletedNotification.model_construct(
                    thread_id="thread",
                    turn=Turn.model_validate(
                        {"id": "turn", "status": "completed", "items": [], "error": None}
                    ),
                )
            )

    handle = Handle()
    adapter = _SdkTurnHandle(
        thread_id="thread",
        handle=handle,
        audit_path=audit / "sdk.jsonl",
        codex_home=tmp_path,
        request=request,
        cwd=cwd,
    )
    result = await adapter.run()
    assert result.status == "completed" and result.observation["response_count"] == 3
    receipt = json.loads(next((root / "receipts").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["case_id"] == "case_original" and receipt["case_type"] == "POLICY_HIT_REVIEW"
    assert receipt["sdk_items"][0]["end_ms"] >= receipt["sdk_items"][0]["start_ms"]
    assert "case_original" in report(root)
    handle.fail = True
    with pytest.raises(RuntimeError, match="stream failure"):
        await adapter.run()
    receipt = json.loads(next((root / "receipts").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["status"] == "interrupted_or_stream_failed"


def test_continuation_inherits_business_and_does_not_store_body():
    rows = [
        record(0, "event_msg", type="task_started", turn_id="turn"),
        record(
            1,
            "response_item",
            type="custom_tool_call",
            call_id="a",
            name="exec",
            input="await tools.web__run({})",
        ),
        record(
            2,
            "response_item",
            type="custom_tool_call_output",
            call_id="a",
            output="Script running with cell ID abc",
        ),
        record(
            3,
            "response_item",
            type="function_call",
            call_id="b",
            name="wait",
            arguments='{"cell_id":"abc"}',
        ),
        record(4, "response_item", type="function_call_output", call_id="b", output="done"),
    ]
    value = project_native(rows, "thread", "turn")
    assert value["tools"][1]["continuation_of_call_id"] == "a"
    assert value["tools"][1]["business_labels"] == ["WEB_RESEARCH"]


@pytest.mark.asyncio
async def test_observation_reference_survives_durable_index_and_ack(tmp_path):
    import asyncio

    from doxagent.codex_worker.jobs import WorkerJobManager
    from doxagent.codex_worker.sdk_runtime import WorkerTurnResult
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from tests.test_worker_resource_budget import Runtime, request

    class Observed(Runtime):
        async def run(self):
            return WorkerTurnResult(
                "thread",
                "turn",
                "completed",
                "{}",
                observation={"receipt": "w3-observation/receipts/test.json"},
            )

    manager = WorkerJobManager(Observed(), LocalWorkspaceStore(tmp_path))
    try:
        req = request(task_phase="w3_template")
        job = await manager.submit(req)
        for _ in range(200):
            await asyncio.sleep(0.01)
            job = manager.get(job.job_id)
            if job.status == "succeeded":
                break
        assert job.status == "succeeded"
        saved = manager.store.get(job.job_id)
        assert saved.resource_receipt["w3_observation"]["receipt"].endswith("test.json")
        assert manager.store.acknowledge(job.job_id).resource_receipt["w3_observation"]
    finally:
        await manager.close()


def test_result_recovery_preserves_observation_reference(tmp_path):
    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.jobs import WorkerJobManager
    from doxagent.codex_worker.result_receipt import commit, receipt_path
    from doxagent.codex_worker.schema import WorkerJob
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from tests.test_worker_resource_budget import Runtime, request

    req = request(task_phase="trade_decision")
    store = JobStore(tmp_path)
    store.save(
        WorkerJob(job_id="job", run_id=req.run_id, attempt_id=req.attempt_id, status="running"), req
    )
    commit(
        receipt_path(tmp_path, req),
        {
            "thread_id": "thread",
            "turn_id": "turn",
            "status": "completed",
            "final_response": "{}",
            "telemetry": None,
            "observation": {"receipt": "w3-observation/receipts/test.json"},
        },
    )
    manager = WorkerJobManager(Runtime(), LocalWorkspaceStore(tmp_path))
    assert manager.get("job").resource_receipt["w3_observation"]["receipt"].endswith("test.json")
