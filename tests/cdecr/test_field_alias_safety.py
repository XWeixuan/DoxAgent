from __future__ import annotations

from pathlib import Path

from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.field_coreference_contracts import (
    FieldCoreferenceCandidate,
    FieldCoreferenceInput,
    FieldCoreferenceModelOutput,
    FieldDecision,
    FieldNamespace,
)
from cdecr.ports import EmbeddingResult, StructuredModelRequest
from cdecr.registry import SQLiteCDECRRegistry


class NoEmbeddings:
    def embed(self, texts: list[str]) -> EmbeddingResult:
        raise AssertionError(f"unexpected embeddings: {texts}")


class NoModel:
    def complete(self, request: StructuredModelRequest) -> object:
        raise AssertionError(f"unexpected model call: {request.user_prompt}")


def test_llm_candidate_link_does_not_promote_raw_value_to_runtime_alias(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "alias.sqlite3")
    registry.initialize()
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=NoEmbeddings(),
        model_client=NoModel(),  # type: ignore[arg-type]
    )
    value = FieldCoreferenceInput(
        namespace=FieldNamespace.METRIC,
        raw_value="ambiguous operating result",
        local_context="The company discussed an ambiguous operating result.",
    )
    entry = resolver.ensure_external_entry(
        value,
        external_id="OPERATING_INCOME",
        canonical_text="Operating income",
        aliases=["operating profit"],
    )
    def accept_link(_: object) -> bool:
        return True

    registry.save_field_link = accept_link  # type: ignore[method-assign]
    resolver._apply_internal(
        value,
        FieldCoreferenceModelOutput(
            decision=FieldDecision.LINK,
            canonical_id=entry.id,
        ),
        [
            FieldCoreferenceCandidate(
                canonical_id=entry.id,
                aliases=["Operating income"],
            )
        ],
        mention_id="M-ALIAS",
        field_path="quantities[0].metric_id",
        run_id="RUN-ALIAS",
    )
    refreshed = registry.resolve_field_registry_entry(entry.id)
    assert refreshed is not None
    assert "ambiguous operating result" not in refreshed.aliases
