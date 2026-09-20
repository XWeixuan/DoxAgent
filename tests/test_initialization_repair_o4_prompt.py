from __future__ import annotations

import hashlib
import json

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.workflows.codex_monitoring_o4.repository import MonitoringO4Repository
from doxagent.workflows.codex_monitoring_o4.runner import MonitoringO4AgentRunner
from doxagent.workflows.codex_monitoring_o4.schema import O4Request
from tests.test_codex_monitoring_o4 import _policy, _Worker, _Workspace


async def test_o4_reuses_frozen_repair_prompt_for_same_request(tmp_path):
    policy = _policy()
    repository = MonitoringO4Repository(tmp_path / "o4.sqlite3")
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    worker = _Worker(digest)
    supplement = ["first"]
    observed: list[dict[str, object]] = []

    def transform(request):
        prompt = request.prompt + "\n" + supplement[0]
        return request.model_copy(update={"prompt": prompt}), {
            "candidate_prompt": prompt,
            "supplement_sha256": supplement[0],
        }

    def observer(request, metadata):
        observed.append(metadata)

    runner = MonitoringO4AgentRunner(
        worker=worker,  # type: ignore[arg-type]
        workspace=_Workspace(),  # type: ignore[arg-type]
        repository=repository,
        initialization_prompt_transform=transform,
        initialization_prompt_observer=observer,
    )
    request = O4Request(
        request_id="o4-init-request",
        ticker="MU",
        node=CodexMonitoringO4Node.CONFIGURE,
        payload={
            "policy_set_json": policy,
            "document2_json": {"shells": []},
            "policy_set_sha256": digest,
        },
        dedupe_key="o4-init-request",
        initialization_id="init-mu-123",
    )
    await runner.run(request)
    supplement[0] = "second"
    await runner.run(request)
    assert worker.requests[0].prompt == worker.requests[1].prompt
    assert worker.requests[0].prompt.endswith("first")
    assert len(observed) == 1
    assert observed[0]["supplement_sha256"] == "first"
    repository.close()
