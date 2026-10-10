import pytest

from doxagent.api_v2.errors import ApiFailure
from doxagent.api_v2.views import Views
from doxagent.v2_read.graph import project
from doxagent.v2_read.repository import ReadStore


def test_candidate_origin_filter_is_applied_before_pagination_and_bound_to_cursor(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "fixture",
        "candidates",
        [
            {
                "kind": "candidate",
                "ticker": "MU",
                "id": str(i),
                "parent": "case",
                "sort": str(i),
                "data": {"candidate_id": str(i), "originating_node": origin},
            }
            for i, origin in enumerate(["W1_R3", "W3", "W3", "W1_R3", "W3"])
        ],
    )
    views = Views(store, None)
    first = views.page(
        "owner", "candidate", "MU", view=None, parent="case", limit=1, originating_node="W1_R3"
    )
    assert first["items"] == [{"candidate_id": "3", "originating_node": "W1_R3"}]
    second = views.page(
        "owner",
        "candidate",
        "MU",
        view=None,
        parent="case",
        limit=1,
        originating_node="W1_R3",
        cursor=first["next_cursor"],
    )
    assert second["items"][0]["candidate_id"] == "0" and not second["has_more"]
    with pytest.raises(ApiFailure):
        views.page(
            "owner",
            "candidate",
            "MU",
            view=None,
            parent="case",
            limit=1,
            originating_node="W3",
            cursor=first["next_cursor"],
        )


def test_tags_filter_same_view_and_graph_retracts_old_routes(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = {
        "case_id": "case",
        "results": ["BADCASE"],
        "first_round_tags": ["FIRST_ROUND_TRADE", "POLICY_RELATED_EVENT"],
        "first_round_shape": "PARALLEL",
        "w3_status": "RUNNING",
        "status": "PENDING_W3",
        "semantic_day": "2026-10-10",
        "received_at": "2026-10-10T00:00:00Z",
    }
    attempts = [
        {
            "kind": "attempt",
            "ticker": "MU",
            "id": node,
            "parent": "case",
            "data": {
                "node_id": node,
                "status": "SUCCEEDED",
                "timing": {"first_started_at": {"value": None}, "completed_at": {"value": None}},
            },
        }
        for node in ("W1", "W2", "W3")
    ]
    seq = store.ingest(
        "fixture",
        "old",
        [
            {"kind": "case", "ticker": "MU", "id": "case", "data": summary},
            {
                "kind": "graph_case",
                "ticker": "MU",
                "id": "case",
                "data": {"nodes": ["SOURCE", "W1", "W2", "BADCASE"], "edges": [["W2", "BADCASE"]]},
            },
            *attempts,
        ],
    )
    assert len(store.page("case", "MU", seq, result="FIRST_ROUND_TRADE")) == 1
    records, counts = project(store, "MU", summary, [])
    graph = next(r["data"] for r in records if r["kind"] == "graph_case")
    assert "BADCASE" not in graph["nodes"]
    assert ("FIRST_ROUND_TRADE", "W3") in graph["edges"]
    assert ("POLICY_RELATED_EVENT", "W3") in graph["edges"]
    assert any(
        c["metric"] == "graph_edges"
        and c["dimensions"] == {"from": "W2", "to": "BADCASE"}
        and c["value"] == 0
        for c in counts
    )
    store.ingest(
        "fixture",
        "update",
        [
            {
                "kind": "case",
                "ticker": "MU",
                "id": "case",
                "data": {**summary, "first_round_tags": []},
            }
        ],
    )
    assert store.page(
        "case",
        "MU",
        seq,
        result="FIRST_ROUND_TRADE",
    )  # pinned old view
    with store.connect() as db:
        current = store.highwater(db)
    assert store.page("case", "MU", current, result="FIRST_ROUND_TRADE") == []
