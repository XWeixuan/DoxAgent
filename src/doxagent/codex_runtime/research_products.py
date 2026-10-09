"""Read-only views of the three independent, pinned D1 research products."""

from __future__ import annotations

import hashlib
from typing import Any

PRODUCT_ROLES = {
    "future_nodes": "c4f_future_nodes",
    "entity_relations": "c4e_formal_scan",
    "entity_network_report": "c4e_network_build",
}


def project_d1_products(bundle) -> dict[str, dict[str, Any]]:
    products = {}
    for key, role in PRODUCT_ROLES.items():
        content = getattr(bundle, key, "" if key == "entity_network_report" else [])
        if isinstance(content, list):
            content = [
                x.model_dump(mode="json", by_alias=True) if hasattr(x, "model_dump") else x
                for x in content
            ]
        recorded = getattr(bundle, "c4_product_status", {}).get(key)
        state = recorded or (
            "available"
            if content
            else "unavailable"
            if key == "entity_network_report"
            else "unrecorded"
        )
        ref = getattr(bundle, "reports", {}).get(role)
        products[key] = {
            "product_key": key,
            "producer_role": role,
            "role": role,
            "state": state,
            "content": ("" if key == "entity_network_report" else [])
            if state == "failed"
            else content,
            "source_run_id": bundle.run_id,
            "source_kind": "published_d1_product_projection"
            if recorded or ref
            else "legacy_published_snapshot",
            "original_ref": ref.relative_path if ref else None,
            "source_artifact_id": ref.artifact_id if ref else None,
            "source_sha256": ref.sha256 if ref else None,
            "producer_attempt_id": ref.attempt_id if ref else None,
            "source_files": {},
            "source_warnings": [],
        }
    return products


async def load_d1_products(bundle, repository, read_text):
    """Resolve provenance only within each published producer's accepted attempt.

    read_text receives an ArtifactRef and returns original UTF-8 text. Bundle
    content remains authoritative when historical source files were cleaned up.
    """
    products = project_d1_products(bundle)
    artifacts = repository.list_artifacts(bundle.run_id, limit=500)
    for product in products.values():
        report = getattr(bundle, "reports", {}).get(product["producer_role"])
        if report is None:
            product["source_warnings"].append("producer reference not recorded")
            continue
        refs = [
            report,
            *[
                ref
                for ref in artifacts
                if ref.attempt_id == report.attempt_id
                and getattr(ref.node, "value", ref.node) == product["producer_role"]
                and getattr(ref.kind, "value", ref.kind) == "structured_completion"
            ],
        ]
        for ref in refs:
            try:
                text = await read_text(ref)
                if text is None or hashlib.sha256(text.encode("utf-8")).hexdigest() != ref.sha256:
                    raise ValueError("source checksum mismatch")
                product["source_files"][ref.relative_path] = text
                if ref != report:
                    product["completion_ref"] = {
                        "original_ref": ref.relative_path,
                        "source_artifact_id": ref.artifact_id,
                        "source_sha256": ref.sha256,
                    }
            except (OSError, ValueError) as exc:
                product["source_warnings"].append(f"{ref.relative_path}:{exc}")
        product["provenance_available"] = report.relative_path in product["source_files"]
        if hasattr(repository, "get_citation_manifest"):
            manifest = repository.get_citation_manifest(bundle.run_id, report.artifact_id)
            if manifest is not None:
                product["citation_manifest"] = manifest.model_dump(mode="json")
    return products


def d1_products_context(products):
    """Shared D2 fields; raw source bytes stay unqualified inside provenance."""
    return {
        **{key: product["content"] for key, product in products.items()},
        "d1_product_status": {key: p["state"] for key, p in products.items()},
        "d1_product_sources": {
            key: {k: v for k, v in product.items() if k != "content"}
            for key, product in products.items()
        },
    }
