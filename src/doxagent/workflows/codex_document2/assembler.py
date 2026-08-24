"""Deterministic Document2 assembly and deliberately non-blocking citation remapping."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from doxagent.codex_runtime.schema import CitationEntry, CitationManifest
from doxagent.workflows.codex_document2.schema import (
    CitationResolutionState,
    CitationStatus,
    Document2CitationEntry,
    Document2CitationManifest,
    Document2Document,
    Document2InputManifest,
    ExpectationShell,
    ShellOutcome,
)

_D2_REF = re.compile(r"^D2REF:([^:]+):(O[1-9]\d*)$")
_D1_REF = re.compile(r"^(?:【cite:)?D1-(O[1-9]\d*)(?:】)?$")
_BRACKET = re.compile(r"^【cite:([^】]+)】$")


@dataclass(frozen=True)
class AssembledDocument2:
    document: Document2Document
    citation_manifest: Document2CitationManifest
    citation_status: CitationStatus


def assemble_document2(
    *,
    run_id: str,
    ticker: str,
    as_of: datetime,
    source_global_run_id: str,
    input_manifest: Document2InputManifest,
    shells: list[ExpectationShell],
    shell_outcomes: list[ShellOutcome],
    document_artifact_id: str,
    d1_manifest: CitationManifest | None,
    local_manifests: dict[str, CitationManifest],
    narrative_run_id: str | None,
) -> AssembledDocument2:
    remapper = _CitationRemapper(
        source_global_run_id=source_global_run_id,
        d1_manifest=d1_manifest,
        local_manifests=local_manifests,
        narrative_run_id=narrative_run_id,
    )
    rewritten = [remapper.rewrite_shell(shell) for shell in shells]
    document = Document2Document(
        document2_run_id=run_id,
        ticker=ticker,
        as_of=as_of,
        source_global_run_id=source_global_run_id,
        input_manifest=input_manifest,
        shells=rewritten,
        shell_outcomes=shell_outcomes,
    )
    manifest = Document2CitationManifest(
        run_id=run_id,
        artifact_id=document_artifact_id,
        entries=remapper.entries,
        warnings=remapper.warnings,
    )
    if not manifest.entries:
        status = CitationStatus.UNAVAILABLE
    elif all(item.status is CitationResolutionState.RESOLVED for item in manifest.entries):
        status = CitationStatus.COMPLETE
    else:
        status = CitationStatus.PARTIAL
    return AssembledDocument2(document, manifest, status)


def render_document2_markdown(document: Document2Document) -> str:
    lines = [
        f"# Document2 — {document.ticker}",
        "",
        f"- Run: `{document.document2_run_id}`",
        f"- Source Global run: `{document.source_global_run_id}`",
        f"- As of: `{document.as_of.isoformat()}`",
        "",
    ]
    for shell in document.shells:
        lines.extend(
            [
                f"## {shell.shell_id}",
                "",
                f"**Core question:** {shell.core_question}",
                "",
                f"**Boundary:** {shell.boundary_rule}",
                "",
            ]
        )
        for unit in shell.units:
            lines.extend(
                [
                    f"### {unit.expectation_id}",
                    "",
                    unit.proposition,
                    "",
                    f"Horizon: {unit.horizon}",
                    "",
                    (
                        f"State parameters: {len(unit.state.parameters)}; "
                        f"values: {len(unit.state.values)}"
                    ),
                    "",
                    (
                        f"Realization factors: {len(unit.realization_factors)}; "
                        f"potential gaps: {len(unit.potential_gaps)}"
                    ),
                    "",
                ]
            )
    failed = [item for item in document.shell_outcomes if item.status == "failed"]
    if failed:
        lines.extend(["## Incomplete shells", ""])
        for outcome in failed:
            stage = outcome.failed_stage.value if outcome.failed_stage else "UNKNOWN"
            lines.extend(
                [
                    f"- `{outcome.shell_id}` stopped at `{stage}`: "
                    f"{outcome.error or 'no error detail'}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class _CitationRemapper:
    def __init__(
        self,
        *,
        source_global_run_id: str,
        d1_manifest: CitationManifest | None,
        local_manifests: dict[str, CitationManifest],
        narrative_run_id: str | None,
    ) -> None:
        self._source_global_run_id = source_global_run_id
        self._d1 = {item.alias: item for item in (d1_manifest.entries if d1_manifest else [])}
        self._local = {
            attempt: {item.alias: item for item in manifest.entries}
            for attempt, manifest in local_manifests.items()
        }
        self._narrative_run_id = narrative_run_id
        self._by_raw: dict[str, str] = {}
        self.entries: list[Document2CitationEntry] = []
        self.warnings: list[str] = []

    def rewrite_shell(self, shell: ExpectationShell) -> ExpectationShell:
        payload = shell.model_dump(mode="json")
        for unit in payload.get("units", []):
            state = unit.get("state", {})
            for value in state.get("values", []):
                value["citation"] = self._rewrite_list(value.get("citation", []))
            for factor in unit.get("realization_factors", []):
                factor["citation"] = self._rewrite_list(factor.get("citation", []))
            for gap in unit.get("potential_gaps", []):
                gap["citation"] = self._rewrite_list(gap.get("citation", []))
        return ExpectationShell.model_validate(payload)

    def _rewrite_list(self, values: object) -> list[str]:
        if not isinstance(values, list):
            values = [values] if values else []
        return [self._map(str(value)) for value in values if str(value).strip()]

    def _map(self, raw_value: str) -> str:
        raw = raw_value.strip()
        if raw in self._by_raw:
            return self._by_raw[raw]
        alias = f"O{len(self.entries) + 1}"
        final = f"【cite:{alias}】"
        entry = self._resolve(alias, raw)
        self.entries.append(entry)
        self._by_raw[raw] = final
        if entry.status is not CitationResolutionState.RESOLVED:
            self.warnings.append(f"{alias}: {entry.warning or entry.status.value}")
        return final

    def _resolve(self, alias: str, raw: str) -> Document2CitationEntry:
        inner = _BRACKET.fullmatch(raw)
        normalized = inner.group(1) if inner else raw
        d2_match = _D2_REF.fullmatch(normalized)
        if d2_match:
            attempt_id, origin_alias = d2_match.groups()
            source = self._local.get(attempt_id, {}).get(origin_alias)
            return self._from_source(alias, raw, attempt_id, origin_alias, source)
        d1_match = _D1_REF.fullmatch(raw)
        if d1_match:
            origin_alias = d1_match.group(1)
            source = self._d1.get(origin_alias)
            return self._from_source(
                alias,
                raw,
                None,
                origin_alias,
                source,
                origin_run_id=self._source_global_run_id,
            )
        if normalized.startswith("DoxAtlas:"):
            source_run_id = normalized.split(":", 1)[1] or self._narrative_run_id
            return Document2CitationEntry(
                alias=alias,
                status=(
                    CitationResolutionState.RESOLVED
                    if source_run_id
                    else CitationResolutionState.UNRESOLVED
                ),
                origin_run_id=source_run_id,
                origin_alias=raw,
                title="DoxAtlas narrative report",
                warning=None if source_run_id else "DoxAtlas run id is unavailable",
            )
        if normalized.startswith(("https://", "http://")):
            return Document2CitationEntry(
                alias=alias,
                status=CitationResolutionState.RESOLVED,
                origin_alias=raw,
                url=normalized,
            )
        return Document2CitationEntry(
            alias=alias,
            status=CitationResolutionState.INVALID,
            origin_alias=raw,
            warning=(
                "citation string does not identify a known D1, D2 attempt, "
                "DoxAtlas, or URL source"
            ),
        )

    @staticmethod
    def _from_source(
        alias: str,
        raw: str,
        attempt_id: str | None,
        origin_alias: str,
        source: CitationEntry | None,
        *,
        origin_run_id: str | None = None,
    ) -> Document2CitationEntry:
        if source is None:
            return Document2CitationEntry(
                alias=alias,
                status=CitationResolutionState.UNRESOLVED,
                origin_run_id=origin_run_id,
                origin_attempt_id=attempt_id,
                origin_alias=origin_alias,
                warning=f"unresolved source for {raw}",
            )
        return Document2CitationEntry(
            alias=alias,
            status=(
                CitationResolutionState.RESOLVED
                if source.resolved
                else CitationResolutionState.UNRESOLVED
            ),
            origin_run_id=origin_run_id,
            origin_attempt_id=attempt_id or source.attempt_id,
            origin_alias=origin_alias,
            source_id=source.source_id,
            url=source.url,
            title=source.title,
            warning=source.warning,
        )
