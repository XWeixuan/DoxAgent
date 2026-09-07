"""Prepare immutable activation references, commit once, and await real consumer ACKs."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository

from .schema import NodeResult
from .service import NodeContext


class ActivationAdapter:
    def __init__(self, settings: DoxAgentSettings) -> None:
        self.settings = settings

    async def reconcile(self, context: NodeContext) -> NodeResult | None:
        return await self.execute(context)

    async def execute(self, context: NodeContext) -> NodeResult:
        identity = context.run.initialization_id + "-activation"
        if context.node.key == "activation.prepare":
            refs: dict[str, Any] = {}
            for dependency in context.node.dependencies:
                refs.update(context.dependency(dependency).artifacts)
            refs.update(context.node.inputs.get("artifacts", {}))
            if context.run.operation_kind in {"ACTIVATE", "REPLACE_ARTIFACT", "ROLLBACK"}:
                from .configuration import CandidateConfiguration, candidate_bus_path

                source = refs["monitoring_configuration"]["initialization_id"]
                candidate = CandidateConfiguration(
                    self.settings.message_bus_v2_sqlite_path,
                    context.run.initialization_id,
                    context.run.ticker,
                )
                active = context.repository.active_revision(context.run.ticker)
                active_configuration = (
                    active["artifacts"].get("monitoring_configuration", {}).get("initialization_id")
                    if active
                    else None
                )
                # Replacing research while retaining the current configuration must
                # preserve its effective human edits, not reinstall the original snapshot.
                snapshot_from = (
                    self.settings.message_bus_v2_sqlite_path
                    if source == active_configuration and context.run.operation_kind != "ROLLBACK"
                    else candidate_bus_path(self.settings.message_bus_v2_sqlite_path, source)
                )
                path = candidate.prepare(snapshot_from=snapshot_from)
                refs["monitoring_configuration"] = {
                    **refs["monitoring_configuration"],
                    "initialization_id": context.run.initialization_id,
                    "database": str(path),
                }
            policies = SQLiteDocument3PolicyRepository(self.settings.codex_runtime_sqlite_path)
            policy = policies.get_projection(context.run.ticker, refs["document3"]["version"])
            reader = PublishedEventLibraryReader(
                refs["event_library"].get("root") or self.settings.event_library_root or "",
                market="US",
            )
            index = reader.known_index(context.run.ticker, version=refs["event_library"]["version"])
            if policy is None or index is None:
                raise ValueError("activation has no usable Index/Projection")
            self._documents_available(context.run.ticker, refs)
            await self._w3_ready()
            # Only references enter the revision; bodies remain in local artifact storage.
            selected = {
                key: refs[key]
                for key in (
                    "document1",
                    "document2",
                    "event_library",
                    "document3",
                    "monitoring_configuration",
                )
            }
            revision = context.repository.stage_revision(context.lease, identity, selected)
            return NodeResult(artifacts={"activation_revision": revision})
        revision = context.dependency("activation.prepare").artifacts["activation_revision"]
        identity = revision["revision_id"]
        if context.node.key == "activation.commit":
            context.repository.activate(context.lease, identity)
            return NodeResult(artifacts={"revision_id": identity})
        worker = {"bus.ready": "bus", "runtime.ready": "runtime"}.get(context.node.key)
        if worker is None:
            raise ValueError(f"unknown activation node: {context.node.key}")
        deadline = time.monotonic() + float(context.node.inputs.get("startup_timeout_seconds", 180))
        while True:
            active = context.repository.active_revision(context.run.ticker)
            if active is None or active["revision_id"] != identity:
                raise ValueError("activation was superseded before readiness")
            if context.repository.revision_acknowledged(context.run.ticker, identity, worker):
                if worker == "runtime":
                    await self._w3_ready()
                    if not context.repository.revision_acknowledged(
                        context.run.ticker, identity, "bus"
                    ):
                        raise ValueError("Bus readiness expired before Runtime completion")
                return NodeResult(artifacts={"revision_id": identity, "worker": worker})
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"{worker} did not acknowledge activation within startup timeout"
                )
            await asyncio.sleep(1)

    async def _w3_ready(self) -> None:
        import httpx

        from doxagent.codex_runtime.capabilities import CapabilityTokenCodec

        token = CapabilityTokenCodec(self.settings.codex_capability_secret or "").issue(
            run_id="readiness", operations={"readiness"}
        )

        # Readiness only: never dispatch a synthetic event or model turn.
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                self.settings.codex_worker_base_url.rstrip("/") + "/v1/readiness",
                params={"model": self.settings.persistent_runtime_v2_w3_model},
                headers={
                    "Authorization": f"Bearer {self.settings.codex_worker_bearer_token}",
                    "X-Workspace-Capability": token,
                },
            )
            response.raise_for_status()
            if not response.json().get("ready"):
                raise ValueError("W3 Codex Worker is not ready")

    def _documents_available(self, ticker: str, refs: dict[str, Any]) -> None:
        from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository

        repository = SQLiteCodexRuntimeRepository(self.settings.codex_runtime_sqlite_path)
        for role, artifact_field in (
            ("document1", "document_artifact_id"),
            ("document2", "document2_artifact_id"),
        ):
            run_id = refs[role]["run_id"]
            bundle = repository.get_bundle(run_id)
            handoff = getattr(bundle, "handoff", None)
            artifact = getattr(handoff, artifact_field, None)
            if bundle is None or bundle.ticker.upper() != ticker or not artifact:
                raise ValueError(f"{role} handoff is unavailable for this ticker")
            body = repository.get_published_document(run_id, artifact)
            if body is None or body.content_text is None:
                raise ValueError(f"{role} published body is unavailable locally")
            if hashlib.sha256(body.content_text.encode("utf-8")).hexdigest() != body.sha256:
                raise ValueError(f"{role} published body checksum mismatch")

    async def compensate(self, context: NodeContext) -> None:
        # First initialization remains failed for exact resume. Replacement rollback
        # is CAS protected; real consumers reinstall/reload the base revision.
        context.repository.rollback_revision(
            context.lease, context.run.initialization_id + "-activation"
        )
