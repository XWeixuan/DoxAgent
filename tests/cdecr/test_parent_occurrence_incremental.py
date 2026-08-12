from inspect import signature

from cdecr.cross_document import CrossDocumentEngine
from cdecr.parent_occurrence import ParentOccurrenceService


def test_incremental_engine_has_only_parent_v2_package_contract() -> None:
    parameters = signature(CrossDocumentEngine.__init__).parameters
    assert "n12_wire_protocol" not in parameters
    assert "n13_wire_protocol" not in parameters
    assert "package_conflict_mode" not in parameters
    assert (
        ParentOccurrenceService.__doc__ == "The sole bulk and incremental Package business service."
    )
