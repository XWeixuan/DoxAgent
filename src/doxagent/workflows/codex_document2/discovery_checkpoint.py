"""D2-only, write-once Scan submission inside one Open Discovery turn."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.workflows.codex_document2.errors import (
    Document2ExecutionError,
    Document2FailureKind,
)
from doxagent.workflows.codex_document2.schema import (
    OpenDiscoveryCheckpointV21,
    OpenDiscoveryCompletionV21,
    OpenDiscoveryResultV21,
    OpenDiscoveryScanV21,
)

CHECKPOINT_PATH = "context/document2/open_discovery_checkpoint.json"
SCAN_PATH = "context/document2/open_discovery_scan.json"
TOOL_NAME = "commit_open_discovery_scan"
_ALIAS = re.compile(r"^(?:【cite:)?(O[1-9]\d*)(?:】)?$")


def stable_json(value: BaseModel | dict) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def qualify_refs(value, attempt_id):
    """Limit alias qualification to references, never business names."""
    if isinstance(value, list):
        return [qualify_refs(item, attempt_id) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: [
            f"D2REF:{attempt_id}:{match.group(1)}" if (match := _ALIAS.fullmatch(ref)) else ref
            for ref in item
        ]
        if key == "ref" and isinstance(item, list)
        else qualify_refs(item, attempt_id)
        for key, item in value.items()
    }


def checkpoint_error(message):
    return Document2ExecutionError(
        message,
        code="D2_DISCOVERY_CHECKPOINT_INVALID",
        kind=Document2FailureKind.SYSTEM,
        node=CodexD2Node.O1_OPEN_DISCOVERY,
        retryable=False,
    )


def validate_checkpoint(checkpoint, context, workspace_run_id):
    from .validation import validate_output

    if (
        checkpoint.workspace_run_id != workspace_run_id
        or checkpoint.shell != context["canonical_shell"]["name"]
        or checkpoint.research_cutoff_at
        != datetime.fromisoformat(str(context["research_cutoff_at"]).replace("Z", "+00:00"))
        or checkpoint.seed_sha256 != sha(stable_json(context["canonical_shell"]))
        or checkpoint.scan_sha256 != sha(stable_json(checkpoint.scan))
        or not checkpoint.producer_attempt_id
    ):
        raise ValueError("Open Discovery checkpoint identity or SHA mismatch")
    validate_output(checkpoint.scan, context)


def assemble_result(completion, checkpoint, context, workspace_run_id):
    from .validation import validate_output

    validate_checkpoint(checkpoint, context, workspace_run_id)
    completion = OpenDiscoveryCompletionV21.model_validate(completion)
    if completion.scan_sha256 != checkpoint.scan_sha256:
        raise checkpoint_error("Open Discovery completion does not match frozen Scan SHA")
    validate_output(
        completion.selection,
        {**context, "open_discovery_scan": checkpoint.scan.model_dump(mode="json")},
    )
    return OpenDiscoveryResultV21(checkpoint=checkpoint, selection=completion.selection)


async def read_checkpoint(workspace, workspace_run_id, context):
    try:
        body = await workspace.read_text(workspace_run_id, CHECKPOINT_PATH)
    except FileNotFoundError:
        return None
    try:
        checkpoint = OpenDiscoveryCheckpointV21.model_validate_json(body.content or "")
        validate_checkpoint(checkpoint, context, workspace_run_id)
        expected = stable_json(checkpoint.scan)
        try:
            scan = await workspace.read_text(workspace_run_id, SCAN_PATH)
        except FileNotFoundError:
            await workspace.write_text(workspace_run_id, SCAN_PATH, expected)
        else:
            if scan.content != expected:
                raise ValueError("frozen Scan file differs from committed checkpoint")
    except (ValueError, TypeError, KeyError) as exc:
        raise checkpoint_error(str(exc)) from exc
    return checkpoint


def task_contract(context):
    return {
        "tool": TOOL_NAME,
        "checkpoint_path": CHECKPOINT_PATH,
        "scan_path": SCAN_PATH,
        "resume_from": context.get("resume_from", "FULL_SCAN"),
        "protocol": (
            "Within this single turn, submit the full Scan through commit_open_discovery_scan, "
            "wait for its successful frozen Scan and SHA, then produce Selection over that Scan. "
            "Return selection and scan_sha256 only. Never write or overwrite checkpoint files "
            "using shell tools. If resume_from is SELECTION, use the injected frozen Scan/SHA "
            "without rescanning or replacing candidates. If the checkpoint file already exists "
            "in an exported source Pilot case, read that frozen Scan/SHA and continue Selection "
            "even when the original immutable context predates the checkpoint."
        ),
    }


@contextmanager
def _commit_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            # Reading byte zero fails while another Windows process holds its
            # byte-range lock. Inspect size without touching the locked byte.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("D2 checkpoint commit lock timed out") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DiscoveryCheckpointService:
    def __init__(self, run_root: Path, run_id: str, attempt_id: str, pilot_case_id=None):
        self.root = run_root.resolve()
        self.store = LocalWorkspaceStore(self.root.parent)
        # The physical Pilot directory is not the logical workspace run ID.
        self.physical_id = self.root.name
        LocalWorkspaceStore._validate_identifier(run_id, "run_id")
        LocalWorkspaceStore._validate_identifier(attempt_id, "attempt_id")
        self.run_id, self.attempt_id = run_id, attempt_id
        if pilot_case_id:
            from doxagent.data_runtime.pilot_case import validate_pilot_case_root

            validate_pilot_case_root(
                run_root=self.root,
                pilot_case_id=pilot_case_id,
                run_id=run_id,
                attempt_id=attempt_id,
                node=CodexD2Node.O1_OPEN_DISCOVERY,
            )
        elif self.physical_id != run_id:
            raise ValueError("D2 checkpoint workspace root does not match run_id")
        prefix = f"attempts/{attempt_id}/input"
        task = json.loads(self.store.read_text(self.physical_id, f"{prefix}/task.json").content)
        self.context = json.loads(
            self.store.read_text(self.physical_id, f"{prefix}/context.json").content
        )
        if (
            task["node"] != CodexD2Node.O1_OPEN_DISCOVERY.value
            or self.context.get("document_schema_version") != "document2.v2.1"
            or self.context.get("discovery_contract_version") != "single-v1"
        ):
            raise ValueError("checkpoint tool requires a bound single-v1 Open Discovery attempt")

    def load(self):
        try:
            body = self.store.read_text(self.physical_id, CHECKPOINT_PATH)
        except FileNotFoundError:
            return None
        checkpoint = OpenDiscoveryCheckpointV21.model_validate_json(body.content or "")
        validate_checkpoint(checkpoint, self.context, self.run_id)
        # write_text accepts same bytes, rejects conflicting immutable Scan.
        self.store.write_text(self.physical_id, SCAN_PATH, stable_json(checkpoint.scan))
        return checkpoint

    def commit(self, scan):
        from .validation import validate_output

        scan = OpenDiscoveryScanV21.model_validate(scan)
        validate_output(scan, self.context)
        qualified = OpenDiscoveryScanV21.model_validate(
            qualify_refs(scan.model_dump(mode="json"), self.attempt_id)
        )
        with _commit_lock(self.root / "audit" / "d2-discovery-commit.lock"):
            existing = self.load()
            if existing is not None:
                if stable_json(qualified) != stable_json(existing.scan):
                    raise ValueError("Open Discovery Scan is already frozen; replacement denied")
                checkpoint = existing
            else:
                checkpoint = OpenDiscoveryCheckpointV21(
                    workspace_run_id=self.run_id,
                    shell=scan.shell,
                    research_cutoff_at=self.context["research_cutoff_at"],
                    seed_sha256=sha(stable_json(self.context["canonical_shell"])),
                    producer_attempt_id=self.attempt_id,
                    scan_sha256=sha(stable_json(qualified)),
                    scan=qualified,
                )
                self.store.write_text(self.physical_id, CHECKPOINT_PATH, stable_json(checkpoint))
                self.store.write_text(self.physical_id, SCAN_PATH, stable_json(qualified))
        return {
            "scan": checkpoint.scan.model_dump(mode="json"),
            "scan_sha256": checkpoint.scan_sha256,
            "checkpoint_path": CHECKPOINT_PATH,
            "producer_attempt_id": checkpoint.producer_attempt_id,
        }


def finalize_pilot(case_root, attempt_id):
    """Validate the one case before the coordinator accepts it as completed."""
    manifest = json.loads((case_root / "case_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("discovery_contract_version") != "single-v1":
        raise ValueError("Pilot Discovery contract mismatch; create a single-v1 case")
    service = DiscoveryCheckpointService(
        case_root, manifest["run_id"], attempt_id, manifest["case_id"]
    )
    checkpoint = service.load()
    if checkpoint is None:
        raise ValueError("Pilot Open Discovery completion is missing its frozen Scan checkpoint")
    output = case_root / "attempts" / attempt_id / "output"
    completion = json.loads((output / "completion.json").read_text(encoding="utf-8"))
    result = assemble_result(completion, checkpoint, service.context, service.run_id)
    result.selection = type(result.selection).model_validate(
        qualify_refs(result.selection.model_dump(mode="json"), attempt_id)
    )
    service.store.write_text(
        service.physical_id,
        f"attempts/{attempt_id}/output/open_discovery_result.json",
        stable_json(result),
    )
    return result


def main():
    from mcp.server.mcpserver import MCPServer

    service = DiscoveryCheckpointService(
        Path.cwd(),
        os.environ["DOXAGENT_CODEX_RUN_ID"],
        os.environ["DOXAGENT_CODEX_ATTEMPT_ID"],
        os.environ.get("DOXAGENT_PILOT_CASE_ID"),
    )
    server = MCPServer(
        "doxagent-d2-discovery",
        instructions=task_contract(service.context)["protocol"],
    )

    @server.tool(name=TOOL_NAME, structured_output=True)
    def commit_open_discovery_scan(scan: OpenDiscoveryScanV21) -> dict[str, Any]:
        """Freeze the full Scan before Selection within this same Open Discovery turn."""
        return service.commit(scan)

    server.run("stdio")


if __name__ == "__main__":
    main()
