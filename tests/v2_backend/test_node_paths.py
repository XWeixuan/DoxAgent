from doxagent.api_v2.graph import Graphs
from doxagent.v2_read.repository import ReadStore


def test_node_paths_do_not_splice_edges_from_unrelated_cases(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records = []
    for identity, nodes, edges in (
        ("a", ["W1", "W2"], [["SOURCE", "W1"], ["W1", "W2"], ["W2", "ARCHIVE"]]),
        ("b", ["W2"], [["SOURCE", "W2"], ["W2", "TRADE_EXECUTION"]]),
    ):
        records.append({"kind":"graph_case","ticker":"MU","id":identity,"data":{"nodes":nodes,"edges":edges}})
        for node in nodes:
            records.append({"kind":"graph_member","ticker":"MU","id":identity+":"+node,"parent":node,"day":"2026-09-04","data":{"case_id":identity}})
    seq = store.ingest("test", "paths", records)
    graph = Graphs(store, None)
    assert {e["edge_id"] for e in graph.paths("MU",seq,["2026-09-04"],"W1")} == {"SOURCE:W1","W1:W2","W2:ARCHIVE"}
    assert graph.paths("MU",seq,["2026-09-03"],"W1") == []
    assert len(graph.paths("MU",seq,None,"W2")) == 5
