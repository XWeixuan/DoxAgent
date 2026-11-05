"""Small host-to-Harness control record; no prompt or SDK protocol changes."""

import json
import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from codex_cli_bin import bundled_path_dir  # type: ignore[import-untyped]
from openai_codex import CodexConfig

from doxagent.codex_runtime.schema import CodexPersistentRuntimeNode
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.settings import DoxAgentSettings

_log = logging.getLogger(__name__)


def configured_codex(config: CodexConfig | None, settings: DoxAgentSettings) -> CodexConfig:
    config = config or CodexConfig(client_name="doxagent-codex-worker")
    if config.codex_bin or not settings.codex_runtime_bin:
        return config
    # An explicit SDK binary skips its normal helper PATH setup (e.g. bundled rg).
    env = dict(config.env or {})
    helper = bundled_path_dir()
    if helper is not None:
        env["PATH"] = str(helper) + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    return replace(config, codex_bin=settings.codex_runtime_bin, env=env)


class W3TimeControl:
    def __init__(self, config: CodexConfig, *, enabled: bool) -> None:
        self.config = config
        self.path: Path | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        if not enabled:
            return
        if not config.codex_bin:
            _log.warning("W3 time awareness disabled: no patched Codex runtime configured")
            return
        budget = (config.env or {}).get("DOXAGENT_MCP_BUDGET_ROOT") or os.environ.get(
            "DOXAGENT_MCP_BUDGET_ROOT"
        )
        if budget:
            root = Path(budget).parent
        else:
            self._temporary = tempfile.TemporaryDirectory(prefix="doxagent-w3-time-")
            root = Path(self._temporary.name)
        self.path = root / "w3-time-context.json"
        self.config = replace(
            config,
            env={**(config.env or {}), "DOXAGENT_W3_TIME_CONTEXT_FILE": str(self.path)},
        )

    def write(self, request: WorkerRunRequest, thread_id: str) -> None:
        value: dict[str, Any] = {"enabled": False}
        if (
            request.node == CodexPersistentRuntimeNode.W3
            and request.task_phase == "trade_decision"
            and request.w3_time_awareness is not None
        ):
            value = dict(
                enabled=True,
                thread_id=thread_id,
                task_phase=request.task_phase,
                **request.w3_time_awareness.model_dump(mode="json"),
            )
        self._write(value)

    def clear(self) -> None:
        self._write({"enabled": False})

    def _write(self, value: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            _log.warning("W3 time control unavailable", exc_info=True)

    def close(self) -> None:
        self.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
