"""Durable internal-node execution shared by existing workflow implementations."""

from __future__ import annotations

import functools
import hashlib
import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ParamSpec, TypeVar, cast

from pydantic import BaseModel, TypeAdapter

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


def checkpointed_json(key: str, call: Callable[[], Any]) -> Any:
    """Synchronous JSON unit, also usable in asyncio.to_thread's copied context."""
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
            if record.ordinal >= 2:
                raise


def attempt_identity(fallback: str) -> str:
    step = _step.get()
    return f"init-{step.node.execution_id}" if step else fallback


def _codec(kind: str, arguments: dict[str, Any]) -> Any:
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
            job=WorkerJob.model_validate(payload["job"]),
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
            if kind == "o2_repair":
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
                    payload = previous.result.artifacts["return"]
                    result = codec.validate_python(payload)
                    return cast(T, result)
                if previous.status == "RUNNING" and "validated_return" in previous.receipt:
                    return cast(T, codec.validate_python(previous.receipt["validated_return"]))
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
                        repo.complete(lease, key, NodeResult(artifacts={"return": payload}))
                    return result
                except Exception as exc:
                    from .schema import LeaseLost

                    if isinstance(exc, LeaseLost):
                        raise
                    repo.fail(lease, key, f"{type(exc).__name__}: {exc}"[:4000])
                    if record.ordinal >= 2:
                        raise
                finally:
                    _step.reset(token)

        return wrapped

    return decorate


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
        return await self.worker.run(request)

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return await self.worker.cancel(job_id)
