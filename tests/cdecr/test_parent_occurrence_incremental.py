from inspect import signature

import pytest

from cdecr.cross_document import CrossDocumentEngine
from cdecr.parent_occurrence import ParentOccurrenceService


def test_incremental_engine_uses_parent_induction_for_package_v3() -> None:
    parameters = signature(CrossDocumentEngine.__init__).parameters
    assert "n12_wire_protocol" not in parameters
    assert "n13_wire_protocol" not in parameters
    assert "package_conflict_mode" not in parameters
    assert ParentOccurrenceService.__doc__ == (
        "Document-local Parent Induction service for the Package V3 occurrence pool."
    )


@pytest.mark.parametrize(
    "removed_entrypoint",
    (
        "run",
        "_resolve_wave",
        "_resolution_tasks",
        "_embed_cards",
        "_repair_guarded_unions",
        "_reduce_proposals",
        "_deduplicate_final_event_ownership",
    ),
)
def test_parent_resolution_entrypoint_is_not_executable(removed_entrypoint: str) -> None:
    assert not hasattr(ParentOccurrenceService, removed_entrypoint)
