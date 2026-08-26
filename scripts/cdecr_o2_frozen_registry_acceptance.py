"""Run a fresh O2 initialization from an immutable FINALIZED CDECR registry."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from cdecr.registry import SQLiteCDECRRegistry
from doxagent.cdecr_integration.contracts import RuntimeRegistryBinding
from doxagent.cdecr_integration.workflow_runner import CDECRWorkflowRunner
from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.event_library.quality import compile_quality_report
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_event_library.remote_runner import (
    RemoteEventLibraryInitializer,
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_only_registry_facts(path: Path) -> dict[str, Any]:
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        epochs = connection.execute(
            """
            SELECT epoch_id, status, current_stage, finalized_at
            FROM bulk_epochs WHERE status='FINALIZED' ORDER BY finalized_at DESC
            """
        ).fetchall()
        atomic_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM atomic_event_heads heads
                LEFT JOIN atomic_event_redirects redirects
                  ON redirects.source_event_id=heads.event_id
                WHERE redirects.source_event_id IS NULL
                """
            ).fetchone()[0]
        )
        package_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM event_package_heads heads
                LEFT JOIN package_redirects redirects
                  ON redirects.source_package_id=heads.package_id
                WHERE redirects.source_package_id IS NULL
                """
            ).fetchone()[0]
        )
        source_count = int(
            connection.execute("SELECT COUNT(*) FROM source_messages").fetchone()[0]
        )
    if quick_check != "ok":
        raise RuntimeError(f"source registry quick_check failed: {quick_check}")
    if len(epochs) != 1:
        raise RuntimeError(f"expected exactly one FINALIZED epoch, found {len(epochs)}")
    epoch_id, status, stage, finalized_at = epochs[0]
    return {
        "epoch_id": str(epoch_id),
        "epoch_status": str(status),
        "epoch_stage": str(stage),
        "epoch_finalized_at": str(finalized_at),
        "source_count": source_count,
        "atomic_count": atomic_count,
        "package_count": package_count,
        "quick_check": quick_check,
    }


def _prompt_manifest(prompt_root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(prompt_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(prompt_root.rglob("*.md"))
    ]


class _NeverRun:
    def process_batch(self, _message_ids: list[str]) -> list[object]:
        raise RuntimeError("frozen-registry acceptance must not run CDECR processors")

    @property
    def last_epoch_id(self) -> str | None:
        return None


class _ProgressWorker:
    def __init__(self, delegate: HttpCodexWorkerClient) -> None:
        self.delegate = delegate
        self.jobs: list[dict[str, object]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        print(
            json.dumps(
                {"event": "MODEL_PHASE_STARTED", "attempt_id": request.attempt_id},
                ensure_ascii=False,
            ),
            flush=True,
        )
        job = await self.delegate.run(request)
        usage = None if job.telemetry is None else job.telemetry.usage.model_dump(mode="json")
        record: dict[str, object] = {
            "attempt_id": request.attempt_id,
            "job_id": job.job_id,
            "status": job.status,
            "thread_id": job.thread_id,
            "turn_id": job.turn_id,
            "usage": usage,
            "error_code": job.error_code,
            "error_message": job.error_message,
        }
        self.jobs.append(record)
        print(json.dumps({"event": "MODEL_PHASE_FINISHED", **record}), flush=True)
        return job


async def _worker_preflight(settings: DoxAgentSettings) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {settings.codex_worker_bearer_token}"}
    async with httpx.AsyncClient(
        base_url=settings.codex_worker_base_url,
        headers=headers,
        timeout=30,
        trust_env=False,
    ) as client:
        response = await client.get("/v1/capabilities")
        response.raise_for_status()
        payload = response.json()
    return {
        "status_code": response.status_code,
        "sdk": payload.get("sdk"),
        "workspace_operations": payload.get("workspace_operations"),
    }


async def _run(args: argparse.Namespace) -> int:
    source_registry = args.source_registry.resolve()
    output_root = args.output_root.resolve()
    if not source_registry.is_file():
        raise FileNotFoundError(source_registry)
    if output_root.exists() and any(output_root.iterdir()) and not args.resume_existing:
        raise FileExistsError(f"acceptance output root is not empty: {output_root}")
    if args.resume_existing and not output_root.is_dir():
        raise FileNotFoundError(f"acceptance output root is unavailable: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    source_hash_before = _sha256(source_registry)
    source_facts = _read_only_registry_facts(source_registry)
    if source_facts["atomic_count"] != args.expected_atomics:
        raise RuntimeError(f"unexpected Atomic count: {source_facts['atomic_count']}")
    if source_facts["package_count"] != args.expected_packages:
        raise RuntimeError(f"unexpected Package count: {source_facts['package_count']}")

    input_root = output_root / "inputs"
    input_root.mkdir(parents=True, exist_ok=True)
    registry_copy = input_root / "r2-registry-copy.sqlite3"
    if args.resume_existing:
        if not registry_copy.is_file():
            raise FileNotFoundError(registry_copy)
        if _sha256(registry_copy) != source_hash_before:
            raise RuntimeError("saved R2 registry copy no longer matches its immutable source")
    else:
        shutil.copy2(source_registry, registry_copy)
    registry = SQLiteCDECRRegistry(registry_copy)
    binding = RuntimeRegistryBinding(
        market=args.market,
        ticker=args.ticker,
        runtime_scope=f"cdecr:{args.market.upper()}:{args.ticker.upper()}",
        registry_path=str(registry_copy),
    )
    never = _NeverRun()
    freezer = CDECRWorkflowRunner(
        binding=binding,
        registry=registry,
        document_processor=never,
        bulk_epoch_engine=never,
    )
    snapshot = freezer.freeze_finalized_snapshot(
        epoch_id=str(source_facts["epoch_id"]), as_of=args.as_of
    )
    if len(snapshot.atomics) != args.expected_atomics:
        raise RuntimeError(f"frozen Atomic count changed: {len(snapshot.atomics)}")
    if len(snapshot.packages) != args.expected_packages:
        raise RuntimeError(f"frozen Package count changed: {len(snapshot.packages)}")
    snapshot_path = input_root / "frozen_runtime_snapshot.json"
    snapshot_json = snapshot.model_dump_json(indent=2)
    if args.resume_existing:
        saved_snapshot = (
            snapshot_path.read_text(encoding="utf-8") if snapshot_path.is_file() else None
        )
        if saved_snapshot != snapshot_json:
            raise RuntimeError("saved FrozenRuntimeSnapshot does not match the resumed input")
    else:
        snapshot_path.write_text(snapshot_json, encoding="utf-8")

    prompt_root = args.prompt_root.resolve()
    prompts = _prompt_manifest(prompt_root)
    prompt_manifest_path = input_root / "prompt_manifest.json"
    if args.resume_existing:
        saved_prompts = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
        if saved_prompts != prompts:
            raise RuntimeError("current O2 prompt/skill files differ from the original run")
    else:
        prompt_manifest_path.write_text(
            json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    repository = EventLibraryRepository(output_root / "event-library.sqlite3")
    service = EventLibraryService(repository)
    published_version_before = repository.published_version(args.ticker)
    if not args.resume_existing and published_version_before != 0:
        raise RuntimeError("fresh Event Library must start at base version 0")
    batch = service.delta_compiler.compile(snapshot)
    if batch.base_library_version != 0:
        raise RuntimeError(f"Delta base version is not zero: {batch.base_library_version}")
    if len(batch.items) != args.expected_atomics:
        raise RuntimeError(f"unexpected Delta count: {len(batch.items)}")
    if len(batch.runtime_packages) != args.expected_packages:
        raise RuntimeError(f"unexpected Runtime Package count: {len(batch.runtime_packages)}")
    print(
        json.dumps(
            {
                "event": "FROZEN_INPUT_READY",
                "snapshot_id": snapshot.snapshot_id,
                "delta_batch_id": batch.batch_id,
                "delta_count": len(batch.items),
                "runtime_package_count": len(batch.runtime_packages),
                "base_library_version": batch.base_library_version,
            }
        ),
        flush=True,
    )
    if args.prepare_only:
        return 0

    settings = DoxAgentSettings()
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise RuntimeError("Codex Worker credentials are not configured")
    preflight = await _worker_preflight(settings)
    print(json.dumps({"event": "WORKER_PREFLIGHT", **preflight}), flush=True)
    raw_worker = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    worker = _ProgressWorker(raw_worker)
    initializer = RemoteEventLibraryInitializer(
        worker=worker,
        workspace=worker,
        service=service,
        local_workspace_root=output_root / "o2-local",
        prompt_root=prompt_root,
        model=args.model or settings.codex_model,
        model_provider=settings.codex_model_provider,
        effort=args.effort or settings.codex_reasoning_effort,
        timeout_seconds=args.timeout_seconds or settings.codex_node_timeout_seconds,
        wave_size=args.wave_size,
        wave_token_budget=args.wave_token_budget,
    )
    try:
        final_batch, publication, validation, exports = await initializer.run(
            snapshot=snapshot,
            run_id=args.run_id,
            cutoff_at=args.as_of,
            export_dir=output_root / "published",
            mode="INITIALIZE",
        )
    finally:
        await raw_worker.aclose()
    if publication is None or validation is None:
        raise RuntimeError("O2 acceptance finished without publication")
    quality = compile_quality_report(repository, ticker=args.ticker)
    source_hash_after = _sha256(source_registry)
    if source_hash_after != source_hash_before:
        raise RuntimeError("source R2 registry changed during acceptance")
    events = repository.published_events(args.ticker)
    report = {
        "status": "PUBLISHED",
        "run_id": args.run_id,
        "resume_existing": args.resume_existing,
        "published_version_before": published_version_before,
        "source_registry": str(source_registry),
        "source_registry_sha256_before": source_hash_before,
        "source_registry_sha256_after": source_hash_after,
        "source_registry_unchanged": True,
        "source_facts": source_facts,
        "prompt_manifest": prompts,
        "prompt_manifest_sha256": _canonical_sha256(prompts),
        "snapshot": {
            "path": str(snapshot_path),
            "sha256": _sha256(snapshot_path),
            "snapshot_id": snapshot.snapshot_id,
            "atomic_count": len(snapshot.atomics),
            "package_count": len(snapshot.packages),
        },
        "delta": {
            "batch_id": final_batch.batch_id,
            "base_library_version": final_batch.base_library_version,
            "item_count": len(final_batch.items),
            "runtime_package_count": len(final_batch.runtime_packages),
        },
        "publication": publication.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json", exclude={"normalized_bundle"}),
        "published_event_count": len(events),
        "published_fact_count": sum(len(item.facts) for item in events),
        "quality": quality.model_dump(mode="json"),
        "model_jobs": worker.jobs,
        "exports": {name: str(path) for name, path in exports.items()},
    }
    report_path = output_root / (
        "idempotency_report.json" if args.resume_existing else "acceptance_report.json"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "event": "ACCEPTANCE_PUBLISHED",
                "report": str(report_path),
                "version": publication.published_library_version,
                "event_count": len(events),
                "fact_count": sum(len(item.facts) for item in events),
                "validation": validation.status.value,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--as-of", type=_timestamp, required=True)
    parser.add_argument("--market", default="US")
    parser.add_argument("--ticker", default="MU")
    parser.add_argument(
        "--prompt-root", type=Path, default=Path("prompts/codex_v2/event_library")
    )
    parser.add_argument("--expected-atomics", type=int, default=742)
    parser.add_argument("--expected-packages", type=int, default=185)
    parser.add_argument("--wave-size", type=int, default=100)
    parser.add_argument("--wave-token-budget", type=int, default=18_000)
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))
