"""Pinned D3 adapter used by the V2 initialization coordinator."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .orchestrator import Document3Orchestrator


class PinnedDocument3Runner:
    def __init__(
        self, orchestrator: Document3Orchestrator, *, orchestration_version: str = "v2"
    ) -> None:
        if (
            orchestration_version not in {"v2", "v2.1"}
            or getattr(orchestrator, "orchestration_version", "v2") != orchestration_version
        ):
            raise ValueError("Pinned D3 orchestration_version does not match its orchestrator")
        self._orchestrator = orchestrator
        self._orchestration_version = orchestration_version

    async def run_pinned(
        self,
        *,
        document2_run_id: str | None = None,
        ticker: str,
        as_of: datetime,
        event_library_version: int | None = None,
        source_global_run_id: str | None = None,
        additional_materials: tuple = (),
    ) -> str:
        if self._orchestration_version == "v2.1":
            from pathlib import Path

            from .inputs_v21 import utc
            from .state_v21 import digest

            material_summary = []
            for material in additional_materials:
                item = dict(material) if isinstance(material, dict) else {"path": str(material)}
                if not item.get("run_id"):
                    item["sha256"] = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
                material_summary.append(item)

            key = digest(
                {
                    "ticker": ticker.upper(),
                    "document2_run_id": document2_run_id,
                    "event_library_version": event_library_version,
                    "as_of": utc(as_of).isoformat(),
                    "version": "v2.1",
                    "schema": "document3.v3",
                    "source_global_run_id": source_global_run_id,
                    "additional_materials": material_summary,
                }
            )[:24]
            run_id = f"d3v21-{ticker.lower()}-{key}"
            await self._orchestrator.initialize(
                ticker=ticker,
                document2_run_id=document2_run_id,
                event_library_version=event_library_version,
                run_id=run_id,
                as_of=as_of,
                source_global_run_id=source_global_run_id,
                additional_materials=additional_materials,
            )
            return run_id
        if document2_run_id is None or event_library_version is None:
            raise ValueError("V2 pinned D3 requires D2 run and Event version")
        digest = hashlib.sha256(
            f"{ticker.upper()}|{document2_run_id}|{event_library_version}".encode()
        ).hexdigest()[:24]
        run_id = f"d3-{ticker.lower()}-{digest}"
        await self._orchestrator.initialize(
            ticker=ticker,
            document2_run_id=document2_run_id,
            event_library_version=event_library_version,
            run_id=run_id,
            cutoff_at=as_of,
        )
        return run_id
