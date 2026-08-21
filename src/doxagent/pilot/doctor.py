"""Preflight and real low-cost MCP verification for a generated Pilot case."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from doxagent.data_runtime.pilot_case import (
    canonical_node_attempt_id,
    validate_pilot_case_root,
)


@dataclass(frozen=True)
class DoctorResult:
    passed: bool
    report_path: Path
    checks: dict[str, object]


async def run_doctor(case_root: str | Path) -> DoctorResult:
    root = Path(case_root).resolve()
    manifest = _load_json(root / "case_manifest.json")
    run_id = str(manifest["run_id"])
    attempt_id = canonical_node_attempt_id(manifest)
    node = str(manifest["node"])
    research_lane = str(manifest.get("research_lane", "legacy_document1"))
    validate_pilot_case_root(
        run_root=root,
        pilot_case_id=str(manifest["case_id"]),
        run_id=run_id,
        attempt_id=attempt_id,
    )
    config = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
    data_config = config["mcp_servers"]["data"]
    python = str(data_config["command"])
    checks: dict[str, object] = {}
    imported = subprocess.run(
        [
            python,
            "-c",
            ("import doxagent.mcp.data_server, doxagent.mcp.source_capture_server; print('ok')"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    checks["python_import"] = {
        "passed": imported.returncode == 0 and imported.stdout.strip() == "ok",
        "returncode": imported.returncode,
        "error": _bounded(imported.stderr),
    }
    expected = set(map(str, manifest["enabled_data_tools"]))
    environment = {**os.environ, **{str(k): str(v) for k, v in data_config["env"].items()}}
    parameters = StdioServerParameters(
        command=python,
        args=list(map(str, data_config["args"])),
        env=environment,
        cwd=Path(str(data_config["cwd"])),
    )
    errlog = cast(TextIO, tempfile.TemporaryFile(mode="w+", encoding="utf-8"))
    try:
        async with stdio_client(parameters, errlog=errlog) as streams:
            async with ClientSession(*streams) as session:
                initialized = await asyncio.wait_for(session.initialize(), timeout=20)
                server_info = getattr(
                    initialized,
                    "server_info",
                    getattr(initialized, "serverInfo", None),
                )
                checks["mcp_initialize"] = {
                    "passed": initialized is not None,
                    "server": getattr(server_info, "name", None),
                }
                listed = await asyncio.wait_for(session.list_tools(), timeout=20)
                actual = {item.name for item in listed.tools}
                checks["tool_whitelist"] = {
                    "passed": actual == expected,
                    "expected": sorted(expected),
                    "actual": sorted(actual),
                    "missing": sorted(expected - actual),
                    "unexpected": sorted(actual - expected),
                }
                guide = await asyncio.wait_for(
                    session.call_tool(
                        "data_tool_guide",
                        arguments={"task": f"Pilot doctor for {research_lane} node {node}"},
                    ),
                    timeout=120,
                )
                checks["data_tool_guide"] = {
                    "passed": not guide.is_error,
                    "summary": _tool_summary(guide),
                }
                probe = manifest["doctor_probe"]
                probe_result = await asyncio.wait_for(
                    session.call_tool(
                        str(probe["name"]),
                        arguments=dict(probe.get("arguments") or {}),
                    ),
                    timeout=120,
                )
                checks["semantic_probe"] = {
                    "passed": _semantic_tool_succeeded(probe_result),
                    "tool": probe["name"],
                    "summary": _tool_summary(probe_result),
                }
    except Exception as exc:
        errlog.flush()
        errlog.seek(0)
        checks["mcp_runtime"] = {
            "passed": False,
            "error": _bounded(_exception_summary(exc)),
            "stderr": _bounded(errlog.read()),
        }
    finally:
        errlog.close()
    passed = all(bool(value.get("passed")) for value in checks.values() if isinstance(value, dict))
    report = {
        "schema_version": "codex-research-pilot-doctor-v2",
        "passed": passed,
        "case_id": manifest["case_id"],
        "run_id": run_id,
        "attempt_id": attempt_id,
        "node": node,
        "research_lane": research_lane,
        "checks": checks,
    }
    report_path = root / "attempts" / attempt_id / "audit" / "doctor_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return DoctorResult(passed=passed, report_path=report_path, checks=checks)


def run_doctor_sync(case_root: str | Path) -> DoctorResult:
    return asyncio.run(run_doctor(case_root))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _tool_summary(result: Any) -> str:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return _bounded(json.dumps(structured, ensure_ascii=False, default=str))
    content = getattr(result, "content", None)
    return _bounded(str(content))


def _semantic_tool_succeeded(result: Any) -> bool:
    if bool(getattr(result, "is_error", False)):
        return False
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "execution_status" in structured:
        return bool(structured["execution_status"] == "succeeded")
    return True


def _bounded(value: str, limit: int = 1000) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _exception_summary(exc: BaseException) -> str:
    nested = getattr(exc, "exceptions", None)
    if isinstance(nested, tuple):
        children = "; ".join(_exception_summary(item) for item in nested)
        return f"{type(exc).__name__}: {exc} [{children}]"
    return f"{type(exc).__name__}: {exc}"
