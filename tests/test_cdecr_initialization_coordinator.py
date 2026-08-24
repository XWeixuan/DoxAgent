from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdecr.contracts import SourceMessage
from doxagent.cdecr_integration.contracts import (
    CDECRWorkflowResult,
    RuntimeRegistryBinding,
    TickerJobStage,
)
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
)
from doxagent.monitoring.schema import (
    FetchedExternalMessage,
    InterfaceType,
    SourceType,
)


class OneArticleProvider:
    provider_id = "benzinga_news"

    def __init__(self, as_of: datetime) -> None:
        self.calls = 0
        self.as_of = as_of

    def fetch(
        self, *, ticker: str, window_start: datetime, window_end: datetime
    ) -> Sequence[FetchedExternalMessage]:
        self.calls += 1
        return [
            FetchedExternalMessage(
                source_id=self.provider_id,
                binding_id=f"{ticker}:{self.provider_id}",
                ticker=ticker,
                source_type=SourceType.MEDIA,
                interface_type=InterfaceType.BY_TICKER,
                raw_payload={
                    "id": "article-1",
                    "title": f"{ticker} announces a material product milestone",
                    "body": (
                        f"{ticker} announced a concrete product milestone with enough "
                        "operational detail for deterministic event extraction. " * 12
                    ),
                    "url": "https://example.com/article-1",
                    "created": self.as_of.isoformat(),
                    "stocks": [{"name": ticker}],
                    "author": "Reporter",
                },
                provider_message_id="article-1",
                source_url="https://example.com/article-1",
                source_published_at=self.as_of,
            )
        ]


class FakeRegistry:
    def __init__(self) -> None:
        self.sources: dict[str, SourceMessage] = {}
        self.fingerprints: set[str] = set()
        self.epoch_finalized = False

    def has_source_fingerprint(self, fingerprint: str) -> bool:
        return fingerprint in self.fingerprints

    def save_source(self, source: SourceMessage, *, fingerprint: str) -> None:
        self.sources[source.message_id] = source
        self.fingerprints.add(fingerprint)

    def get_source(self, message_id: str) -> SourceMessage | None:
        return self.sources.get(message_id)

    def get_bulk_epoch(self, epoch_id: str) -> dict[str, str] | None:
        if self.epoch_finalized and epoch_id == "epoch-1":
            return {"epoch_id": epoch_id, "status": "FINALIZED"}
        return None

    def list_current_atomic_events(self, *, limit: int) -> list[Any]:
        if not self.sources:
            return []
        return [SimpleNamespace(event_id="A1", mention_ids=["M1"])]

    def get_mention(self, mention_id: str) -> Any:
        message_id = next(iter(self.sources))
        return SimpleNamespace(message_id=message_id) if mention_id == "M1" else None

    def list_current_packages(self, *, limit: int) -> list[Any]:
        return []


class FakeRunner:
    def __init__(self, binding: RuntimeRegistryBinding, registry: FakeRegistry) -> None:
        self.binding = binding
        self.registry = registry
        self.run_calls = 0

    def run(self, message_ids: Sequence[str]) -> CDECRWorkflowResult:
        self.run_calls += 1
        self.registry.epoch_finalized = True
        return CDECRWorkflowResult(
            market=self.binding.market,
            ticker=self.binding.ticker,
            runtime_scope=self.binding.runtime_scope,
            status="FINALIZED",
            message_ids=list(message_ids),
            document_count=len(message_ids),
            eligible_document_count=len(message_ids),
            epoch_id="epoch-1",
            completed_at=datetime.now(UTC),
        )

    def freeze_finalized_snapshot(
        self,
        *,
        epoch_id: str,
        as_of: datetime,
        eligible_atomic_ids: set[str] | None = None,
    ) -> FrozenRuntimeSnapshot:
        assert eligible_atomic_ids == {"A1"}
        return FrozenRuntimeSnapshot(
            snapshot_id="runtime-snapshot:coordinator",
            runtime_scope=self.binding.runtime_scope,
            epoch_id=epoch_id,
            market=self.binding.market,
            ticker=self.binding.ticker,
            as_of=as_of,
            atomics=[
                FrozenRuntimeAtomic(
                    runtime_atomic_id="A1",
                    version=1,
                    proposition="The issuer announced a material product milestone.",
                    time=as_of.date().isoformat(),
                    assertion_state=CanonicalAssertionState.ACTUAL,
                    entities=[self.binding.ticker],
                    runtime_package_ids=[],
                )
            ],
            packages=[],
        )


@pytest.mark.asyncio
async def test_prepare_only_job_resumes_without_refetching_or_rerunning_cdecr(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    provider = OneArticleProvider(as_of)
    registry = FakeRegistry()
    created_runners: list[FakeRunner] = []

    def runtime_factory(
        binding: RuntimeRegistryBinding,
    ) -> tuple[Any, Any]:
        runner = FakeRunner(binding, registry)
        created_runners.append(runner)
        return registry, runner

    coordinator = TickerCDECRPipelineCoordinator(
        registry_root=tmp_path / "registries",
        state_root=tmp_path / "state",
        event_library_root=tmp_path / "libraries",
        providers=[provider],
        runtime_factory=runtime_factory,
    )
    first = await coordinator.initialize(
        market="US",
        ticker="NEW",
        as_of=as_of,
        export_dir=tmp_path / "exports",
        run_o2=False,
    )
    second = await coordinator.initialize(
        market="US",
        ticker="NEW",
        as_of=as_of,
        export_dir=tmp_path / "exports",
        run_o2=False,
    )

    assert first.job.stage is TickerJobStage.DELTA_READY
    assert second.job.stage is TickerJobStage.DELTA_READY
    assert first.delta_batch_id == second.delta_batch_id
    assert provider.calls == 1
    assert sum(runner.run_calls for runner in created_runners) == 1
    assert coordinator.status(market="US", ticker="NEW") == second.job

