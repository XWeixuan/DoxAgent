"""Pinned Document1 and optional external inputs for Document2."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from doxagent.codex_runtime.client import WorkspaceClient
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    CodexD1Node,
    GlobalResearchBundle,
)
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.models import AgentName, AgentPermissions
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolRequest
from doxagent.workflows.codex_document2.schema import (
    Document2InputManifest,
    InputAvailability,
    InputManifestEntry,
)

EVENT_LIBRARY_PORT_VERSION = "event-library-read-v2"


class PreparedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OptionalInput(PreparedModel):
    status: InputAvailability
    payload: dict[str, Any] | str | None = None
    source_run_id: str | None = None
    as_of: datetime | None = None
    warning: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventLibraryProvider(Protocol):
    interface_version: str
    read_only: bool

    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput: ...


class UnconfiguredEventLibraryProvider:
    interface_version = EVENT_LIBRARY_PORT_VERSION
    read_only = True

    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput:
        del ticker, as_of
        return OptionalInput(
            status=InputAvailability.NOT_CONFIGURED,
            warning="Event Library integration is reserved but not configured.",
            metadata={
                "interface_version": self.interface_version,
                "read_only": self.read_only,
            },
        )


class PublishedEventLibraryProvider:
    """D2 reserved read-only port backed only by the Published Reference View."""

    interface_version = EVENT_LIBRARY_PORT_VERSION
    read_only = True

    def __init__(
        self,
        reader: PublishedEventLibraryReader,
        *,
        pinned_version: int | None = None,
        pinned_sha256: str | None = None,
        pinned_published_at: datetime | None = None,
    ) -> None:
        self._reader = reader
        self._pinned_version = pinned_version
        self._pinned_sha256 = pinned_sha256
        self._pinned_published_at = pinned_published_at

    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput:
        snapshot = self._reader.reference_view(ticker, version=self._pinned_version)
        if snapshot is None:
            return OptionalInput(
                status=InputAvailability.ABSENT,
                warning="No Published Event Library version exists for this ticker.",
                metadata={
                    "interface_version": self.interface_version,
                    "read_only": self.read_only,
                },
            )
        if snapshot.published_at is not None and snapshot.published_at > as_of:
            return OptionalInput(
                status=InputAvailability.ABSENT,
                warning="Published Event Library head is newer than the Document2 cutoff.",
                metadata={
                    "interface_version": self.interface_version,
                    "read_only": self.read_only,
                    "version": snapshot.version,
                },
            )
        if self._pinned_sha256 is not None and snapshot.sha256 != self._pinned_sha256:
            raise ValueError("Pinned Event Library reference view hash mismatch")
        if (
            self._pinned_published_at is not None
            and snapshot.published_at != self._pinned_published_at
        ):
            raise ValueError("Pinned Event Library published timestamp mismatch")
        return OptionalInput(
            status=InputAvailability.AVAILABLE,
            payload=snapshot.reference_view,
            source_run_id=f"event-library:{ticker.upper()}:v{snapshot.version}",
            as_of=snapshot.published_at,
            metadata={
                "interface_version": self.interface_version,
                "read_only": self.read_only,
                "version": snapshot.version,
                "sha256": snapshot.sha256,
                "view": "REFERENCE_VIEW",
                "content_type": "text/markdown; charset=utf-8",
                "contract_version": snapshot.contract_version,
            },
        )


class NarrativeReportProvider(Protocol):
    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput: ...


class DoxAtlasNarrativeReportProvider:
    """Fetch the latest completed report and apply the agreed seven-day freshness rule."""

    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools

    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput:
        request = ToolRequest(
            tool_name="doxa_get_narrative_report",
            ticker=ticker,
            agent_name=AgentName.O1_EXPECTATION_OWNER,
            input={"ticker": ticker, "view": "agent_provenance"},
            metadata={"workflow_version": "codex_document2_v1", "prefetch": True},
        )
        try:
            result = await asyncio.to_thread(
                self._tools.call,
                request,
                AgentPermissions(allowed_tools=["doxa_get_narrative_report"]),
            )
        except Exception as exc:
            return OptionalInput(
                status=InputAvailability.UNAVAILABLE,
                warning=f"DoxAtlas narrative lookup failed: {_bounded(str(exc))}",
            )
        if not result.succeeded:
            message = result.error.message if result.error is not None else "unknown error"
            lowered = message.lower()
            status = (
                InputAvailability.ABSENT
                if "not found" in lowered or "no narrative" in lowered
                else InputAvailability.UNAVAILABLE
            )
            return OptionalInput(status=status, warning=message)
        output = result.output if isinstance(result.output, dict) else {}
        nested = output.get("data")
        raw: dict[str, Any] = nested if isinstance(nested, dict) else output
        run_ref = raw.get("run_ref")
        if not isinstance(run_ref, dict):
            return OptionalInput(
                status=InputAvailability.UNAVAILABLE,
                warning="DoxAtlas narrative response is missing run_ref.",
            )
        completed_at = _parse_datetime(run_ref.get("completed_at"))
        if completed_at is None:
            return OptionalInput(
                status=InputAvailability.UNAVAILABLE,
                source_run_id=_optional_string(run_ref.get("run_id")),
                warning="DoxAtlas narrative response is missing completed_at.",
            )
        reference_time = as_of.astimezone(UTC)
        age = reference_time - completed_at
        if age < timedelta(0) or age > timedelta(days=7):
            return OptionalInput(
                status=InputAvailability.ABSENT,
                source_run_id=_optional_string(run_ref.get("run_id")),
                as_of=completed_at,
                warning="Latest completed DoxAtlas narrative report is outside the 7-day window.",
                metadata={"age_seconds": age.total_seconds()},
            )
        return OptionalInput(
            status=InputAvailability.AVAILABLE,
            payload=raw,
            source_run_id=_optional_string(run_ref.get("run_id")),
            as_of=completed_at,
            metadata={
                "view": raw.get("view"),
                "map_version": raw.get("map_version"),
            },
        )


class GlobalResearchInput(PreparedModel):
    run_id: str
    ticker: str
    published_at: datetime
    reports: dict[str, str]
    report_artifact_ids: dict[str, str]
    entity_relations: list[dict[str, Any]] = Field(default_factory=list)
    future_nodes: list[dict[str, Any]] = Field(default_factory=list)
    horizontal_collection: dict[str, Any] = Field(default_factory=dict)
    horizontal_artifact_id: str | None = None
    document_artifact_id: str
    citation_manifest_artifact_id: str | None = None


class PreparedDocument2Inputs(PreparedModel):
    ticker: str
    as_of: datetime
    global_research: GlobalResearchInput
    narrative_research: OptionalInput
    event_library: OptionalInput
    manifest: Document2InputManifest


class Document2InputLoader:
    def __init__(
        self,
        *,
        repository: CodexRuntimeRepository,
        workspace: WorkspaceClient,
        narrative_provider: NarrativeReportProvider,
        event_library_provider: EventLibraryProvider | None = None,
    ) -> None:
        self._repository = repository
        self._workspace = workspace
        self._narrative = narrative_provider
        self._events = event_library_provider or UnconfiguredEventLibraryProvider()

    async def load(
        self,
        *,
        source_global_run_id: str,
        requested_ticker: str | None,
        requested_as_of: datetime | None,
    ) -> PreparedDocument2Inputs:
        bundle = self._repository.get_bundle(source_global_run_id)
        if not isinstance(bundle, GlobalResearchBundle) or bundle.status != "published":
            raise ValueError("source_global_run_id must reference a published Global Research run")
        if requested_ticker and requested_ticker.upper() != bundle.ticker.upper():
            raise ValueError("Document2 ticker does not match the source Global Research run")
        if bundle.handoff is None or bundle.published_at is None:
            raise ValueError("published Global Research bundle is missing its handoff")
        source_published_at = bundle.published_at
        if source_published_at.tzinfo is None:
            source_published_at = source_published_at.replace(tzinfo=UTC)
        requested_cutoff = requested_as_of
        as_of = requested_cutoff or source_published_at
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        cutoff_warning: str | None = None
        if as_of < source_published_at:
            cutoff_warning = (
                "requested Document2 as_of preceded the pinned Global Research publication; "
                "the effective research cutoff was raised to the source publication time"
            )
            as_of = source_published_at
        reports: dict[str, str] = {}
        report_ids: dict[str, str] = {}
        paths: list[str] = []
        for role in ("c1", "c3", "c5"):
            reference = bundle.reports.get(role)
            if reference is None:
                raise ValueError(f"Global Research bundle is missing required {role} report")
            file = await self._workspace.read_text(source_global_run_id, reference.relative_path)
            if file.content is None or file.sha256 != reference.sha256:
                raise ValueError(f"Global Research {role} artifact failed checksum verification")
            reports[role] = _qualify_d1_aliases(file.content)
            report_ids[role] = reference.artifact_id
            paths.append(reference.relative_path)

        horizontal: dict[str, Any] = {}
        horizontal_id: str | None = None
        horizontal_paths: list[str] = []
        candidates = [
            item
            for item in self._repository.list_artifacts(source_global_run_id, limit=500)
            if item.node is CodexD1Node.PROGRAM_COLLECTION and item.kind is ArtifactKind.BUNDLE
        ]
        for reference in reversed(candidates):
            try:
                file = await self._workspace.read_text(
                    source_global_run_id, reference.relative_path
                )
                if file.content is not None and file.sha256 == reference.sha256:
                    import json

                    horizontal = json.loads(file.content)
                    horizontal_id = reference.artifact_id
                    horizontal_paths = [reference.relative_path]
                    break
            except (OSError, ValueError):
                continue

        narrative, events = await asyncio.gather(
            _safe_optional_load(
                self._narrative,
                ticker=bundle.ticker,
                as_of=as_of,
                label="Narrative Research",
            ),
            _safe_optional_load(
                self._events,
                ticker=bundle.ticker,
                as_of=as_of,
                label="Event Library",
            ),
        )
        global_input = GlobalResearchInput(
            run_id=bundle.run_id,
            ticker=bundle.ticker,
            published_at=bundle.published_at,
            reports=reports,
            report_artifact_ids=report_ids,
            entity_relations=[
                cast(
                    dict[str, Any],
                    _qualify_d1_context(item.model_dump(mode="json", by_alias=True)),
                )
                for item in bundle.entity_relations
            ],
            future_nodes=[
                cast(
                    dict[str, Any],
                    _qualify_d1_context(item.model_dump(mode="json", by_alias=True)),
                )
                for item in bundle.future_nodes
            ],
            horizontal_collection=cast(dict[str, Any], _qualify_d1_context(horizontal)),
            horizontal_artifact_id=horizontal_id,
            document_artifact_id=bundle.handoff.document_artifact_id,
            citation_manifest_artifact_id=bundle.handoff.citation_manifest_artifact_id,
        )
        global_manifest = InputManifestEntry(
            status=InputAvailability.AVAILABLE,
            artifact_ids=[
                *report_ids.values(),
                *([horizontal_id] if horizontal_id else []),
                bundle.handoff.document_artifact_id,
            ],
            workspace_paths=[*paths, *horizontal_paths],
            source_run_id=bundle.run_id,
            as_of=bundle.published_at,
            warning=cutoff_warning,
            metadata={
                "requested_as_of": (
                    requested_cutoff.isoformat() if requested_cutoff is not None else None
                ),
                "effective_research_cutoff_at": as_of.isoformat(),
            },
        )
        return PreparedDocument2Inputs(
            ticker=bundle.ticker,
            as_of=as_of,
            global_research=global_input,
            narrative_research=narrative,
            event_library=events,
            manifest=Document2InputManifest(
                global_research=global_manifest,
                narrative_research=_manifest_entry(narrative),
                event_library=_manifest_entry(events),
            ),
        )


def _manifest_entry(value: OptionalInput) -> InputManifestEntry:
    return InputManifestEntry(
        status=value.status,
        source_run_id=value.source_run_id,
        as_of=value.as_of,
        warning=value.warning,
        metadata=value.metadata,
    )


def _qualify_d1_aliases(value: str) -> str:
    return re.sub(r"【cite:(O[1-9]\d*)】", r"【cite:D1-\1】", value)


def _qualify_d1_context(value: Any) -> Any:
    """Qualify legacy aliases throughout pinned Document1 structured context."""
    if isinstance(value, str):
        return _qualify_d1_aliases(value)
    if isinstance(value, list):
        return [_qualify_d1_context(item) for item in value]
    if isinstance(value, dict):
        return {key: _qualify_d1_context(item) for key, item in value.items()}
    return value


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None and str(value).strip() else None


async def _safe_optional_load(
    provider: NarrativeReportProvider | EventLibraryProvider,
    *,
    ticker: str,
    as_of: datetime,
    label: str,
) -> OptionalInput:
    try:
        return await provider.load(ticker=ticker, as_of=as_of)
    except Exception as exc:
        return OptionalInput(
            status=InputAvailability.UNAVAILABLE,
            warning=f"{label} provider failed: {_bounded(str(exc))}",
        )


def _bounded(value: str, limit: int = 1_000) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."
