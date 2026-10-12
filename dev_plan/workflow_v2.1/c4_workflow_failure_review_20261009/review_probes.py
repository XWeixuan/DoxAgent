"""Deterministic bad-case probes; temporary workspaces and fake Workers only."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.insert(0, str(REPO))

from doxagent.codex_runtime.schema import CodexD1Node  # noqa: E402
from doxagent.codex_worker.jobs import WorkerJobManager  # noqa: E402
from doxagent.workflows.codex_document1.recovery import recover_node_output  # noqa: E402
from doxagent.workflows.codex_global_research.schema import GlobalResearchRunRequest  # noqa: E402
from tests.fixtures.codex_document1 import _FakeWorker  # noqa: E402
from tests.test_c4_split_workflow import runtime  # noqa: E402


async def main() -> None:
    results = {}
    signature = inspect.signature(WorkerJobManager._capture_identity)
    try:
        signature.bind(object(), "thread", None)
    except TypeError as exc:
        results["direct_runtime_callback_still_incompatible"] = str(exc)

    class FailingFuture(_FakeWorker):
        fail = True

        async def run(self, request):
            job = await super().run(request)
            if self.fail and request.node is CodexD1Node.C4F_FUTURE_NODES:
                return job.model_copy(update={"status": "failed", "final_response": None})
            return job

    with tempfile.TemporaryDirectory(prefix="c4-review-") as directory:
        owner, worker, _, _ = runtime(Path(directory), FailingFuture)
        request = GlobalResearchRunRequest(run_id="cascade", ticker="MU", research_brief="probe")
        bundle = await owner.run(request)
        before = len(worker.requests)
        worker.fail = False
        replay = await owner.run(request)
        results["future_failure_cascade_and_published_replay"] = {
            "products": bundle.c4_product_status,
            "formal_called": any(x.node is CodexD1Node.C4E_FORMAL_SCAN for x in worker.requests),
            "network_called": any(x.node is CodexD1Node.C4E_NETWORK_BUILD for x in worker.requests),
            "calls_after_provider_repaired": len(worker.requests) - before,
            "replayed_products": replay.c4_product_status,
        }

    class AckWorker(_FakeWorker):
        async def run(self, request):
            job = await super().run(request)
            if request.node is CodexD1Node.C4E_NETWORK_BUILD:
                task = json.loads((await self.workspace.read_text(
                    request.run_id, f"attempts/{request.attempt_id}/input/task.json"
                )).content)
                await self.workspace.write_text(request.run_id, task["markdown_output_path"],
                                                "# Actual network research\nValid saved report.")
                return job.model_copy(update={
                    "status": "failed", "error_code": "WORKER_TIMEOUT",
                    "final_response": "I could not complete the request.",
                })
            return job

    with tempfile.TemporaryDirectory(prefix="c4-review-") as directory:
        owner, _, _, _ = runtime(Path(directory), AckWorker)
        bundle = await owner.run(GlobalResearchRunRequest(
            run_id="ack", ticker="MU", research_brief="probe"
        ))
        results["failed_network_ack_overrides_valid_file"] = {
            "status": bundle.c4_product_status["entity_network_report"],
            "accepted_report": bundle.entity_network_report,
        }

    schema = json.loads((REPO / "prompts/codex_v2/document1/schemas/c4e_relations.schema.json")
                        .read_text(encoding="utf-8"))
    raw = {
        "status": "completed", "summary": "probe", "warnings": [], "report_markdown": "",
        "entity_relations": [
            {"关系主体": "MU", "关系对象": "NVIDIA", "关系类型": "adaptation",
             "关系说明": "supported relationship", "关联业务或产品": "HBM"},
            {"关系主体": "bad row"},
        ],
    }
    try:
        recover_node_output(CodexD1Node.C4E_FORMAL_SCAN, json.dumps(raw), output_schema=schema)
    except Exception as exc:
        results["one_bad_relation_discards_good_rows"] = {"error": str(exc), "good_rows": 1}

    with tempfile.TemporaryDirectory(prefix="c4-review-") as directory:
        owner, worker, _, repository = runtime(Path(directory), max_attempts=2)
        original = owner._node_runner._record_usage

        def usage_failure(request, job, **kwargs):
            if request.node is CodexD1Node.C4F_FUTURE_NODES:
                raise OSError("simulated optional usage sink unavailable")
            return original(request, job, **kwargs)

        owner._node_runner._record_usage = usage_failure
        bundle = await owner.run(GlobalResearchRunRequest(
            run_id="usage", ticker="MU", research_brief="probe"
        ))
        attempts = [x for x in repository.list_attempts("usage")
                    if x.node is CodexD1Node.C4F_FUTURE_NODES]
        results["usage_sink_failure_reruns_valid_research"] = {
            "research_calls": sum(x.node is CodexD1Node.C4F_FUTURE_NODES for x in worker.requests),
            "attempt_statuses": [x.status.value for x in attempts],
            "products": bundle.c4_product_status,
        }

    (ROOT / "probe_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
