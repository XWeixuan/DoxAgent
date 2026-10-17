import json
from datetime import UTC, datetime

import pytest

from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.substeps import durable, settle_stage
from doxagent.v2_read.content_files import ContentUnavailable


def test_targeted_reads_do_not_expand_unrelated_child_receipts(tmp_path):
    repo = InitializationRepository(tmp_path / "control.sqlite3")
    run = repo.submit(
        "COHR",
        datetime.now(UTC),
        [
            NodeSpec(key="d3", block="D3"),
            NodeSpec(key="d3.old", block="D3", inputs={"managed_by": "d3"}),
        ],
    )
    # Unavailable archived child content must not be read to schedule its root.
    with repo._connection() as db:
        node = repo._node(db, run.initialization_id, "d3.old")
    payload = node.model_dump(mode="json")
    payload["receipt"] = {"$doxagent_content_v1": "a" * 64, "bytes": 20000}
    with repo._write() as db:
        db.execute(
            "UPDATE initialization_nodes SET payload=? WHERE run_id=? AND node_key=?",
            (json.dumps(payload), run.initialization_id, "d3.old"),
        )
    assert repo.node(run.initialization_id, "d3").key == "d3"
    assert [n.key for n in repo.nodes(run.initialization_id, top_level_only=True)] == ["d3"]
    # Selecting that child still enforces its content integrity.
    with pytest.raises(ContentUnavailable):
        repo.node(run.initialization_id, "d3.old")
    with pytest.raises(ContentUnavailable):
        repo.nodes(run.initialization_id)


def test_root_selection_preserves_ownership_when_inputs_are_externalized(tmp_path):
    repo = InitializationRepository(tmp_path / "control.sqlite3")
    run = repo.submit(
        "COHR",
        datetime.now(UTC),
        [
            NodeSpec(key="d3", block="D3"),
            NodeSpec(key="d3.large", block="D3", inputs={"managed_by": "d3", "data": "x" * 20000}),
        ],
    )
    assert [n.key for n in repo.nodes(run.initialization_id, top_level_only=True)] == ["d3"]


@pytest.mark.asyncio
async def test_scheduler_and_durable_stages_never_scan_all_child_payloads(tmp_path, monkeypatch):
    repo = InitializationRepository(tmp_path / "control.sqlite3")
    run = repo.submit("COHR", datetime.now(UTC), [NodeSpec(key="d3", block="D3")])
    original = repo.nodes

    def roots_only(run_id, *, top_level_only=False):
        assert top_level_only, "hot execution path expanded the entire history"
        return original(run_id, top_level_only=True)

    monkeypatch.setattr(repo, "nodes", roots_only)

    class Stages:
        calls = 0

        @durable("d3_v21")
        async def stage(self, *, key, run_id, owner, phase):
            self.calls += 1
            return {"files": {"result": "accepted"}}

        async def reconcile(self, context):
            return None

        async def execute(self, context):
            await self.stage(
                key="supplement:OPEN:0", run_id=run.initialization_id, owner="OPEN", phase="build"
            )
            settle_stage("build:OPEN", durable_key="supplement:OPEN:0")
            return NodeResult()

    stages = Stages()
    result = await InitializationWorker(repo, lambda _: stages).run_once()
    assert result.status == "SUCCEEDED"
    assert stages.calls == 1
    assert repo.node(run.initialization_id, "d3.build:OPEN:supplement:OPEN:0").status == "SUCCEEDED"
