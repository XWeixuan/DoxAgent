"""Small D3 publication hook that durably enqueues O4_CONFIGURE."""

from __future__ import annotations

import hashlib
import json

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.workflows.codex_document2.schema import Document2Document
from doxagent.workflows.codex_document3.schema import PolicySet

from .repository import MonitoringO4Repository
from .schema import O4Request


class Document3MonitoringO4Trigger:
    def __init__(self, repository: MonitoringO4Repository) -> None:
        self._repository = repository

    def on_policy_published(
        self, *, policy_set: PolicySet, document2: Document2Document
    ) -> None:
        policy_json = policy_set.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(
                policy_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        self._repository.enqueue(
            O4Request(
                ticker=policy_set.ticker,
                node=CodexMonitoringO4Node.CONFIGURE,
                payload={
                    "policy_set_json": policy_json,
                    "document2_json": document2.model_dump(mode="json"),
                    "policy_set_sha256": digest,
                    "reason": "document3_published",
                },
                dedupe_key=(
                    f"configure:{policy_set.ticker.upper()}:"
                    f"{policy_set.policy_set_version}:{digest}"
                ),
            )
        )


__all__ = ["Document3MonitoringO4Trigger"]
