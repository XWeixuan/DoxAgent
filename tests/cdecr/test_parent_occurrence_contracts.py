from cdecr.contracts import EventMention, EventPackage
from cdecr.model_boundary import compact_wire_schema
from cdecr.parent_occurrence_contracts import ParentInductionBatch, ParentResolutionBatch


def test_v2_removes_hint_and_anchor_business_fields() -> None:
    assert "local_package_hint" not in EventMention.model_json_schema()["properties"]
    properties = EventPackage.model_json_schema()["properties"]
    assert "package_anchor_ids" not in properties
    assert "primary_anchor_id" not in properties
    assert "parent_scope" in properties
    assert "partition_hash" in properties


def test_provider_schemas_keep_descriptions_but_remove_generated_titles() -> None:
    for model in (ParentInductionBatch, ParentResolutionBatch):
        schema = compact_wire_schema(model.model_json_schema())
        encoded = str(schema)
        assert "description" in encoded
        assert "'title':" not in encoded
