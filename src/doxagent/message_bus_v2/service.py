"""Message Bus v2 application service shared by agent and human APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from doxagent.message_bus_v2.compiler import compiled_body_length_for_members
from doxagent.message_bus_v2.manifests import initial_default_profile, initial_sources
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    AcquisitionFailure,
    AlertSeverity,
    AuditRecord,
    DefaultMonitoringProfile,
    HardDeleteResult,
    IngestDecision,
    IngestResult,
    MaterializedStreamItem,
    MaterializedStreamMember,
    OperationalAlert,
    PollExecutionResult,
    PollResult,
    PollState,
    PollStatus,
    PublicationMode,
    RawMessage,
    RawMessageInput,
    RawProcessingStatus,
    SourceDefinition,
    SourceKind,
    StandardMessage,
    StreamItem,
    TickerMonitoringState,
    TickerMonitoringStatus,
    TickerSourceBinding,
    UpdateActor,
    binding_id_for,
    canonical_json,
    content_hash_for,
    identity_key_for,
    new_id,
    sha256_text,
    source_item_key_for,
    utc_now,
    validate_parameter_schema,
)


class ContentMaterializer(Protocol):
    async def materialize(self, message: RawMessageInput) -> RawMessageInput: ...


class PassthroughContentMaterializer:
    async def materialize(self, message: RawMessageInput) -> RawMessageInput:
        return message


class MessageBusV2Service:
    """One control/data-plane service used by worker, dashboard and agent tools."""

    def __init__(
        self,
        repository: MessageBusV2Repository,
        *,
        materializer: ContentMaterializer | None = None,
    ) -> None:
        self.repository = repository
        self.materializer = materializer or PassthroughContentMaterializer()

    def bootstrap(self) -> None:
        for source in initial_sources():
            if self.repository.get_source(source.source_id) is None:
                self.register_source(source)
        if self.repository.get_default_profile("default") is None:
            self.save_default_profile(initial_default_profile())

    # -- source registry -------------------------------------------------------------

    def register_source(self, source: SourceDefinition) -> SourceDefinition:
        if self.repository.get_source(source.source_id) is not None:
            raise ValueError(f"source already exists: {source.source_id}")
        self._validate_source_adapter(source)
        if source.default_parameters:
            validate_parameter_schema(source.parameter_schema, source.default_parameters)
        created = source.model_copy(
            update={"version": 1, "created_at": utc_now(), "updated_at": utc_now()}
        )
        self.repository.save_source(created)
        self._audit(
            "source",
            created.source_id,
            "register",
            created.updated_by,
            created.updated_reason,
            created.version,
            created,
        )
        return created

    def update_source(
        self,
        source_id: str,
        patch: Mapping[str, object],
        *,
        actor: UpdateActor,
        reason: str | None = None,
        binding_patches: Mapping[str, Mapping[str, object]] | None = None,
    ) -> SourceDefinition:
        current = self.require_source(source_id)
        normalized_patch = dict(patch)
        if "source_kind" in normalized_patch:
            if "kind" in normalized_patch:
                raise ValueError("provide only one of kind or source_kind")
            normalized_patch["kind"] = normalized_patch.pop("source_kind")
        unknown = set(normalized_patch) - set(SourceDefinition.model_fields)
        if unknown:
            raise ValueError(f"unsupported source field(s): {', '.join(sorted(unknown))}")
        forbidden = {"source_id", "version", "created_at"} & set(normalized_patch)
        if forbidden:
            raise ValueError(f"immutable source field(s): {', '.join(sorted(forbidden))}")
        updated = SourceDefinition.model_validate(
            {
                **current.model_dump(),
                **normalized_patch,
                "version": current.version + 1,
                "updated_at": utc_now(),
                "updated_by": actor,
                "updated_reason": reason,
            }
        )
        self._validate_source_adapter(updated)
        if updated.default_parameters:
            validate_parameter_schema(updated.parameter_schema, updated.default_parameters)
        bindings = self.repository.list_bindings(source_id=current.source_id)
        invalid: list[str] = []
        patches = binding_patches or {}
        prepared: list[TickerSourceBinding] = []
        for binding in bindings:
            binding_patch = dict(patches.get(binding.binding_id, {}))
            candidate_parameters = binding_patch.get("source_parameters", binding.source_parameters)
            if not isinstance(candidate_parameters, Mapping):
                invalid.append(binding.binding_id)
                continue
            try:
                validate_parameter_schema(updated.parameter_schema, dict(candidate_parameters))
            except ValueError:
                invalid.append(binding.binding_id)
                continue
            candidate = self._patched_binding(
                binding,
                binding_patch,
                actor=actor,
                reason=reason or f"source updated to v{updated.version}",
            )
            prepared.append(candidate.model_copy(update={"source_version": updated.version}))
        if invalid:
            raise ValueError(
                "source schema is incompatible with active binding(s); use atomic "
                f"binding_patches for: {', '.join(invalid)}"
            )
        self.repository.save_source_with_bindings(updated, prepared)
        self._audit("source", updated.source_id, "update", actor, reason, updated.version, updated)
        return updated

    def disable_source(
        self, source_id: str, *, actor: UpdateActor, reason: str | None = None
    ) -> SourceDefinition:
        return self.update_source(source_id, {"enabled": False}, actor=actor, reason=reason)

    def rollback_source(
        self, source_id: str, version: int, *, actor: UpdateActor, reason: str | None = None
    ) -> SourceDefinition:
        historical = self.repository.source_revision(source_id, version)
        if historical is None:
            raise KeyError(f"source revision not found: {source_id}@{version}")
        patch = historical.model_dump(
            exclude={
                "source_id",
                "version",
                "created_at",
                "updated_at",
                "updated_by",
                "updated_reason",
            }
        )
        return self.update_source(
            source_id, patch, actor=actor, reason=reason or f"rollback to v{version}"
        )

    def hard_delete_source(
        self, source_id: str, *, actor: UpdateActor, reason: str | None = None
    ) -> HardDeleteResult:
        source = self.require_source(source_id)
        self.retry_pending_raw(limit=10_000, source_id=source.source_id)
        flushed: list[str] = []
        for binding in self.repository.list_bindings(source_id=source.source_id):
            flushed.extend(
                item.stream_item_id for item in self.flush_binding(binding.binding_id, force=True)
            )
        count = self.repository.delete_source_control_plane(
            source.source_id,
            actor=actor,
            reason=reason,
        )
        self._audit(
            "source", source.source_id, "hard_delete", actor, reason, source.version, source
        )
        return HardDeleteResult(
            source_id=source.source_id,
            deleted_binding_count=count,
            flushed_stream_item_ids=flushed,
        )

    def require_source(self, source_id: str) -> SourceDefinition:
        source = self.repository.get_source(source_id)
        if source is None:
            raise KeyError(f"source not found: {source_id}")
        return source

    # -- profile and ticker lifecycle ------------------------------------------------

    def save_default_profile(self, profile: DefaultMonitoringProfile) -> DefaultMonitoringProfile:
        for entry in profile.entries:
            source = self.require_source(entry.source_id)
            validate_parameter_schema(source.parameter_schema, entry.source_parameters)
        current = self.repository.get_default_profile(profile.profile_id)
        version = 1 if current is None else current.version + 1
        updated = profile.model_copy(update={"version": version, "updated_at": utc_now()})
        self.repository.save_default_profile(updated)
        self._audit(
            "profile",
            updated.profile_id,
            "save",
            updated.updated_by,
            updated.updated_reason,
            updated.version,
            updated,
        )
        return updated

    def rollback_default_profile(
        self,
        profile_id: str,
        version: int,
        *,
        actor: UpdateActor,
        reason: str | None = None,
    ) -> DefaultMonitoringProfile:
        historical = self.repository.profile_revision(profile_id, version)
        if historical is None:
            raise KeyError(f"profile revision not found: {profile_id}@{version}")
        return self.save_default_profile(
            historical.model_copy(
                update={"updated_by": actor, "updated_reason": reason or f"rollback to v{version}"}
            )
        )

    def start_ticker(
        self,
        ticker: str,
        *,
        profile_id: str = "default",
        actor: UpdateActor = UpdateActor.SYSTEM,
    ) -> TickerMonitoringState:
        normalized = ticker.strip().upper()
        existing = self.repository.get_ticker_state(normalized)
        if existing is not None:
            resumed = existing.model_copy(
                update={
                    "status": TickerMonitoringStatus.RUNNING,
                    "updated_at": utc_now(),
                    "updated_by": actor,
                }
            )
            self.repository.save_ticker_state(resumed)
            return resumed
        profile = self.repository.get_default_profile(profile_id)
        if profile is None:
            raise KeyError(f"default profile not found: {profile_id}")
        for entry in profile.entries:
            source = self.require_source(entry.source_id)
            self.configure_binding(
                ticker=normalized,
                source_id=entry.source_id,
                source_parameters=entry.source_parameters,
                polling=entry.polling.model_dump(),
                streaming=entry.streaming.model_dump(),
                actor=actor,
                reason=f"materialized from {profile.profile_id}@{profile.version}",
                source_version=source.version,
            )
        state = TickerMonitoringState(
            ticker=normalized,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            updated_by=actor,
        )
        self.repository.save_ticker_state(state)
        self._audit("ticker", normalized, "start", actor, None, profile.version, state)
        return state

    def set_ticker_status(
        self,
        ticker: str,
        status: TickerMonitoringStatus,
        *,
        actor: UpdateActor,
        reason: str | None = None,
    ) -> TickerMonitoringState:
        current = self.repository.get_ticker_state(ticker)
        if current is None:
            raise KeyError(f"ticker monitoring state not found: {ticker}")
        updated = current.model_copy(
            update={"status": status, "updated_at": utc_now(), "updated_by": actor}
        )
        self.repository.save_ticker_state(updated)
        self._audit("ticker", updated.ticker, status.value, actor, reason, None, updated)
        return updated

    def configure_binding(
        self,
        *,
        ticker: str,
        source_id: str,
        source_parameters: Mapping[str, object] | None = None,
        polling: Mapping[str, object] | None = None,
        streaming: Mapping[str, object] | None = None,
        enabled: bool = True,
        actor: UpdateActor,
        reason: str | None = None,
        source_version: int | None = None,
    ) -> TickerSourceBinding:
        source = self.require_source(source_id)
        parameters = dict(
            source.default_parameters if source_parameters is None else source_parameters
        )
        validate_parameter_schema(source.parameter_schema, parameters)
        binding_id = binding_id_for(ticker, source.source_id)
        current = self.repository.get_binding(binding_id, include_tombstoned=True)
        data = {
            "binding_id": binding_id,
            "ticker": ticker,
            "source_id": source.source_id,
            "source_parameters": parameters,
            "polling": (
                source.default_polling_config.model_dump() if polling is None else dict(polling)
            ),
            "streaming": (
                source.default_streaming_config.model_dump()
                if streaming is None
                else dict(streaming)
            ),
            "enabled": enabled,
            "version": 1 if current is None else current.version + 1,
            "source_version": source_version or source.version,
            "created_at": current.created_at if current else utc_now(),
            "updated_at": utc_now(),
            "updated_by": actor,
            "updated_reason": reason,
            "tombstoned_at": None,
        }
        binding = TickerSourceBinding.model_validate(data)
        self.repository.save_binding(binding)
        self._audit(
            "binding", binding.binding_id, "configure", actor, reason, binding.version, binding
        )
        return binding

    def update_binding(
        self,
        binding_id: str,
        patch: Mapping[str, object],
        *,
        actor: UpdateActor,
        reason: str | None = None,
    ) -> TickerSourceBinding:
        current = self.repository.get_binding(binding_id)
        if current is None:
            raise KeyError(f"binding not found: {binding_id}")
        updated = self._patched_binding(current, patch, actor=actor, reason=reason)
        source = self.require_source(updated.source_id)
        validate_parameter_schema(source.parameter_schema, updated.source_parameters)
        self.repository.save_binding(updated)
        self._audit(
            "binding", updated.binding_id, "update", actor, reason, updated.version, updated
        )
        return updated

    def delete_binding(
        self, binding_id: str, *, actor: UpdateActor, reason: str | None = None
    ) -> TickerSourceBinding:
        current = self.repository.get_binding(binding_id)
        if current is None:
            raise KeyError(f"binding not found: {binding_id}")
        for _ in self.flush_binding(binding_id, force=True):
            pass
        updated = current.model_copy(
            update={
                "enabled": False,
                "tombstoned_at": utc_now(),
                "updated_at": utc_now(),
                "updated_by": actor,
                "updated_reason": reason,
                "version": current.version + 1,
            }
        )
        self.repository.save_binding(updated)
        self._audit("binding", binding_id, "tombstone", actor, reason, updated.version, updated)
        return updated

    def reset_binding_bootstrap(
        self, binding_id: str, *, actor: UpdateActor, reason: str | None = None
    ) -> PollState:
        binding = self.repository.get_binding(binding_id)
        if binding is None:
            raise KeyError(f"binding not found: {binding_id}")
        self.repository.reset_binding_baseline(binding_id)
        state = PollState(
            binding_id=binding.binding_id, source_id=binding.source_id, ticker=binding.ticker
        )
        self.repository.save_poll_state(state)
        self._audit(
            "binding", binding_id, "reset_bootstrap", actor, reason, binding.version, binding
        )
        return state

    # -- data plane ------------------------------------------------------------------

    async def accept_poll_result(
        self,
        *,
        source: SourceDefinition,
        binding: TickerSourceBinding,
        result: PollResult,
        attempted_at: datetime | None = None,
    ) -> PollExecutionResult:
        now = attempted_at or utc_now()
        state = self.repository.get_poll_state(binding)
        bootstrap = not state.bootstrap_complete
        output = PollExecutionResult(
            poll_run_id=str(result.acquisition_metadata.get("poll_run_id") or new_id("poll")),
            binding_id=binding.binding_id,
            crawler_execution_id=(
                str(result.acquisition_metadata["crawler_execution_id"])
                if result.acquisition_metadata.get("crawler_execution_id")
                else None
            ),
            collected_count=len(result.messages),
        )
        for failure in result.failures:
            self.repository.save_failure(failure)
            self._upsert_failure_alert(failure)
            output = output.model_copy(update={"invalid_count": output.invalid_count + 1})
        for input_message in result.messages:
            ingest = await self.accept_message(
                source=source,
                binding=binding,
                message=input_message,
                bootstrap=bootstrap,
                collected_at=now,
            )
            updates: dict[str, int] = {}
            if ingest.decision is IngestDecision.INSERTED:
                updates["inserted_count"] = output.inserted_count + 1
            elif ingest.decision is IngestDecision.REVISION:
                updates["revision_count"] = output.revision_count + 1
            elif ingest.decision is IngestDecision.DUPLICATE:
                updates["duplicate_count"] = output.duplicate_count + 1
            elif ingest.decision is IngestDecision.BOOTSTRAP_SUPPRESSED:
                updates["bootstrap_suppressed_count"] = output.bootstrap_suppressed_count + 1
            elif ingest.decision is IngestDecision.INVALID:
                updates["invalid_count"] = output.invalid_count + 1
            if ingest.stream_item_ids:
                updates["published_count"] = output.published_count + len(ingest.stream_item_ids)
            output = output.model_copy(update=updates)
        saved_state = state.model_copy(
            update={
                "status": (
                    PollStatus.PARTIAL if result.failures else PollStatus.SUCCEEDED
                ),
                "checkpoint": ({} if source.kind is SourceKind.CRAWLER else result.next_checkpoint),
                "bootstrap_complete": True,
                "last_attempt_at": now,
                "last_success_at": now,
                "last_failure_at": now if result.failures else None,
                "failure_since": None,
                "last_error_code": (
                    result.failures[0].error_code if result.failures else None
                ),
                "last_error_message": (
                    result.failures[0].error_message[:1000] if result.failures else None
                ),
                "consecutive_failures": 0,
                "collected_count": state.collected_count + output.collected_count,
                "published_count": state.published_count + output.published_count,
                "updated_at": now,
            }
        )
        self.repository.save_poll_state(saved_state)
        self.repository.resolve_alert(f"poll_failure:{binding.binding_id}")
        self._refresh_source_failure_alert(source.source_id, now=now)
        return output

    @staticmethod
    def _validate_source_adapter(source: SourceDefinition) -> None:
        is_crawler_ref = source.adapter_ref.startswith("crawler:")
        if source.kind is SourceKind.CRAWLER and not is_crawler_ref:
            raise ValueError("crawler sources must use adapter_ref crawler:<crawler_id>")
        if source.kind is SourceKind.API and is_crawler_ref:
            raise ValueError("API sources may not use a crawler adapter_ref")

    async def accept_message(
        self,
        *,
        source: SourceDefinition,
        binding: TickerSourceBinding,
        message: RawMessageInput,
        bootstrap: bool,
        collected_at: datetime | None = None,
    ) -> IngestResult:
        now = collected_at or utc_now()
        try:
            materialized = await self.materializer.materialize(message)
        except Exception as exc:
            failure = self._failure(
                source=source,
                binding=binding,
                code="content_materialization_failed",
                message=str(exc),
                payload=message.raw_payload,
            )
            self.repository.save_failure(failure)
            self._upsert_failure_alert(failure)
            return IngestResult(decision=IngestDecision.INVALID, error_code=failure.error_code)
        effective_source = materialized.source or source.display_name
        materialized = RawMessageInput.model_validate(
            {**materialized.model_dump(), "source": effective_source}
        )
        raw_hash = sha256_text(canonical_json(materialized.raw_payload))
        raw = RawMessage(
            raw_message_id=new_id("raw"),
            ticker=binding.ticker,
            source_id=source.source_id,
            binding_id=binding.binding_id,
            source_definition_version=source.version,
            external_id=materialized.external_id,
            source_item_key=source_item_key_for(source.source_id, materialized),
            identity_key=identity_key_for(source.source_id, materialized),
            content_hash=content_hash_for(materialized),
            raw_hash=raw_hash,
            title=materialized.title,
            body=materialized.body,
            source=effective_source,
            url=materialized.url,
            published_at=materialized.published_at,
            collected_at=now,
            raw_payload=materialized.raw_payload,
            metadata=materialized.metadata,
            streaming_config=binding.streaming,
            first_seen_at=now,
            last_seen_at=now,
            bootstrap_suppressed=bootstrap,
        )
        decision, persisted = self.repository.record_raw(raw)
        if decision is IngestDecision.DUPLICATE:
            return IngestResult(decision=decision, raw_message_id=persisted.raw_message_id)
        if bootstrap:
            self.repository.add_baseline(persisted)
            completed = persisted.model_copy(
                update={"processing_status": RawProcessingStatus.COMPLETED}
            )
            self.repository.save_raw(completed)
            return IngestResult(
                decision=IngestDecision.BOOTSTRAP_SUPPRESSED,
                raw_message_id=persisted.raw_message_id,
            )
        processing = persisted.model_copy(
            update={
                "processing_status": RawProcessingStatus.PROCESSING,
                "processing_attempts": persisted.processing_attempts + 1,
            }
        )
        self.repository.save_raw(processing)
        try:
            standard = self._standardize(processing)
            stream_item = self.repository.finalize_standard(
                raw=processing,
                message=standard,
                streaming=processing.streaming_config,
            )
            stream_ids = [stream_item.stream_item_id] if stream_item else []
            if processing.streaming_config.publication_mode is PublicationMode.BUFFERED:
                stream_ids.extend(
                    item.stream_item_id for item in self.flush_binding(binding.binding_id)
                )
            return IngestResult(
                decision=decision,
                raw_message_id=processing.raw_message_id,
                standard_message_id=standard.standard_message_id,
                stream_item_ids=stream_ids,
            )
        except Exception as exc:
            self.repository.save_raw(
                processing.model_copy(
                    update={
                        "processing_status": RawProcessingStatus.FAILED,
                        "processing_error": str(exc)[:1000],
                    }
                )
            )
            raise

    def retry_pending_raw(self, *, limit: int = 100, source_id: str | None = None) -> int:
        """Complete durable Raw rows left pending by an interrupted process."""

        count = 0
        values = [
            *self.repository.list_raw(status=RawProcessingStatus.PENDING, limit=limit),
            *self.repository.list_raw(status=RawProcessingStatus.PROCESSING, limit=limit),
        ]
        for raw in values[:limit]:
            if source_id is not None and raw.source_id != source_id:
                continue
            if raw.bootstrap_suppressed:
                self.repository.add_baseline(raw)
                self.repository.save_raw(
                    raw.model_copy(update={"processing_status": RawProcessingStatus.COMPLETED})
                )
                count += 1
                continue
            standard = self.repository.get_standard_for_raw(raw.raw_message_id)
            if standard is None:
                standard = self._standardize(raw)
            self.repository.finalize_standard(
                raw=raw,
                message=standard,
                streaming=raw.streaming_config,
            )
            count += 1
        return count

    def flush_due_buffers(self, *, now: datetime | None = None) -> list[str]:
        current = now or utc_now()
        published: list[str] = []
        for binding_id in self.repository.list_buffer_binding_ids():
            binding = self.repository.get_binding(binding_id, include_tombstoned=True)
            if binding is None:
                continue
            buffered = self.repository.list_buffer(binding_id)
            if not buffered:
                continue
            oldest = min(added for _, added in buffered)
            due = (
                current - oldest.astimezone(UTC)
            ).total_seconds() >= binding.streaming.buffer.max_wait_seconds
            if due:
                published.extend(
                    item.stream_item_id for item in self.flush_binding(binding_id, force=True)
                )
        return published

    def flush_binding(self, binding_id: str, *, force: bool = False) -> list[StreamItem]:
        binding = self.repository.get_binding(binding_id, include_tombstoned=True)
        if binding is None:
            return []
        entries = self.repository.list_buffer(binding_id)
        if not entries:
            return []
        if not force and len(entries) < binding.streaming.buffer.max_items:
            return []
        messages = [message for message, _ in entries]
        batches = self._pack_buffer(binding, messages)
        published = [self.repository.publish_buffered(binding.ticker, batch) for batch in batches]
        return published

    def pending_stream(
        self, consumer_id: str, ticker: str, *, limit: int = 100
    ) -> list[MaterializedStreamItem]:
        cursor = self.repository.get_consumer_offset(consumer_id, ticker)
        return self.repository.read_stream(ticker, after_offset=cursor.stream_offset, limit=limit)

    def commit_stream(self, consumer_id: str, item: MaterializedStreamItem) -> None:
        self.repository.commit_consumer_offset(
            consumer_id, item.item.ticker, item.item.stream_offset
        )

    def seek_consumer_to_tail(self, consumer_id: str, ticker: str) -> int:
        offset = self.repository.latest_stream_offset(ticker)
        self.repository.commit_consumer_offset(consumer_id, ticker, offset)
        return offset

    def initialize_runtime_cursor(self, consumer_id: str, ticker: str) -> int:
        state = self.repository.get_ticker_state(ticker)
        if state is None:
            raise KeyError(f"ticker monitoring state not found: {ticker}")
        if state.runtime_cursor_initialized:
            return self.repository.get_consumer_offset(consumer_id, ticker).stream_offset
        offset = self.seek_consumer_to_tail(consumer_id, ticker)
        self.repository.save_ticker_state(
            state.model_copy(update={"runtime_cursor_initialized": True, "updated_at": utc_now()})
        )
        return offset

    # -- helpers ---------------------------------------------------------------------

    def _patched_binding(
        self,
        binding: TickerSourceBinding,
        patch: Mapping[str, object],
        *,
        actor: UpdateActor,
        reason: str | None,
    ) -> TickerSourceBinding:
        forbidden = {"binding_id", "ticker", "source_id", "created_at", "version"} & set(patch)
        if forbidden:
            raise ValueError(f"immutable binding field(s): {', '.join(sorted(forbidden))}")
        value = binding.model_dump()
        value.update(dict(patch))
        value.update(
            {
                "version": binding.version + 1,
                "updated_at": utc_now(),
                "updated_by": actor,
                "updated_reason": reason,
            }
        )
        return TickerSourceBinding.model_validate(value)

    @staticmethod
    def _standardize(raw: RawMessage) -> StandardMessage:
        return StandardMessage(
            standard_message_id=new_id("std"),
            raw_message_id=raw.raw_message_id,
            ticker=raw.ticker,
            source_id=raw.source_id,
            binding_id=raw.binding_id,
            source_definition_version=raw.source_definition_version,
            external_id=raw.external_id,
            source_item_key=raw.source_item_key,
            revision=raw.revision,
            title=raw.title,
            body=raw.body,
            source=raw.source,
            url=raw.url,
            published_at=raw.published_at,
            collected_at=raw.collected_at,
            metadata=raw.metadata,
        )

    def _pack_buffer(
        self, binding: TickerSourceBinding, messages: Sequence[StandardMessage]
    ) -> list[list[StandardMessage]]:
        ordered = sorted(enumerate(messages), key=lambda pair: (pair[1].published_at, pair[0]))
        batches: list[list[StandardMessage]] = []
        current: list[StandardMessage] = []
        limit = binding.streaming.buffer.max_compiled_body_chars
        for _, message in ordered:
            candidate = [*current, message]
            members = [self._member_for_size(value, index) for index, value in enumerate(candidate)]
            if current and compiled_body_length_for_members(members) > limit:
                batches.append(current)
                current = [message]
            else:
                current = candidate
            if len(current) >= binding.streaming.buffer.max_items:
                batches.append(current)
                current = []
        if current:
            batches.append(current)
        for batch in batches:
            members = [self._member_for_size(value, index) for index, value in enumerate(batch)]
            if len(batch) == 1 and compiled_body_length_for_members(members) > limit:
                self.repository.upsert_alert(
                    OperationalAlert(
                        alert_key=f"oversized_message:{batch[0].standard_message_id}",
                        severity=AlertSeverity.WARNING,
                        code="compiled_message_oversized",
                        message=(
                            "single message exceeds max_compiled_body_chars and was "
                            "published intact"
                        ),
                        source_id=batch[0].source_id,
                        binding_id=batch[0].binding_id,
                        metadata={
                            "compiled_chars": compiled_body_length_for_members(members),
                            "limit": limit,
                        },
                    )
                )
        return batches

    @staticmethod
    def _member_for_size(message: StandardMessage, index: int) -> MaterializedStreamMember:
        return MaterializedStreamMember(
            stream_item_id="size_estimate",
            member_index=index,
            standard_message_id=message.standard_message_id,
            source_id=message.source_id,
            binding_id=message.binding_id,
            title=message.title,
            body=message.body,
            source=message.source,
            url=message.url,
            published_at=message.published_at,
        )

    @staticmethod
    def _failure(
        *,
        source: SourceDefinition,
        binding: TickerSourceBinding,
        code: str,
        message: str,
        payload: dict[str, object],
    ) -> AcquisitionFailure:
        return AcquisitionFailure(
            source_id=source.source_id,
            binding_id=binding.binding_id,
            ticker=binding.ticker,
            error_code=code,
            error_message=message[:1000],
            raw_hash=sha256_text(canonical_json(payload)),
            original_payload=payload,
        )

    def _upsert_failure_alert(self, failure: AcquisitionFailure) -> None:
        self.repository.upsert_alert(
            OperationalAlert(
                alert_key=f"acquisition:{failure.binding_id}:{failure.error_code}",
                severity=AlertSeverity.WARNING,
                code=failure.error_code,
                message=failure.error_message,
                source_id=failure.source_id,
                binding_id=failure.binding_id,
                metadata={"failure_id": failure.failure_id},
            )
        )

    def record_poll_failure(
        self,
        binding: TickerSourceBinding,
        *,
        code: str,
        message: str,
        attempted_at: datetime | None = None,
    ) -> PollState:
        now = attempted_at or utc_now()
        state = self.repository.get_poll_state(binding)
        updated = state.model_copy(
            update={
                "status": PollStatus.FAILED,
                "last_attempt_at": now,
                "last_failure_at": now,
                "failure_since": state.failure_since or now,
                "last_error_code": code,
                "last_error_message": message[:1000],
                "consecutive_failures": state.consecutive_failures + 1,
                "updated_at": now,
            }
        )
        self.repository.save_poll_state(updated)
        if (
            updated.failure_since is not None
            and (now - updated.failure_since).total_seconds() >= binding.polling.alert_after_seconds
        ):
            self.repository.upsert_alert(
                OperationalAlert(
                    alert_key=f"poll_failure:{binding.binding_id}",
                    severity=AlertSeverity.ERROR,
                    code="source_poll_failure",
                    message=message[:1000],
                    source_id=binding.source_id,
                    binding_id=binding.binding_id,
                    metadata={
                        "error_code": code,
                        "consecutive_failures": updated.consecutive_failures,
                    },
                )
            )
        self._refresh_source_failure_alert(
            binding.source_id,
            now=now,
            latest_code=code,
            latest_message=message,
        )
        return updated

    def _refresh_source_failure_alert(
        self,
        source_id: str,
        *,
        now: datetime,
        latest_code: str | None = None,
        latest_message: str | None = None,
    ) -> None:
        source = self.repository.get_source(source_id)
        if source is None:
            return
        group_sources = {
            value.source_id
            for value in self.repository.list_sources()
            if value.scheduler_group == source.scheduler_group
        }
        recent_cutoff = now - timedelta(minutes=5)
        failures = [
            state
            for state in self.repository.list_poll_states()
            if state.source_id in group_sources
            and state.status is PollStatus.FAILED
            and state.last_failure_at is not None
            and state.last_failure_at >= recent_cutoff
        ]
        key = f"source_poll_failure:{source.scheduler_group}"
        if len(failures) < 3:
            self.repository.resolve_alert(key)
            return
        self.repository.upsert_alert(
            OperationalAlert(
                alert_key=key,
                severity=AlertSeverity.ERROR,
                code="source_poll_failure_aggregate",
                message=(latest_message or f"multiple bindings failed in {source.scheduler_group}")[
                    :1000
                ],
                source_id=source.source_id,
                scheduler_group=source.scheduler_group,
                metadata={
                    "latest_error_code": latest_code,
                    "affected_bindings": sorted(state.binding_id for state in failures),
                    "affected_tickers": sorted({state.ticker for state in failures}),
                },
            )
        )

    def _audit(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: UpdateActor,
        reason: str | None,
        version: int | None,
        value: object,
    ) -> None:
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else {}
        self.repository.save_audit(
            AuditRecord(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor=actor,
                reason=reason,
                version=version,
                payload=payload,
            )
        )


__all__ = [
    "ContentMaterializer",
    "MessageBusV2Service",
    "PassthroughContentMaterializer",
]
