from types import SimpleNamespace

import httpx
import pytest

from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
from doxagent.codex_worker.app import create_worker_app


@pytest.mark.asyncio
async def test_snapshot_fork_requires_both_capabilities_and_readiness_uses_selected_model(tmp_path):
    secret, bearer = "s" * 32, "b" * 32

    async def probe():
        return {"authenticated": True, "models": ["offline-model"]}

    app = create_worker_app(
        workspace_root=str(tmp_path),
        bearer_token=bearer,
        capability_secret=secret,
        runtime=SimpleNamespace(probe=probe),
    )
    app.state.workspaces.write_text("source", "context/input.json", "{}")
    codec = CapabilityTokenCodec(secret)
    headers = {
        "Authorization": f"Bearer {bearer}",
        "X-Workspace-Capability": codec.issue(run_id="source", operations={"snapshot"}),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://offline"
    ) as client:
        assert (
            await client.post("/v1/workspaces/source/snapshots/s1", headers=headers)
        ).status_code == 200
        url = "/v1/workspaces/source/snapshots/s1/fork/new"
        assert (await client.post(url, headers=headers)).status_code == 403
        headers["X-Destination-Capability"] = codec.issue(run_id="different", operations={"write"})
        assert (await client.post(url, headers=headers)).status_code == 403
        headers["X-Destination-Capability"] = codec.issue(run_id="new", operations={"write"})
        assert (await client.post(url, headers=headers)).status_code == 200
        assert (await client.post(url, headers=headers)).status_code == 200
        headers["X-Workspace-Capability"] = codec.issue(
            run_id="readiness", operations={"readiness"}
        )
        assert (
            await client.get("/v1/readiness?model=offline-model", headers=headers)
        ).status_code == 200
        assert (
            await client.get("/v1/readiness?model=unavailable", headers=headers)
        ).status_code == 503
        headers["X-Workspace-Capability"] = CapabilityTokenCodec("x" * 32).issue(
            run_id="readiness", operations={"readiness"}
        )
        assert (
            await client.get("/v1/readiness?model=offline-model", headers=headers)
        ).status_code == 403
