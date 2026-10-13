from unittest.mock import Mock, patch

from doxagent.v2_read.policy_hits import project


def test_case_loads_frozen_policy_details_once_and_keeps_first_match():
    store = Mock()
    store.get.side_effect = lambda kind, *args: (
        {"policy_set": {"artifact_id": "frozen"}} if kind == "activation_revision" else None
    )
    policies = [{"policy_id": f"p{i}"} for i in range(262)]
    case = {"case_id": "case", "source": {"snapshot": {"ticker": "MU"}},
            "version_pin": {"activation_revision_id": "activation"},
            "frozen_inputs": {"projection": {"policies": policies}},
            "w2_final": {"policy_ids": ["p0", "p261"]}}
    details = {f"p{i}": {"summary": {"policy_id": f"p{i}", "policy_revision_id": f"r{i}"}}
               for i in range(262)}
    details["duplicate"] = {"summary": {"policy_id": "p0", "policy_revision_id": "other"}}
    with patch("doxagent.v2_read.lifecycle.rows", return_value=details) as rows:
        records, contributions = project(store, case, {"final_policy_hit": {"value": True}},
            {"operation": "UPDATE", "recorded_at": "2026-10-13T00:00:00Z"})
    rows.assert_called_once_with(store, "policy_detail", "MU", "frozen")
    assert len(records) == 262 and len(contributions) == 524
    assert records[0]["data"]["ar"] == "r0"
    assert {r["data"]["policy_id"] for r in records if r["data"]["hit"]} == {"p0", "p261"}
