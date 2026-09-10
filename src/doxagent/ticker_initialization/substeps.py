"""Durable internal-node execution shared by existing workflow implementations."""

from __future__ import annotations

import functools
import hashlib
import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ParamSpec, TypeVar, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest

from .schema import NodeResult, NodeSpec
from .service import NodeContext

_parent: ContextVar[NodeContext | None] = ContextVar("initialization_parent", default=None)
_step: ContextVar[NodeContext | None] = ContextVar("initialization_step", default=None)
P = ParamSpec("P")
T = TypeVar("T")


@contextmanager
def execution_scope(context: NodeContext) -> Iterator[None]:
    token = _parent.set(context)
    try:
        yield
    finally:
        _parent.reset(token)


def managed() -> bool:
    return _parent.get() is not None


def capture_worker(request: WorkerRunRequest, job: WorkerJob) -> None:
    context = _step.get() or _parent.get()
    if context is None:
        return
    context.checkpoint(
        worker_invocations={
            job.job_id: {
                "job": job.model_dump(mode="json"),
                "ticker": request.ticker,
                "node": request.node.value,
                "model": request.model,
                "provider": request.model_provider,
                "run_id": request.run_id,
                "initialization_id": context.run.initialization_id,
                "ordinal": context.node.ordinal,
            }
        }
    )


def capture_gateway(request, response):
    context = _step.get() or _parent.get()
    if context is None or not request.metadata.get("invocation_id"):
        return
    from datetime import UTC, datetime

    from .usage_capture import collector

    usage = response.usage or response.audit.usage
    provider = response.audit.provider.value
    collector(context)(
        {
            "invocation_id": request.metadata["invocation_id"],
            "scope": "API",
            "provider": "bailian" if provider == "dashscope" else provider,
            "node": request.metadata.get("workflow_node") or context.node.key,
            "model": response.audit.model or request.model,
            "started_at": request.metadata["invocation_started_at"],
            "finished_at": request.metadata.get("invocation_finished_at"),
            "recorded_at": datetime.now(UTC).isoformat(),
            "usage": usage.model_dump(mode="json") if usage else {},
            "status": "FAILED" if response.error else "SUCCEEDED",
        }
    )


