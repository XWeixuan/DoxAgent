"""Selectively adopt immutable research into production; activate through the normal DAG."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from doxagent.codex_runtime.published_storage import cache_local_published_content
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import PublishedDocument
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.workflows.codex_document3.consumer_contracts import parse_policy_set
from doxagent.workflows.codex_document3.publication_v21 import (
    finalize_candidate_v21,
    render_policy_markdown,
)
from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
from doxagent.workflows.codex_document3.runtime_projection import project_policy_set_v3
from doxagent.workflows.codex_document3.state_v21 import StateV21, canonical, digest


def source_database(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def immutable_file(target: Path, content: bytes):
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError(f"immutable adoption file collision: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


class Workspace:
    def __init__(self, root):
        self.local = LocalWorkspaceStore(root)

    async def read_text(self, run_id, path):
        return self.local.read_text(run_id, path)


def inspect_source(spec):
    with source_database(spec["source_database"]) as db:
        row = db.execute(
            "SELECT payload,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?",
            (spec["d3_run"],),
        ).fetchone()
        if row is None:
            raise ValueError("source staged release missing")
        payload, receipt = json.loads(row[0]), json.loads(row[1])
        policy = parse_policy_set(payload)
        root = Path(spec["source_workspace"]) / spec["d3_run"]
        for name, path in receipt["files"].items():
            actual = hashlib.sha256((root / path).read_bytes()).hexdigest()
            if actual != receipt["hashes"][name]:
                raise ValueError(f"source release checksum mismatch: {name}")
        if receipt["hashes"]["document3.json"] != spec["source_sha256"]:
            raise ValueError("source release differs from approved manifest")
        if policy.document2_ref.run_id != spec["d2_run"] or policy.ticker != spec["ticker"]:
            raise ValueError("source D2/Policy provenance mismatch")
        run = json.loads(
            db.execute(
                "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (spec["d3_run"],)
            ).fetchone()[0]
        )
        return policy, receipt, run


def copy_records(spec, repository):
    runs = [spec["d1_run"], spec["d2_run"], spec["d3_run"]]
    with (
        source_database(spec["source_database"]) as source,
        sqlite3.connect(repository.path, timeout=30) as target,
    ):
        for table in (
            "codex_runtime_records",
            "codex_local_sources",
            "codex_local_citation_manifests",
        ):
            info = source.execute(f"PRAGMA table_info({table})").fetchall()
            columns = [r[1] for r in info]
            keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            query = " OR ".join("(run_id=? OR run_id LIKE ?)" for _ in runs)
            args = [v for run in runs for v in (run, run + "-%")]
            for row in source.execute(f"SELECT * FROM {table} WHERE {query}", args):
                values = dict(row)
                prior = target.execute(
                    f"SELECT * FROM {table} WHERE " + " AND ".join(k + "=?" for k in keys),
                    [values[k] for k in keys],
                ).fetchone()
                if prior:
                    existing = dict(zip(columns, prior, strict=True))
                    for column in columns:
                        if column == "updated_at":
                            continue
                        left, right = existing[column], values[column]
                        if table == "codex_runtime_records" and column == "payload_json":
                            left, right = json.loads(left), json.loads(right)
                            if values["record_type"] == "published_documents":
                                for item in (left, right):
                                    item.pop("content_text", None)
                                    item.pop("storage_path", None)
                        if left != right:
                            raise ValueError(f"research identity collision: {table}:{keys}")
                    continue
                target.execute(
                    f"INSERT INTO {table} ("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    [values[c] for c in columns],
                )
    # Materialize only the imported run-scoped documents; never copy source storage settings.
    with source_database(repository.path) as target:
        rows = target.execute(
            "SELECT payload_json FROM codex_runtime_records "
            "WHERE record_type='published_documents' "
            "AND (" + " OR ".join("run_id=? OR run_id LIKE ?" for _ in runs) + ")",
            [v for run in runs for v in (run, run + "-%")],
        ).fetchall()
    origin = Path(spec["source_database"]).parent
    for row in rows:
        document = PublishedDocument.model_validate_json(row[0])
        content = document.content_text.encode() if document.content_text is not None else None
        if content is None:
            candidates = [
                origin / "published-contents" / document.sha256,
                origin / "published" / document.run_id / document.artifact_id,
            ]
            if document.storage_path:
                path = (origin / "published" / document.storage_path).resolve()
                if not path.is_relative_to(origin.resolve()):
                    raise ValueError("source published path escapes research directory")
                candidates.append(path)
            content = next((p.read_bytes() for p in candidates if p.is_file()), None)
        if (
            content is None
            or len(content) != document.size_bytes
            or hashlib.sha256(content).hexdigest() != document.sha256
        ):
            raise ValueError(f"published source body missing or corrupt: {document.artifact_id}")
        cache_local_published_content(repository, document.sha256, content)
        repository.save_published_document(
            PublishedDocument.model_validate(
                {
                    **document.model_dump(),
                    "content_text": content.decode("utf-8"),
                    "storage_path": None,
                }
            )
        )


def archive_evidence(spec, adoption_root):
    source_root = Path(spec["source_workspace"])
    runs = {spec["d3_run"]}
    with source_database(spec["source_database"]) as db:
        for row in db.execute(
            "SELECT payload FROM codex_document3_v21_tasks WHERE run_id=?", (spec["d3_run"],)
        ):
            run = json.loads(row[0]).get("workspace_run_id")
            if run:
                runs.add(run)
    files = []
    for run in sorted(runs):
        for folder in ("context", "output/work", "artifacts"):
            for source in (source_root / run / folder).rglob("*"):
                if not source.is_file() or source.is_symlink():
                    continue
                # Context-reader page caches duplicate immutable D1/D2 bodies.
                # Keep task/index files and original outputs; imported records
                # and published bodies retain the underlying reference material.
                if (
                    folder == "context"
                    and "materials" in source.relative_to(source_root / run).parts
                ):
                    continue
                relative = source.relative_to(source_root)
                destination = adoption_root / "source-workspaces" / relative
                from doxagent.codex_runtime.immutable_evidence import adopt_file
                adopt_file(source,destination,adoption_root.parent.parent/'adoption-contents')
                files.append(str(relative))
    return files


def materialize_upstream_workspaces(spec, repository, workspace_root):
    """Make hash-pinned D1/D2 artifacts readable by the formal Worker."""
    copied = []
    for run_id in (spec["d1_run"], spec["d2_run"]):
        bundle = repository.get_bundle(run_id)
        reports = getattr(bundle, "reports", {})
        required = {ref.artifact_id for ref in reports.values()}
        handoff = getattr(bundle, "handoff", None)
        document_id = getattr(handoff, "document_artifact_id", None) or getattr(
            handoff, "document2_artifact_id", None
        )
        accepted_attempts = {(ref.node, ref.attempt_id) for ref in reports.values()}
        refs = {
            ref.artifact_id: ref
            for ref in repository.list_artifacts(run_id, limit=500)
            if ref.artifact_id == document_id
            or (
                (ref.node, ref.attempt_id) in accepted_attempts
                and getattr(ref.kind, "value", ref.kind) == "structured_completion"
            )
        }
        refs.update({ref.artifact_id: ref for ref in reports.values()})
        for ref in refs.values():
            source = Path(spec["source_workspace"]) / run_id / ref.relative_path
            published = repository.get_published_document(run_id, ref.artifact_id)
            content = (
                source.read_bytes()
                if source.is_file()
                else (
                    published.content_text.encode()
                    if published and published.content_text is not None
                    else None
                )
            )
            if content is None:
                if ref.artifact_id in required or ref.published:
                    raise ValueError(
                        f"upstream workspace artifact missing: {run_id}/{ref.relative_path}"
                    )
                continue
            if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != ref.sha256:
                raise ValueError(
                    f"upstream workspace artifact checksum mismatch: {ref.relative_path}"
                )
            immutable_file(Path(workspace_root) / run_id / ref.relative_path, content)
            copied.append({"run_id": run_id, "path": ref.relative_path, "sha256": ref.sha256})
    return copied


async def adopt(spec, *, database, workspace_root):
    source_policy, source_receipt, source_state = inspect_source(spec)
    repository = SQLiteCodexRuntimeRepository(database)
    policies = SQLiteDocument3PolicyRepository(database)
    state = StateV21(database)
    # Also heal a prior completed adoption whose published bodies exist but
    # whose Worker-facing upstream artifact files have not been materialized.
    copy_records(spec, repository)
    upstream = materialize_upstream_workspaces(spec, repository, workspace_root)
    archive_root = Path(database).parent / "adoptions" / spec["adoption_run"]
    existing = archive_root / "adoption-receipt.json"
    if existing.is_file():
        if json.loads((archive_root / "source-manifest.json").read_text()) != spec:
            raise ValueError("adoption manifest changed for an existing release")
        prior = json.loads(existing.read_text())
        staged = state.get_staged_by_run(spec["adoption_run"])
        if staged is None or staged[1]["hashes"] != prior["target_hashes"]:
            raise ValueError("adoption checkpoint differs from receipt")
        await finalize_candidate_v21(
            spec["adoption_run"],
            state=state,
            workspace=Workspace(workspace_root),
            runtime_repository=repository,
            policy_repository=policies,
        )
        return prior
    run_id = spec["adoption_run"]
    version = state.reserve(run_id, spec["ticker"])
    raw = source_policy.model_dump(mode="json")
    patches = {p["policy_id"]: p for p in spec.get("policy_repairs", [])}
    if set(patches) - {p["policy_id"] for p in raw["policies"]}:
        raise ValueError("repair must target an existing source Policy")
    raw["policies"] = [patches.get(p["policy_id"], p) for p in raw["policies"]]
    raw["policy_set_version"] = version
    policy = parse_policy_set(raw)
    workspace = Workspace(workspace_root)
    coverage = json.loads(
        (
            Path(spec["source_workspace"])
            / spec["d3_run"]
            / source_receipt["files"]["coverage_map.json"]
        ).read_text()
    )
    coverage["policy_set_version"] = version
    repaired_paths = set(spec.get("repair_paths", []))
    remaining_missing = [
        value
        for value in source_state["missing"]
        if not any(value == "final edit incomplete:" + path for path in repaired_paths)
    ]
    coverage["host_missing"] = remaining_missing
    coverage["remaining_gaps"] = [
        gap
        for gap in coverage.get("remaining_gaps", [])
        if not any(path in gap.get("reason", "") for path in repaired_paths)
    ]
    if "version" in coverage:
        coverage["version"] = version
    payloads = {
        "document3.json": policy.model_dump_json(indent=2),
        "runtime_projection.json": canonical(project_policy_set_v3(policy)),
        "coverage_map.json": canonical(coverage),
        "document3.md": render_policy_markdown(
            policy, status=source_state["status"], coverage=coverage
        ),
    }
    paths = {name: f"artifacts/document3/releases/{version}/{name}" for name in payloads}
    for name, text in payloads.items():
        immutable_file(Path(workspace_root) / run_id / paths[name], text.encode())
    published = workspace.local.publish(run_id, list(paths.values()), release_only=True)
    manifest_path = next(
        f.relative_path
        for f in published.files
        if f.relative_path.startswith("published/") and f.relative_path.endswith("/manifest.json")
    )
    receipt = {
        **source_receipt,
        "version": version,
        "files": paths,
        "hashes": {name: digest(text) for name, text in payloads.items()},
        "projection": project_policy_set_v3(policy),
        "final_hash": digest([p.model_dump(mode="json") for p in policy.policies]),
        "coverage_hash": digest(coverage),
        "release_manifest_path": manifest_path,
        "accepted_rearm": [],
        "rearm_hash": digest([]),
    }
    state.start(
        run_id,
        {"adoption_source": spec["source_sha256"], "repairs": digest(patches)},
        {
            "run_id": run_id,
            "ticker": spec["ticker"],
            "mode": "initialize",
            "phase": "STAGED",
            "status": source_state["status"],
            "missing": remaining_missing,
            "identity": source_state["identity"],
            "coverage": source_state.get("coverage", {}),
        },
    )
    state.save_staged_v3(run_id, policy, receipt)
    handoff = await finalize_candidate_v21(
        run_id,
        state=state,
        workspace=workspace,
        runtime_repository=repository,
        policy_repository=policies,
    )
    files = archive_evidence(spec, archive_root)
    result = {
        "ticker": spec["ticker"],
        "run_id": run_id,
        "version": version,
        "source_sha256": spec["source_sha256"],
        "target_hashes": receipt["hashes"],
        "repaired_policy_ids": sorted(patches),
        "policy_count": len(policy.policies),
        "research_cutoff_at": policy.as_of.isoformat(),
        "evidence_files": len(files),
        "upstream_workspace_files": len(upstream),
        "handoff": handoff.model_dump(mode="json"),
        "research_refs": {
            "document1": {"run_id": spec["d1_run"]},
            "document2": {"run_id": spec["d2_run"]},
            "document3": {"run_id": run_id, "version": version},
        },
    }
    immutable_file(archive_root / "source-manifest.json", canonical(spec).encode())
    immutable_file(archive_root / "adoption-receipt.json", canonical(result).encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "import", "activate", "verify"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    spec = manifest["tickers"][args.ticker]
    from doxagent.settings import DoxAgentSettings

    settings = DoxAgentSettings(_env_file=None)
    if args.command == "plan":
        policy, receipt, run = inspect_source(spec)
        print(
            canonical(
                {
                    "ticker": args.ticker,
                    "policy_count": len(policy.policies),
                    "source_hashes": receipt["hashes"],
                    "missing": run["missing"],
                }
            )
        )
    elif args.command == "import":
        print(
            canonical(
                asyncio.run(
                    adopt(
                        spec,
                        database=settings.codex_runtime_sqlite_path,
                        workspace_root=settings.codex_workspace_root,
                    )
                )
            )
        )
    else:
        from doxagent.ticker_initialization.operations import submit_activation
        from doxagent.ticker_initialization.repository import InitializationRepository

        control = InitializationRepository(settings.ticker_initialization_control_path)
        receipt = json.loads(
            (
                Path(settings.codex_runtime_sqlite_path).parent
                / "adoptions"
                / spec["adoption_run"]
                / "adoption-receipt.json"
            ).read_text()
        )
        active = control.active_revision(args.ticker)
        if args.command == "activate":
            refs = {**active["artifacts"], **receipt["research_refs"]}
            if all(active["artifacts"].get(k) == v for k, v in receipt["research_refs"].items()):
                print(canonical({"already_active": active["revision_id"]}))
                return
            run = submit_activation(
                control,
                args.ticker,
                artifacts=refs,
                reason="V21 reviewed research adoption",
                cutoff=datetime.fromisoformat(receipt["research_cutoff_at"]),
                expected_base_revision=active["revision_id"],
                preserve_monitoring_configuration=True,
            )
            print(run.model_dump_json())
        else:
            expected = receipt["research_refs"]
            matches = all(active["artifacts"].get(k) == v for k, v in expected.items())
            acks = {
                worker: control.revision_acknowledged(args.ticker, active["revision_id"], worker)
                for worker in ("bus", "runtime")
            }
            print(canonical({"active": active, "research_matches": matches, "acks": acks}))
            if not matches or not all(acks.values()):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
