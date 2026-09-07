import asyncio
import hashlib
from unittest.mock import AsyncMock, Mock

import pytest

from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    CodexD1Node,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    PublishedDocument,
    ResearchLane,
)
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import InitializationRepository, InitializationWorker
from doxagent.ticker_initialization.activation_adapter import ActivationAdapter
from doxagent.ticker_initialization.configuration import CandidateConfiguration
from doxagent.ticker_initialization.consumers import admit_bus_revisions, admit_runtime_revisions
from doxagent.ticker_initialization.operations import replace_artifact, submit_activation
from doxagent.ticker_initialization.runtime_inputs import ActivatedRuntimeInputs
from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
from tests.test_codex_document3_workflow import NOW, _policy_set, _seed_published_d2
from tests.test_phase25_runtime_scheduler import _missing_bundle, _scheduler


@pytest.mark.asyncio
async def test_real_startup_handshake_replacement_rollback_and_exact_resume(tmp_path, monkeypatch):
    control = InitializationRepository(tmp_path / "control.db")
    runtime_path, bus_path = tmp_path / "runtime.db", tmp_path / "bus.db"
    repository = SQLiteCodexRuntimeRepository(runtime_path)
    _seed_published_d2(repository, partial=True)
    repository.save_bundle(
        GlobalResearchBundle(
            run_id="d1-new",
            ticker="MU",
            status="published",
            published_at=NOW,
            handoff=GlobalResearchHandoffV1(
                run_id="d1-new", ticker="MU", document_artifact_id="d1-body", published_at=NOW
            ),
        )
    )
    repository.save_artifact(ArtifactRef(
        workflow_version="codex_global_research_v1", research_lane=ResearchLane.GLOBAL_RESEARCH,
        run_id="d1-new", artifact_id="d1-body", node=CodexD1Node.ASSEMBLE,
        attempt_id="offline-assemble", kind=ArtifactKind.BUNDLE, relative_path="artifacts/d1.json",
        sha256=hashlib.sha256(b"{}").hexdigest(), size_bytes=2, content_type="application/json",
        published=True,
    ))
    repository.save_published_document(
        PublishedDocument(
            run_id="d1-new",
            artifact_id="d1-body",
            artifact_kind="bundle",
            content_type="application/json",
            content_text="{}",
            size_bytes=2,
            sha256=hashlib.sha256(b"{}").hexdigest(),
            published_at=NOW,
        )
    )
    events = EventLibraryRepository(tmp_path / "events" / "US" / "MU" / "event_library.sqlite3")
    events.ensure_empty_publication("MU")
    policies = SQLiteDocument3PolicyRepository(runtime_path)
    policies.publish(_policy_set(), expected_base_version=None)
    version = policies.reserve_version("MU", "candidate-two")
    policies.publish_candidate(_policy_set(version), run_id="candidate-two")
    bus = MessageBusV2Service(MessageBusV2Repository(bus_path))
    bus.bootstrap()
    CandidateConfiguration(bus_path, "offline-source", "MU").prepare()
    scheduler, provider, _, legacy_runtime = _scheduler(_missing_bundle())
    scheduler.runtime_v2_service = Mock(
        input_snapshot_loader=ActivatedRuntimeInputs(
            control, PublishedEventLibraryReader(tmp_path / "events"), policies
        )
    )
    scheduler.message_bus_v2_service = bus
    scheduler.message_bus_v2_enabled = True
    settings = DoxAgentSettings(_env_file=None).model_copy(
        update={
            "codex_runtime_sqlite_path": str(runtime_path),
            "message_bus_v2_sqlite_path": str(bus_path),
            "event_library_root": str(tmp_path / "events"),
        }
    )
    adapter = ActivationAdapter(settings)
    monkeypatch.setattr(adapter, "_w3_ready", AsyncMock())
    first = submit_activation(
        control,
        "MU",
        cutoff=NOW,
        reason="offline first activation",
        artifacts={
            "document1": {"run_id": "d1-new"},
            "document2": {"run_id": "d2-mu"},
            "document3": {"version": 1},
            "event_library": {"version": 1},
            "monitoring_configuration": {"initialization_id": "offline-source"},
        },
    )

    async def drive(*, runtime_ready=True):
        stop = asyncio.Event()

        async def consumers():
            while not stop.is_set():
                admit_bus_revisions(control, bus)
                if runtime_ready:
                    admit_runtime_revisions(control, scheduler)
                await asyncio.sleep(0.01)

        task = asyncio.create_task(consumers())
        try:
            return await InitializationWorker(control, lambda _: adapter).run_once()
        finally:
            stop.set()
            await task

    try:
        result = await drive()
        assert result.status == "SUCCEEDED", result.error
        first_revision = first.initialization_id + "-activation"
        assert result.phase == "VERIFY_READY"
        assert control.revision_acknowledged("MU", first_revision, "bus")
        assert control.revision_acknowledged("MU", first_revision, "runtime")
        assert provider.initialize_calls == 0
        replacement = replace_artifact(
            control,
            "MU",
            role="document3",
            reference={"version": version},
            reason="offline replacement",
        )
        # Deterministic zero-wait failure only at Runtime startup; do not shorten the lease.
        original_execute = adapter.execute

        async def execute(context):
            if context.node.key == "runtime.ready":
                context.node.inputs["startup_timeout_seconds"] = 0
            return await original_execute(context)

        monkeypatch.setattr(adapter, "execute", execute)
        result = await drive(runtime_ready=False)
        assert result.status == "FAILED"
        assert control.active_revision("MU")["revision_id"] == first_revision
        admit_bus_revisions(control, bus)
        assert (
            CandidateConfiguration(bus_path, first.initialization_id, "MU").current_head()
            == first.initialization_id
        )
        completed_prepare = control.nodes(replacement.initialization_id)[0]
        control.resume(
            replacement.initialization_id, node_key="runtime.ready", reason="runtime restored"
        )
        monkeypatch.setattr(adapter, "execute", original_execute)
        result = await drive()
        assert result.status == "SUCCEEDED", result.error
        assert control.nodes(replacement.initialization_id)[0] == completed_prepare
        active = control.active_revision("MU")
        assert active["artifacts"]["document1"] == {"run_id": "d1-new"}
        assert active["artifacts"]["document2"] == {"run_id": "d2-mu"}
        assert active["artifacts"]["document3"] == {"version": version}
        assert scheduler.repository.get_state("MU").metadata["initial_offset"] == 0
    finally:
        bus.repository.close()
