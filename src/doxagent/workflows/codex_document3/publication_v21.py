"""Explicit promotion of an immutable V21 staged release into a formal candidate."""

import hashlib
import json

from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    CodexD3Node,
    PublishedDocument,
    ResearchLane,
)

from .consumer_contracts import parse_policy_set, parse_runtime_projection
from .schema import Document3Bundle, Document3Handoff


async def finalize_candidate_v21(
    run_id, *, state, workspace, runtime_repository, policy_repository, published_storage=None
):
    staged = state.get_staged_by_run(run_id)
    if staged is None:
        raise ValueError("V21 candidate requires a completed staged release")
    payload, receipt = staged
    policy = parse_policy_set(payload)
    projection = parse_runtime_projection(receipt["projection"])
    from .runtime_projection import project_policy_set_v3
    from .state_v21 import digest

    if projection != parse_runtime_projection(project_policy_set_v3(policy)):
        raise ValueError("candidate projection differs from canonical Policy Set")
    if receipt.get("rearm_hash", digest([])) != digest(receipt.get("accepted_rearm", [])):
        raise ValueError("candidate authorization receipt checksum mismatch")
    if (projection.ticker, projection.policy_set_version) != (
        policy.ticker,
        policy.policy_set_version,
    ):
        raise ValueError("candidate projection identity mismatch")
    manifest = json.loads(
        (await workspace.read_text(run_id, receipt["release_manifest_path"])).content
    )
    entries = {v["relative_path"]: v["sha256"] for v in manifest["artifacts"]}
    artifacts = {}
    for name in ("document3.json", "runtime_projection.json", "coverage_map.json", "document3.md"):
        path = receipt["files"][name]
        text = (await workspace.read_text(run_id, path)).content
        sha = hashlib.sha256(text.encode()).hexdigest()
        if sha != receipt["hashes"][name] or entries.get(path) != sha:
            raise ValueError(f"candidate release checksum mismatch: {name}")
        if name == "document3.json" and parse_policy_set(text) != policy:
            raise ValueError("candidate body mismatch")
        if name == "runtime_projection.json" and parse_runtime_projection(text) != projection:
            raise ValueError("candidate projection mismatch")
        artifact = ArtifactRef(
            workflow_version="codex_document3_v1",
            research_lane=ResearchLane.DOCUMENT3,
            artifact_id=hashlib.sha256(f"{run_id}:{name}".encode()).hexdigest()[:32],
            run_id=run_id,
            node=CodexD3Node.PUBLISH,
            attempt_id="v21-candidate",
            kind=ArtifactKind.BUNDLE if name == "document3.json" else ArtifactKind.REPORT,
            relative_path=path,
            sha256=sha,
            size_bytes=len(text.encode()),
            content_type="text/markdown" if name.endswith(".md") else "application/json",
            published=True,
            created_at=policy.published_at,
        )
        runtime_repository.save_artifact(artifact)
        location = {"content_text": text}
        if artifact.size_bytes > 2 * 1024 * 1024:
            if published_storage is None:
                raise ValueError("D3 published artifact exceeds inline limit without storage")
            from doxagent.codex_runtime.published_storage import cache_local_published_content

            cache_local_published_content(runtime_repository, sha, text.encode("utf-8"))
            storage_path = f"codex/document3/{run_id}/{artifact.artifact_id}/{name}"
            await published_storage.put(storage_path, text.encode("utf-8"), artifact.content_type)
            location = {"storage_path": storage_path}
        runtime_repository.save_published_document(
            PublishedDocument(
                artifact_id=artifact.artifact_id,
                run_id=run_id,
                artifact_kind="bundle" if name == "document3.json" else "report",
                sha256=sha,
                size_bytes=artifact.size_bytes,
                content_type=artifact.content_type,
                **location,
                published_at=policy.published_at,
            )
        )
        artifacts[name] = artifact
    policy_repository.publish_candidate(policy, run_id=run_id)
    handoff = Document3Handoff(
        ticker=policy.ticker,
        run_id=run_id,
        policy_set_version=policy.policy_set_version,
        publication_state=policy.publication_state,
        published_at=policy.published_at,
        published_artifact=artifacts["document3.json"],
        coverage_artifact=artifacts["coverage_map.json"],
        runtime_projection_artifact=artifacts["runtime_projection.json"],
        document_schema_version=policy.schema_version,
        projection_schema_version=projection.schema_version,
        release_receipt={
            "run_id": run_id,
            "state_key": run_id,
            "sha256": hashlib.sha256(
                json.dumps(
                    receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        },
    )
    runtime_repository.save_bundle(
        Document3Bundle(
            ticker=policy.ticker,
            run_id=run_id,
            status="published",
            handoff=handoff,
            artifacts=list(artifacts.values()),
            created_at=policy.published_at,
            updated_at=policy.published_at,
            published_at=policy.published_at,
        )
    )
    state.update(run_id, candidate_handoff=handoff.model_dump(mode="json"))
    return handoff
