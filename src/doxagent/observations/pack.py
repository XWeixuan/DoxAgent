"""Atomic materialization of large cleaned Observation Packs."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from doxagent.data_runtime.contracts import DataPackLocator
from doxagent.observations.models import PersistedObservation
from doxagent.observations.projection import observation_projection, observation_source

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ObservationPackWriter:
    def __init__(self, run_root: str | Path) -> None:
        self.run_root = Path(run_root).resolve()

    def write(
        self,
        *,
        attempt_id: str,
        tool_call_id: str,
        observations: list[PersistedObservation],
        selected_aliases: list[str],
    ) -> DataPackLocator:
        self._validate_identifier(attempt_id, "attempt_id")
        self._validate_identifier(tool_call_id, "tool_call_id")
        relative_root = Path("context") / "mcp_data" / attempt_id / tool_call_id
        target = (self.run_root / relative_root).resolve()
        self._ensure_contained(target)
        if target.exists():
            return self._locator(relative_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{tool_call_id}.", dir=target.parent))
        try:
            blocks_root = staging / "blocks"
            blocks_root.mkdir(parents=True)
            manifest_blocks: list[dict[str, object]] = []
            catalog: list[dict[str, object]] = []
            selected = set(selected_aliases)
            for observation in observations:
                suffix, content = render_observation_block(observation)
                block_relative = Path("blocks") / f"{observation.alias}{suffix}"
                block_path = staging / block_relative
                block_path.write_text(content, encoding="utf-8")
                size = len(json.dumps(observation.content, ensure_ascii=False, default=str))
                item: dict[str, object] = {
                    "alias": observation.alias,
                    "title": observation.title,
                    "block_type": observation.block_type,
                    "path": block_relative.as_posix(),
                    "source": observation_source(observation),
                    "content_chars": size,
                    "selected": observation.alias in selected,
                }
                catalog.append(item)
                manifest_blocks.append(
                    {"alias": observation.alias, "path": block_relative.as_posix()}
                )
            (staging / "catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            selected_lines = ["# Selected Data MCP observations", ""]
            by_alias = {item.alias: item for item in observations}
            for alias in selected_aliases:
                selected_observation = by_alias.get(alias)
                if selected_observation is None:
                    continue
                selected_lines.extend(
                    [
                        f"## {alias} — {selected_observation.title}",
                        "",
                        _render_content(selected_observation.content),
                        "",
                        _source_line(selected_observation),
                        "",
                    ]
                )
            (staging / "selected.md").write_text(
                "\n".join(selected_lines).strip() + "\n", encoding="utf-8"
            )
            (staging / "manifest.json").write_text(
                json.dumps(
                    {
                        "selected_aliases": selected_aliases,
                        "blocks": manifest_blocks,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _publish_directory(staging, target)
            _make_read_only(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self._locator(relative_root)

    def _locator(self, relative_root: Path) -> DataPackLocator:
        root = relative_root.as_posix()
        return DataPackLocator(
            root=root,
            manifest_path=f"{root}/manifest.json",
            selected_path=f"{root}/selected.md",
            catalog_path=f"{root}/catalog.json",
        )

    def _ensure_contained(self, path: Path) -> None:
        try:
            path.relative_to(self.run_root)
        except ValueError as exc:
            raise ValueError("Observation Pack escaped run workspace") from exc

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {name}")


def _render_content(content: object) -> str:
    if isinstance(content, str):
        return content
    return "```json\n" + json.dumps(content, ensure_ascii=False, indent=2) + "\n```"


def _source_line(observation: PersistedObservation) -> str:
    source = observation_source(observation)
    value = source["provider"]
    if locator := source.get("locator"):
        value += f" · {locator}"
    return f"Source: {value}"


def _publish_directory(staging: Path, target: Path) -> None:
    """Atomically publish a pack with a bounded Windows scanner-contention retry."""

    for attempt in range(4):
        try:
            os.rename(staging, target)
            return
        except PermissionError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) != 5 or attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def render_observation_block(observation: PersistedObservation) -> tuple[str, str]:
    """Return the canonical on-disk block representation used for tamper checks."""

    if isinstance(observation.content, str):
        return (
            ".md",
            f"# {observation.alias} — {observation.title}\n\n"
            f"{observation.content}\n\n"
            f"{_source_line(observation)}\n",
        )
    return (
        ".json",
        json.dumps(
            observation_projection(observation),
            ensure_ascii=False,
            indent=2,
        ),
    )


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            path.chmod(0o555 if path.is_dir() else 0o444)
        except OSError:
            continue
    try:
        root.chmod(0o555)
    except OSError:
        pass
