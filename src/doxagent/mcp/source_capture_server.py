"""MCP 2.0 stdio entrypoint for the Source Capture tool."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from doxagent.codex_runtime.schema import SourceRecord
from doxagent.data_runtime.pilot_case import validate_pilot_case_root
from doxagent.mcp.source_capture import SourceCaptureService
from doxagent.observations.kernel import ObservationKernel
from doxagent.observations.store import AttemptObservationStore
from doxagent.settings import DoxAgentSettings


class WorkspaceSourceRepository:
    """Minimal source repository shared by all MCP processes through the run workspace."""

    def __init__(self, run_root: Path) -> None:
        self._root = run_root.resolve() / "audit" / "sources"
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def list_sources(self, run_id: str) -> list[SourceRecord]:
        values: list[SourceRecord] = []
        for path in sorted(self._root.glob("O*.json")):
            try:
                value = SourceRecord.model_validate_json(path.read_text(encoding="utf-8"))
                if value.run_id == run_id:
                    values.append(value)
            except (OSError, ValueError):
                continue
        return values

    def save_source(self, source: SourceRecord) -> None:
        with self._lock:
            for _attempt in range(20):
                existing = self.list_sources(source.run_id)
                used = {item.alias for item in existing}
                if source.alias in used:
                    highest = max((int(item.alias[1:]) for item in existing), default=0)
                    source.alias = f"O{highest + 1}"
                target = self._root / f"{source.alias}.json"
                descriptor, temporary = tempfile.mkstemp(prefix=f".{source.alias}.", dir=self._root)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(source.model_dump_json(indent=2))
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        os.link(temporary, target)
                        return
                    except FileExistsError:
                        continue
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            raise RuntimeError("could not allocate a unique observation alias")


class ObservationSourceRepository:
    """Source Capture adapter over the shared attempt Observation Kernel."""

    def __init__(
        self,
        *,
        run_root: Path,
        control_root: Path,
        run_id: str,
        attempt_id: str,
    ) -> None:
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._store = AttemptObservationStore(
            control_root=control_root,
            mirror_root=(run_root / "attempts" / attempt_id / "audit" / "observations"),
            run_id=run_id,
            attempt_id=attempt_id,
        )
        self._kernel = ObservationKernel(store=self._store, run_root=run_root)

    def list_sources(self, run_id: str) -> list[SourceRecord]:
        if run_id != self._run_id:
            return []
        values: list[SourceRecord] = []
        for item in self._store.list_all():
            if item.tool_name != "source_capture":
                continue
            coordinates = (
                item.source_coordinates if isinstance(item.source_coordinates, dict) else {}
            )
            url = str(coordinates.get("url") or f"observation://{item.attempt_id}/{item.alias}")
            values.append(
                SourceRecord(
                    source_id=item.tool_call_id,
                    run_id=item.run_id,
                    attempt_id=item.attempt_id,
                    alias=item.alias,
                    url=url,
                    source=item.provider,
                    note=item.title,
                    title=item.title,
                    captured_text=(item.content if isinstance(item.content, str) else None),
                    content_hash=item.content_hash,
                )
            )
        return values

    def save_source(self, source: SourceRecord) -> None:
        observations = self._kernel.ingest_external_source(
            source_id=source.source_id,
            url=source.url,
            source=source.source,
            note=source.note,
            title=source.title,
            cleaned_content=source.captured_text or "",
        )
        primary = next(
            (item for item in observations if item.locator.startswith("/text")),
            observations[0] if observations else None,
        )
        if primary is None:
            raise RuntimeError("source capture produced no cleaned Observation block")
        source.alias = primary.alias


def build_server(service: SourceCaptureService, *, run_id: str, attempt_id: str) -> MCPServer:
    server = MCPServer(
        "doxagent-source-capture",
        instructions=(
            "Capture public web sources and return stable O# aliases. Failures return warnings "
            "and must never block the research workflow. This server does not expose Data MCP."
        ),
    )

    @server.tool(
        name="capture_source",
        description="Capture a public source URL and return an O# citation alias or warning.",
        structured_output=True,
    )
    async def capture_source(
        url: str, source: str | None = None, note: str | None = None
    ) -> dict[str, str]:
        result = await service.capture(
            run_id=run_id,
            attempt_id=attempt_id,
            url=url,
            source=source,
            note=note,
        )
        return result.as_dict()

    return server


def main() -> None:
    pilot_env_file = os.environ.get("DOXAGENT_PILOT_ENV_FILE")
    if pilot_env_file:
        load_dotenv(pilot_env_file, override=False)
    run_id = os.environ.get("DOXAGENT_CODEX_RUN_ID")
    attempt_id = os.environ.get("DOXAGENT_CODEX_ATTEMPT_ID")
    if not run_id or not attempt_id:
        raise RuntimeError("DOXAGENT_CODEX_RUN_ID and DOXAGENT_CODEX_ATTEMPT_ID are required")
    run_root = Path.cwd().resolve()
    control_root_value = os.environ.get("DOXAGENT_OBSERVATION_CONTROL_ROOT")
    if not control_root_value:
        raise RuntimeError("DOXAGENT_OBSERVATION_CONTROL_ROOT is required")
    control_root = Path(control_root_value).resolve()
    pilot_case_id = os.environ.get("DOXAGENT_PILOT_CASE_ID")
    if pilot_case_id:
        validate_pilot_case_root(
            run_root=run_root,
            pilot_case_id=pilot_case_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        expected_control_root = (run_root / ".control" / run_id / attempt_id).resolve()
    else:
        expected_control_root = (run_root.parent / ".control" / run_id / attempt_id).resolve()
    if control_root != expected_control_root:
        raise RuntimeError("Source Capture control root does not match attempt scope")
    repository = ObservationSourceRepository(
        run_root=run_root,
        control_root=control_root,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    settings = DoxAgentSettings()
    build_server(
        SourceCaptureService(
            repository,
            user_agent=settings.sec_user_agent or "DoxAgent-SourceCapture/1.0",
            sec_min_request_interval_seconds=settings.sec_min_request_interval_seconds,
        ),
        run_id=run_id,
        attempt_id=attempt_id,
    ).run("stdio")


if __name__ == "__main__":
    main()
