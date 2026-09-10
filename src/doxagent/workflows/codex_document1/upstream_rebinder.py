"""Rebind cited upstream observations into the current attempt's private store."""

from __future__ import annotations

import hashlib
import re
from typing import Any, cast

from doxagent.codex_runtime.client import WorkspaceClient
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.observations.models import PersistedObservation

_CITATION = re.compile(r"【cite:(O[1-9]\d*)】")
_MARKER = "__upstream_artifact_id"


def upstream_handoff(payload: dict[str, Any], artifact_id: str | None) -> dict[str, Any]:
    value = dict(payload)
    if artifact_id:
        value[_MARKER] = artifact_id
    return value


class UpstreamObservationRebinder:
    def __init__(self, workspace: WorkspaceClient, repository: CodexRuntimeRepository) -> None:
        self._workspace = workspace
        self._repository = repository

    async def rebind_payload(
        self, *, run_id: str, attempt_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return cast(dict[str, object], await self._walk(run_id, attempt_id, payload))

    async def _walk(self, run_id: str, attempt_id: str, value: Any) -> Any:
        if isinstance(value, list):
            return [await self._walk(run_id, attempt_id, item) for item in value]
        if not isinstance(value, dict):
            return value
        if _MARKER in value:
            return await self._rebind_handoff(run_id, attempt_id, value)
        rebound: dict[str, Any] = {}
        for key, item in value.items():
            rebound[key] = await self._walk(run_id, attempt_id, item)
        return rebound

    async def _rebind_handoff(
        self, run_id: str, attempt_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        artifact_id = str(value[_MARKER])
        artifact = self._repository.get_artifact(run_id, artifact_id)
        manifest = self._repository.get_citation_manifest(run_id, artifact_id)
        clean = {key: item for key, item in value.items() if key != _MARKER}
        warnings = list(clean.get("warnings") or [])
        if artifact is None or manifest is None:
            return self._downgrade(clean, {}, warnings, "upstream_evidence_unavailable")
        canonical = await self._workspace.read_attempt_observations(run_id, artifact.attempt_id)
        by_alias = {item.alias: item for item in canonical}
        requested: list[tuple[str, str, PersistedObservation]] = []
        canonical_by_old_alias: dict[str, str] = {}
        missing: set[str] = set()
        seen: set[str] = set()
        for entry in manifest.entries:
            if not entry.resolved or not entry.alias:
                missing.add(entry.alias)
                continue
            observation = by_alias.get(entry.alias)
            if observation is None:
                missing.add(entry.alias)
                continue
            canonical_key = entry.source_id or observation.content_hash
            canonical_by_old_alias[entry.alias] = canonical_key
            if canonical_key in seen:
                continue
            seen.add(canonical_key)
            token = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:24]
            requested.append(
                (
                    entry.alias,
                    canonical_key,
                    observation.model_copy(
                        update={
                            "run_id": run_id,
                            "attempt_id": attempt_id,
                            "alias": "",
                            "block_id": f"rehydrated_{token}",
                            "tool_call_id": f"rehydrated_{token}",
                            "metadata": {
                                **observation.metadata,
                                "rehydrated": True,
                                "derived_from_attempt_id": artifact.attempt_id,
                                "derived_from_alias": entry.alias,
                                "canonical_source_id": canonical_key,
                            },
                        }
                    ),
                )
            )
        try:
            imported = await self._workspace.import_attempt_observations(
                run_id, attempt_id, [item[2] for item in requested]
            )
        except (OSError, ValueError) as exc:
            warnings.append(f"upstream_evidence_unavailable: {exc}")
            return self._downgrade(clean, {}, warnings, None)
        alias_by_canonical = {
            canonical_key: stored.alias
            for (_old_alias, canonical_key, _observation), stored in zip(
                requested, imported, strict=True
            )
        }
        mapping = {
            old_alias: alias_by_canonical[canonical_key]
            for old_alias, canonical_key in canonical_by_old_alias.items()
            if canonical_key in alias_by_canonical
        }
        if missing:
            warnings.append(
                "upstream_evidence_unavailable: "
                + ", ".join(sorted(item for item in missing if item))
            )
        return self._downgrade(clean, mapping, warnings, None)

    @staticmethod
    def _downgrade(
        value: dict[str, Any],
        mapping: dict[str, str],
        warnings: list[str],
        warning: str | None,
    ) -> dict[str, Any]:
        if warning:
            warnings.append(warning)

        def rewrite(item: Any) -> Any:
            if isinstance(item, str):
                return _CITATION.sub(
                    lambda match: (
                        f"【cite:{mapping[match.group(1)]}】"
                        if match.group(1) in mapping
                        else "[upstream evidence unavailable]"
                    ),
                    item,
                )
            if isinstance(item, list):
                return [rewrite(child) for child in item]
            if isinstance(item, dict):
                return {key: rewrite(child) for key, child in item.items()}
            return item

        value = rewrite(value)
        candidates = value.get("observation_candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                aliases = candidate.get("source_aliases")
                if isinstance(aliases, list):
                    candidate["source_aliases"] = [
                        mapping[item] for item in aliases if item in mapping
                    ]
        if warnings:
            value["warnings"] = list(dict.fromkeys(warnings))
        return value