def checkpointed_json(key: str, call: Callable[[], Any], *, max_retries: int = 1) -> Any:
    """Synchronous JSON unit with a bounded durable retry budget.

    The default preserves the existing two-attempt durable-node behavior. Callers
    that implement a narrower request-level retry policy can pass ``max_retries=0``
    so one failed request does not consume a second durable attempt.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    parent = _parent.get()
    if parent is None:
        return call()
    key = parent.node.key + "." + key
    repo, lease = parent.repository, parent.lease
    repo.expand(
        lease,
        [
            NodeSpec(
                key=key,
                block=parent.node.block,
                inputs={"managed_by": parent.node.key, "kind": "json"},
            )
        ],
    )
    while True:
        previous = next(n for n in repo.nodes(parent.run.initialization_id) if n.key == key)
        if previous.status == "SUCCEEDED" and previous.result is not None:
            return previous.result.artifacts["return"]
        if previous.status == "RUNNING":
            repo.fail(lease, key, "interrupted JSON operation")
        record = repo.begin(lease, key, parent.node.execution_version)
        try:
            result = call()
            repo.complete(lease, key, NodeResult(artifacts={"return": result}))
            return result
        except Exception as exc:
            from .schema import LeaseLost

            if isinstance(exc, LeaseLost):
                raise
            repo.fail(lease, key, type(exc).__name__)
            if record.ordinal >= max_retries + 1:
                raise


def attempt_identity(fallback: str) -> str:
    step = _step.get()
    return f"init-{step.node.execution_id}" if step else fallback


def _codec(kind: str, arguments: dict[str, Any]) -> Any:
    if kind in {"d1_assemble", "d1_publish"}:
        from datetime import datetime

        from doxagent.codex_runtime.schema import ArtifactRef

        return TypeAdapter(
            ArtifactRef if kind == "d1_assemble" else tuple[datetime, list[ArtifactRef]]
        )
    if kind == "d1":
        from doxagent.codex_runtime.schema import ArtifactRef
        from doxagent.workflows.codex_document1.schema import NodeOutput

        return TypeAdapter(tuple[NodeOutput, ArtifactRef])
    if kind == "d2":
        return _D2Codec(arguments["output_model"])
    if kind in {"o2", "o2_repair"}:
        from doxagent.workflows.codex_event_library.schema import O2RunResult

        return TypeAdapter(tuple[O2RunResult, str | None])
    output_model = arguments["output_model"]
    return TypeAdapter(tuple[output_model, str | None])  # type: ignore[valid-type]


class _D2Codec:
    def __init__(self, output_model: type[BaseModel]) -> None:
        self.output_model = output_model

    def dump_python(self, result: Any, **kwargs: Any) -> dict[str, Any]:
        from dataclasses import fields

        return {
            f.name: (value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
            for f in fields(result)
            for value in [getattr(result, f.name)]
        }

    def validate_python(self, payload: dict[str, Any]) -> Any:
        from doxagent.codex_runtime.schema import ArtifactRef, CitationManifest, NodeAttempt
        from doxagent.codex_worker.schema import WorkerJob
        from doxagent.workflows.codex_document2.runner import Document2TurnResult

        return Document2TurnResult(
            output=self.output_model.model_validate(payload["output"]),
            artifact=ArtifactRef.model_validate(payload["artifact"]),
            attempt=NodeAttempt.model_validate(payload["attempt"]),
            job=WorkerJob.model_validate(payload["job"]) if payload.get("job") else None,
            thread_id=payload["thread_id"],
            citation_manifest=CitationManifest.model_validate(payload["citation_manifest"]),
            workspace_run_id=payload["workspace_run_id"],
        )


def durable(kind: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def decorate(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        signature = inspect.signature(function)

        @functools.wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            parent = _parent.get()
            if parent is None:
                return await function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = bound.arguments
            if kind in {"d1_assemble", "d1_publish"}:
                logical = kind.removeprefix("d1_")
            elif kind == "o2_repair":
                logical = arguments["attempt_id"]
            elif kind == "o2":
                from doxagent.workflows.codex_event_library.remote_runner import _base_attempt_id

                logical = _base_attempt_id(arguments["phase"]["attempt_id"])
            else:
                logical = arguments["node"].value
            if kind == "d2":
                logical += (
                    ":"
                    + arguments["workspace_run_id"]
                    + ":"
                    + str(arguments.get("artifact_key") or "default")
                )
            key = parent.node.key + "." + logical
            repo, lease = parent.repository, parent.lease
            repo.expand(
                lease,
                [
                    NodeSpec(
                        key=key,
                        block=parent.node.block,
                        inputs={"managed_by": parent.node.key, "kind": kind},
                    )
                ],
            )
            codec = _codec(kind, arguments)
            while True:
                previous = next(n for n in repo.nodes(parent.run.initialization_id) if n.key == key)
                if previous.status == "SUCCEEDED" and previous.result is not None:
                    payload = previous.receipt.get(
                        "decoded_return_v1", previous.result.artifacts["return"]
                    )
                    try:
                        result = _decode_receipt(codec, kind, payload)
                    except (ValueError, TypeError, KeyError) as exc:
                        restored = await _recover_artifact_receipt(kind, arguments, payload, codec)
                        if restored is not None:
                            repo.recovered_receipt(
                                lease, key, codec.dump_python(restored, mode="json")
                            )
                            return cast(T, restored)
                        repo.reject_receipt(lease, key, f"RECEIPT_UNREADABLE: {type(exc).__name__}")
                    else:
                        repo.recovered_receipt(lease, key, codec.dump_python(result, mode="json"))
                        return cast(T, result)
                if previous.status == "RUNNING" and "validated_return" in previous.receipt:
                    try:
                        return cast(
                            T, _decode_receipt(codec, kind, previous.receipt["validated_return"])
                        )
                    except (ValueError, TypeError, KeyError) as exc:
                        repo.reject_receipt(lease, key, f"RECEIPT_UNREADABLE: {type(exc).__name__}")
                record = repo.begin(lease, key, parent.node.execution_version)
                child = NodeContext(repo, lease, record)
                token = _step.set(child)
                try:
                    from .invocation import freeze_invocation

                    await freeze_invocation(child, kind, arguments)
                    if "max_attempts" in arguments:
                        bound.arguments["max_attempts"] = 1
                    result = await function(*bound.args, **bound.kwargs)
                    payload = codec.dump_python(result, mode="json", serialize_as_any=True)
                    if kind == "d3":
                        # The small response is not proof of usable workspace artifacts.
                        # The orchestrator confirms this receipt after its stage checks.
                        child.checkpoint(validated_return=payload)
                    else:
                        repo.complete(
                            lease,
                            key,
                            NodeResult(artifacts={"return": payload, "codec_version": 1}),
                        )
                    return result
                except Exception as exc:
                    from .schema import LeaseLost

                    if isinstance(exc, LeaseLost):
                        raise
                    from doxagent.codex_runtime.recovery import failure_details

                    details = failure_details(exc)
                    child.checkpoint(failure=details)
                    repo.fail(lease, key, f"{type(exc).__name__}: {exc}"[:4000])
                    if details.get("manual_resume_required"):
                        from doxagent.codex_runtime.errors import InfrastructureRecoveryExhausted

                        raise InfrastructureRecoveryExhausted(str(exc)) from exc
                    if record.ordinal >= 2:
                        raise
                    if details["retryable"]:
                        import asyncio

                        await asyncio.sleep(1)
                finally:
                    _step.reset(token)

        return wrapped

    return decorate


async def _recover_artifact_receipt(kind, arguments, payload, codec):
    """Read only the exact artifact named by the old receipt, with checksum proof."""
    import copy

    from doxagent.codex_runtime.recovery import ingest_model, json_value
    from doxagent.codex_runtime.schema import ArtifactRef

    owner = arguments["self"]
    workspace = getattr(owner, "_workspace", None)
    if workspace is None or kind not in {"d1", "d2"}:
        return None
    try:
        raw_ref = payload[1] if kind == "d1" else payload["artifact"]
        ref = ArtifactRef.model_validate(raw_ref)
        expected_run = (
            arguments["request"].run_id if kind == "d1" else arguments["persistence_run_id"]
        )
        if ref.run_id != expected_run:
            return None
        file = await workspace.read_text(ref.run_id, ref.relative_path)
        if file.content is None or hashlib.sha256(file.content.encode()).hexdigest() != ref.sha256:
            return None
        value = copy.deepcopy(payload)
        if kind == "d1":
            from doxagent.workflows.codex_document1.schema import NodeOutput

            if not file.content.strip():
                return None
            value[0] = NodeOutput(
                status="PARTIAL",
                report_markdown=file.content,
                warnings=["RECEIPT_RECOVERED_FROM_HASHED_REPORT"],
            ).model_dump(mode="json")
        else:
            value["output"] = ingest_model(
                arguments["output_model"], json_value(file.content)
            ).model_dump(mode="json")
        return _decode_receipt(codec, kind, value)
    except (ValueError, TypeError, KeyError, FileNotFoundError):
        return None


def _decode_receipt(codec: Any, kind: str, payload: Any) -> Any:
    import copy

    from doxagent.codex_runtime.recovery import bounded_text

    value = copy.deepcopy(payload)

    # Historical models wrote internal aliases and unbounded diagnostic strings.
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                k: bounded_text(v, 3000) if k == "error_message" and v is not None else clean(v)
                for k, v in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        return item

    value = clean(value)
    if kind == "d1":
        import json

        from doxagent.workflows.codex_document1.recovery import recover_output

        output, _ = recover_output(json.dumps(value[0], ensure_ascii=False))
        if not (output.report_markdown.strip() or output.entity_relations or output.future_nodes):
            # Empty optional C4 is valid; the original strict decoder decides.
            return codec.validate_python(value)
        value[0] = output.model_dump(mode="json")
    for _ in range(3):
        try:
            return codec.validate_python(value)
        except ValidationError as exc:
            errors = exc.errors()
            if any(e["type"] != "extra_forbidden" for e in errors):
                raise
            for error in errors:
                target = value
                for part in error["loc"][:-1]:
                    target = target[part]
                target.pop(error["loc"][-1], None)
    return codec.validate_python(value)


def settle_stage(node: str, *, error: str | None = None) -> None:
    """Bridge a validated/recovered D3 stage to its durable execution ledger."""
    parent = _parent.get()
    if parent is None:
        return
    key = parent.node.key + "." + node
    child = next(
        (n for n in parent.repository.nodes(parent.run.initialization_id) if n.key == key), None
    )
    if child is None or child.status != "RUNNING":
        return
    if error is not None:
        parent.repository.fail(parent.lease, key, error)
    else:
        payload = child.receipt.get("validated_return")
        artifacts = {"return": payload} if payload is not None else {"workspace_recovered": True}
        parent.repository.complete(parent.lease, key, NodeResult(artifacts=artifacts))


class DurableWorker:
    """Freeze HTTP dispatch before sending; reconciling a child reattaches the same job."""

    def __init__(self, worker: CodexWorkerClient) -> None:
        self.worker = worker

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        step = _step.get()
        if step is None:
            return await self.worker.run(request)
        frozen = step.node.receipt.get("worker_request")
        if frozen is None:
            request = request.model_copy(update={"idempotency_key": step.node.execution_id})
            step.checkpoint(
                worker_request=request.model_dump(mode="json"),
                dispatch_provenance={
                    "request_sha256": hashlib.sha256(
                        request.model_dump_json().encode()
                    ).hexdigest(),
                    "prompt_sha256": hashlib.sha256(request.prompt.encode()).hexdigest(),
                    "model": request.model,
                    "model_provider": request.model_provider,
                    "effort": request.effort,
                    "workflow_version": request.workflow_version,
                },
            )
        else:
            request = WorkerRunRequest.model_validate(frozen)
        job = await self.worker.run(request)
        # Actual worker identity survives cached returns and lease recovery; no prompt is copied.
        observations = dict(step.node.receipt.get("worker_invocations", {}))
        observations[job.job_id] = {
            "job": job.model_dump(mode="json"),
            "ticker": request.ticker,
            "node": request.node.value,
            "model": request.model,
            "provider": request.model_provider,
            "run_id": request.run_id,
            "initialization_id": step.run.initialization_id,
            "ordinal": step.node.ordinal,
        }
        step.checkpoint(worker_invocations=observations)
        return job

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return await self.worker.cancel(job_id)
