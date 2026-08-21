"""Non-blocking cited-only promotion from attempt observations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from doxagent.codex_runtime.schema import CitationEntry, CitationManifest, SourceRecord
from doxagent.observations.models import PersistedObservation

_CITATION = re.compile(r"【cite:(O[1-9]\d*)】")


@dataclass(frozen=True)
class AggregateCitationPlan:
    """A deterministic, globally unique citation namespace for one final document."""

    aliases_by_attempt: dict[str, dict[str, str]]
    entries: list[CitationEntry]
    warnings: list[str]

    def rewrite(self, *, attempt_id: str, markdown: str) -> str:
        aliases = self.aliases_by_attempt.get(attempt_id, {})

        def replace(match: re.Match[str]) -> str:
            alias = match.group(1)
            remapped = aliases.get(alias)
            if remapped is None:
                raise ValueError(
                    f"aggregate citation is missing from manifest: {attempt_id}/{alias}"
                )
            return f"【cite:{remapped}】"

        return _CITATION.sub(replace, markdown)


class PromotionRepository(Protocol):
    def save_source(self, source: SourceRecord) -> None: ...
    def save_citation_manifest(self, manifest: CitationManifest) -> None: ...


class CitationPromotionService:
    def __init__(self, repository: PromotionRepository) -> None:
        self._repository = repository

    def promote(
        self,
        *,
        run_id: str,
        attempt_id: str,
        artifact_id: str,
        anchor: str,
        markdown: str,
        observations: list[PersistedObservation],
    ) -> CitationManifest:
        by_alias = {
            item.alias: item
            for item in observations
            if item.run_id == run_id and item.attempt_id == attempt_id
        }
        entries: list[CitationEntry] = []
        warnings: list[str] = []
        for alias in dict.fromkeys(_CITATION.findall(markdown)):
            observation = by_alias.get(alias)
            if observation is None:
                warning = f"unresolved citation alias in {attempt_id}: {alias}"
                warnings.append(warning)
                entries.append(
                    CitationEntry(
                        alias=alias,
                        attempt_id=attempt_id,
                        anchor=anchor,
                        resolved=False,
                        warning=warning,
                    )
                )
                continue
            actual_hash = _content_hash(observation.content)
            if actual_hash != observation.content_hash:
                warning = f"citation checksum mismatch in {attempt_id}: {alias}"
                warnings.append(warning)
                entries.append(
                    CitationEntry(
                        alias=alias,
                        attempt_id=attempt_id,
                        anchor=anchor,
                        block_id=observation.block_id,
                        source_locator=observation.source_locator,
                        content_hash=observation.content_hash,
                        resolved=False,
                        warning=warning,
                    )
                )
                continue
            source_id = "src_" + hashlib.sha256(
                f"{run_id}|{attempt_id}|{observation.block_id}|{actual_hash}".encode()
            ).hexdigest()[:24]
            url = _source_url(observation)
            source = SourceRecord(
                source_id=source_id,
                run_id=run_id,
                attempt_id=attempt_id,
                alias=alias,
                url=url or f"observation://{attempt_id}/{alias}",
                source=observation.provider,
                note=observation.title,
                title=observation.title,
                captured_text=_render_content(observation.content),
                source_type=(
                    "web" if observation.tool_name == "source_capture" else "tool_observation"
                ),
                tool_name=observation.tool_name,
                tool_call_id=observation.tool_call_id,
                block_id=observation.block_id,
                source_locator=observation.source_locator,
                source_coordinates=observation.source_coordinates,
                content_hash=observation.content_hash,
                provider=observation.provider,
                method_version=observation.method_version,
                promoted_from_attempt_id=attempt_id,
            )
            self._repository.save_source(source)
            entries.append(
                CitationEntry(
                    alias=alias,
                    source_id=source_id,
                    url=url,
                    title=observation.title,
                    attempt_id=attempt_id,
                    anchor=anchor,
                    block_id=observation.block_id,
                    source_locator=observation.source_locator,
                    content_hash=observation.content_hash,
                    resolved=True,
                )
            )
        manifest = CitationManifest(
            run_id=run_id,
            artifact_id=artifact_id,
            entries=entries,
            warnings=warnings,
        )
        self._repository.save_citation_manifest(manifest)
        return manifest

    def merge(
        self,
        *,
        run_id: str,
        artifact_id: str,
        manifests: list[CitationManifest],
    ) -> CitationManifest:
        entries = [entry for manifest in manifests for entry in manifest.entries]
        warnings = [warning for manifest in manifests for warning in manifest.warnings]
        merged = CitationManifest(
            run_id=run_id,
            artifact_id=artifact_id,
            entries=entries,
            warnings=warnings,
        )
        self._repository.save_citation_manifest(merged)
        return merged

    def plan_aggregate(self, manifests: list[CitationManifest]) -> AggregateCitationPlan:
        aliases_by_attempt: dict[str, dict[str, str]] = {}
        entries: list[CitationEntry] = []
        warnings = [warning for manifest in manifests for warning in manifest.warnings]
        for manifest in manifests:
            for entry in manifest.entries:
                if not entry.attempt_id:
                    raise ValueError(f"citation {entry.alias} has no attempt identity")
                if not entry.resolved:
                    raise ValueError(
                        f"unresolved citation cannot be aggregated: "
                        f"{entry.attempt_id}/{entry.alias}"
                    )
                attempt_aliases = aliases_by_attempt.setdefault(entry.attempt_id, {})
                remapped = attempt_aliases.get(entry.alias)
                if remapped is None:
                    remapped = f"O{len(entries) + 1}"
                    attempt_aliases[entry.alias] = remapped
                    entries.append(entry.model_copy(update={"alias": remapped}))
        return AggregateCitationPlan(
            aliases_by_attempt=aliases_by_attempt,
            entries=entries,
            warnings=warnings,
        )

    def commit_aggregate(
        self,
        *,
        run_id: str,
        artifact_id: str,
        plan: AggregateCitationPlan,
    ) -> CitationManifest:
        manifest = CitationManifest(
            run_id=run_id,
            artifact_id=artifact_id,
            entries=plan.entries,
            warnings=plan.warnings,
        )
        self._repository.save_citation_manifest(manifest)
        return manifest


def _content_hash(content: object) -> str:
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _render_content(content: object) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def _source_url(observation: PersistedObservation) -> str | None:
    coordinates = observation.source_coordinates
    if isinstance(coordinates, dict):
        value = coordinates.get("url") or coordinates.get("source_url")
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    value = observation.metadata.get("context_envelope", {}).get("url")
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None

