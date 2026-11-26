"""Distinguish current blockers from retained managed-work diagnostics."""

from collections.abc import Iterable

from .schema import NodeRecord


def historical_failed_node_keys(nodes: Iterable[NodeRecord]) -> set[str]:
    """A successful manager supersedes its descendants for retry selection only.

    Keep their FAILED records intact. Missing managers and cycles are not proof
    of a successful handoff and must never hide an actionable failure.
    """
    by_key = {node.key: node for node in nodes}
    historical: set[str] = set()
    for node in by_key.values():
        if node.status != "FAILED":
            continue
        manager = node.inputs.get("managed_by")
        seen = {node.key}
        while manager and manager not in seen:
            seen.add(manager)
            parent = by_key.get(manager)
            if parent is None:
                break
            if parent.status == "SUCCEEDED":
                historical.add(node.key)
                break
            manager = parent.inputs.get("managed_by")
    return historical
