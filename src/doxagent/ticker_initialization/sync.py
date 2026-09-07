"""Asynchronous compact summary delivery; cloud availability never gates a run."""

from typing import Any, Protocol

import httpx

from .repository import InitializationRepository


class SummarySink(Protocol):
    async def upsert(self, summary: dict[str, Any]) -> None:
        """Conditionally upsert by initialization_id, accepting only a newer state_seq."""
        ...


class SupabaseSummarySink:
    """Server-only RPC; no body payloads, worker receipts, or diagnostic text."""

    def __init__(self, url: str, secret: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/") + "/rest/v1/rpc/upsert_ticker_initialization_summary"
        self.client = client or httpx.AsyncClient(timeout=10)
        self.headers = {"apikey": secret, "Authorization": f"Bearer {secret}"}

    async def upsert(self, summary: dict[str, Any]) -> None:
        selected = {
            key: summary[key]
            for key in (
                "initialization_id",
                "ticker",
                "state_seq",
                "status",
                "phase",
                "created_at",
                "updated_at",
                "manual_resume_required",
                "has_error",
            )
        }
        for key in (
            "operation_kind",
            "failed_nodes",
            "diagnostics_count",
            "activation_manifest",
            "last_operator_action",
        ):
            if key in summary:
                selected[key] = summary[key]
        response = await self.client.post(
            self.url, headers=self.headers, json={"summary": selected}
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self.client.aclose()


async def flush_summaries(repository: InitializationRepository, sink: SummarySink) -> int:
    delivered = 0
    for summary in repository.outbox():
        try:
            await sink.upsert(summary)
        except Exception:
            # Keep the coalesced local row; a later flush retries it, not the workflow.
            break
        repository.acknowledge_summary(summary["initialization_id"], summary["state_seq"])
        delivered += 1
    return delivered
