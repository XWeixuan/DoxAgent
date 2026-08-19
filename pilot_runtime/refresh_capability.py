"""Rotate the signed Data MCP capability for an existing Pilot case."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_CAPABILITY_YEARS = 10
_CAPABILITY_LINE = re.compile(
    r'(?m)^DOXAGENT_DATA_MCP_CAPABILITY = "[^"]*"$'
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh a persistent Codex D1 Pilot Data MCP capability"
    )
    parser.add_argument(
        "--case",
        dest="cases",
        action="append",
        type=Path,
        required=True,
        help="Existing Pilot case root; repeat for multiple cases",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=DEFAULT_CAPABILITY_YEARS,
        help="Capability lifetime in years (default: 10)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(r"D:\DoxAgentPilot\runtime\.env.local"),
    )
    args = parser.parse_args()
    if args.years < 1:
        raise SystemExit("--years must be at least 1")
    load_dotenv(args.env_file, override=True)

    repo_root = os.environ.get("DOXAGENT_PILOT_REPO_ROOT")
    if repo_root:
        sys.path.insert(0, str(Path(repo_root).resolve() / "src"))
    from doxagent.data_runtime.policy import DataCapabilityCodec
    from doxagent.settings import DoxAgentSettings

    settings = DoxAgentSettings()
    if not settings.codex_capability_secret:
        raise RuntimeError("DOXAGENT_CODEX_CAPABILITY_SECRET is missing")
    codec = DataCapabilityCodec(settings.codex_capability_secret)
    ttl_hours = args.years * 365 * 24
    refreshed = []
    for raw_case in args.cases:
        refreshed.append(
            _refresh_case(
                raw_case.resolve(),
                codec=codec,
                ttl_hours=ttl_hours,
            )
        )
    print(json.dumps(refreshed, ensure_ascii=False, indent=2))
    print("Capability values were not printed.")
    return 0


def _refresh_case(
    case_root: Path,
    *,
    codec: object,
    ttl_hours: int,
) -> dict[str, object]:
    manifest_path = case_root / "case_manifest.json"
    config_path = case_root / ".codex" / "config.toml"
    if not manifest_path.is_file() or not config_path.is_file():
        raise ValueError(f"not a Pilot case root: {case_root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = _read_toml(config_path)
    data_env = config["mcp_servers"]["data"]["env"]
    if data_env.get("DOXAGENT_DATA_MCP_PUBLIC_KEY") != codec.public_key:
        raise ValueError(f"capability key mismatch: {case_root}")
    from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node

    expires_at = int(datetime.now(UTC).timestamp()) + ttl_hours * 3600
    attempt_id = str(manifest.get("node_attempt_id") or manifest["attempt_id"])
    capability = codec.issue(
        run_id=str(manifest["run_id"]),
        node_id=CodexD1Node(str(manifest["node"])),
        node_attempt_id=attempt_id,
        agent_role=CodexAgentRole(str(manifest["agent_role"])),
        ticker=str(manifest["ticker"]),
        cutoff_at=datetime.fromisoformat(str(manifest["cutoff_at"])),
        enabled_tool_ids=list(manifest["enabled_canonical_tools"]),
        ttl_seconds=ttl_hours * 3600,
        pilot_case_id=str(manifest["case_id"]),
    )
    updated_config = _CAPABILITY_LINE.sub(
        f'DOXAGENT_DATA_MCP_CAPABILITY = "{capability}"',
        config_path.read_text(encoding="utf-8"),
        count=1,
    )
    if updated_config == config_path.read_text(encoding="utf-8"):
        raise ValueError(f"capability setting not found: {config_path}")
    _write_preserving_readonly(config_path, updated_config)

    refreshed_at = datetime.now(UTC).isoformat()
    manifest["capability_expires_after_hours"] = ttl_hours
    manifest["capability_refreshed_at"] = refreshed_at
    _write_preserving_readonly(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    audit_path = case_root / "audit" / "capability_refresh.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps(
            {
                "schema_version": "codex-d1-capability-refresh-v1",
                "case_id": manifest["case_id"],
                "node": manifest["node"],
                "node_attempt_id": attempt_id,
                "refreshed_at": refreshed_at,
                "expires_at": expires_at,
                "expires_after_hours": ttl_hours,
                "input_sha256_preserved": manifest["input_sha256"],
                "changed_files": [".codex/config.toml", "case_manifest.json"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "case_id": manifest["case_id"],
        "node": manifest["node"],
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "input_sha256": manifest["input_sha256"],
    }


def _read_toml(path: Path) -> dict[str, object]:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def _write_preserving_readonly(path: Path, content: str) -> None:
    was_readonly = not os.access(path, os.W_OK)
    if was_readonly:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if was_readonly:
            os.chmod(path, stat.S_IREAD)


if __name__ == "__main__":
    raise SystemExit(main())
