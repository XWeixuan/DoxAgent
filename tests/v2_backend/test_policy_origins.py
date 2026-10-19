from doxagent.v2_read.policy_origin_trace import trace_origins
from doxagent.v2_read.policy_origins import memberships


def test_memberships_are_multi_shell_deduplicated_and_name_bound():
    shells = [dict(shell_id="shell-0008", name="Supply"),
              dict(shell_id="shell-0001", name="Demand")]
    assert memberships(["Demand", "Supply", "Demand", "OPEN_EVENT"], shells) == (
        ["shell-0001", "shell-0008"], ["OPEN_EVENT"],
    )
    assert memberships([], shells) == ([], [])
    assert memberships(["Supply"], shells + [dict(shell_id="other", name="Supply")]) == (
        [], ["Supply"],
    )


def test_repaired_origins_are_frozen_d2_scoped_and_future_versions_inherit(tmp_path):
    import json

    from doxagent.v2_read.policy_origins import published_origins
    from doxagent.v2_read.repository import ReadStore

    class Source:
        def body(self, *args):
            return {}, json.dumps({"P1": [], "P2": ["Demand"]})

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    frozen = dict(run_id="d2", artifact_id="source", content_sha256="exact-sha")
    store.ingest("repair", "basis", [dict(
        kind="policy_origin_basis", ticker="MU", id="d2",
        data=dict(source_document2=frozen, policy_origins={"P1": ["Supply"], "P2": ["Supply"]}),
    )])
    bundle = dict(run_id="future-maintain", ticker="MU", artifacts=[
        dict(relative_path="artifacts/policy_origins.json", artifact_id="origins")])
    assert published_origins(Source(), bundle, store, frozen) == {
        "P1": ["Supply"], "P2": ["Demand"],
    }
    assert published_origins(Source(), bundle, store, {**frozen, "content_sha256": "other"}) == {
        "P1": [], "P2": ["Demand"],
    }


def test_trace_follows_research_result_topic_owner_and_only_successful_replacements():
    run = dict(
        run_id="frozen-research",
        prepared=dict(document2_ref=dict(run_id="d2-a", sha256="pinned"), topology=dict(
            shells=[dict(name="Supply"), dict(name="Demand")],
            research_owners=dict(S1="Supply", S2="Demand", OPEN_EVENT="OPEN_EVENT"),
        )),
        agenda=dict(topics=[dict(name="a", owner="S1"), dict(name="b", owner="S2"),
                            dict(name="open", owner="OPEN_EVENT")]),
        main_results={"a": [dict(topic="a", policies=["draft-a"])],
                      "b": [dict(topic="b", policies=["draft-b"])],
                      "open": [dict(topic="open", policies=["draft-open"])]},
        coverage=dict(replacements=[dict(before=["A", "draft-b"], after=["merged"]),
                                    dict(before=["draft-a"], after=["failed"])]),
        replacement_receipt=dict(successful=[0]),
        final=[dict(policy_id=p) for p in ("MERGED", "FAILED", "OPEN")],
    )
    drafts = {path: dict(policy=dict(policy_id=pid, ref=["shell-9999"])) for path, pid in (
        ("draft-a", "A"), ("draft-b", "B"), ("merged", "MERGED"),
        ("failed", "FAILED"), ("draft-open", "OPEN"),
    )}
    origins, evidence, issues = trace_origins(run, drafts)
    assert not issues
    assert origins == {"FAILED": [], "MERGED": ["Demand", "Supply"], "OPEN": []}
    assert {e["path"] for e in evidence["MERGED"]} == {"draft-a", "draft-b"}
    assert evidence["OPEN"][0]["owner"] == "OPEN_EVENT"
    assert evidence["OPEN"][0]["shell"] is None
    # A later version inherits the chain without interpreting Policy prose/refs.
    later = dict(run_id="maintain", final=[dict(policy_id="MERGED")])
    assert trace_origins(later, {}, evidence)[0] == {"MERGED": ["Demand", "Supply"]}


def test_cross_shell_policy_is_counted_once_in_all_and_once_in_each_shell(tmp_path):
    from doxagent.api_v2.dto import available
    from doxagent.api_v2.policy_queries import metrics, page
    from doxagent.v2_read.repository import ReadStore

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = dict(shell_ids=["S1", "S2"], lifecycle="ACTIVE", effective=available(True),
                   consumed=available(False), decision="LONG")
    seq = store.ingest("test", "cross-shell", [
        dict(kind="policy_catalog", ticker="MU", id="P1:r1", data=summary),
        *[dict(kind="policy_change", ticker="MU", id="change:"+s, parent=s,
               route="P1:r1", day="2026-10-20", data=dict(type="ADD"))
          for s in ("ALL", "S1", "S2")],
    ])
    for shell in ("ALL", "S1", "S2"):
        assert metrics(store, "MU", seq, shell, None)["active"] == 1
        assert metrics(store, "MU", seq, shell, None)["added"] == 1
        assert len(page(store, "MU", seq, shell, None, "ACTIVE", "", 20)[0]) == 1


def test_backfill_is_idempotent_preserves_old_views_and_change_identity(tmp_path):
    import json

    from doxagent.api_v2.dto import available
    from doxagent.api_v2.policy_queries import metrics
    from doxagent.v2_read.policy_origin_backfill import apply, plan
    from doxagent.v2_read.repository import ReadStore

    class Source:
        def record(self, *args):
            return dict(run_id="d3", ticker="MU", artifacts=[
                dict(relative_path="artifacts/policy_origins.json", artifact_id="origins")])

        def body(self, *args):
            return {}, json.dumps({"P1": ["Supply", "Demand"]})

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = dict(schema_version="document3.policy-summary.v3", policy_id="P1",
                   policy_revision_id="rev", shell_ids=[], unresolved_shell_ids=[],
                   lifecycle="ACTIVE", consumed=available(False), effective=available(True))
    old = store.ingest("fixture", "seed", [
        *[dict(kind="shell", ticker="MU", id="d2:"+sid, parent="d2",
               data=dict(name=name, shell_id=sid))
          for sid, name in (("S1", "Supply"), ("S2", "Demand"))],
        dict(kind="policy_set", ticker="MU", id="artifact", data=dict(
            reference=dict(run_id="d3", schema_version="document3.v3", policy_set_version=1),
            source_document2=dict(run_id="d2"))),
        dict(kind="policy_detail", ticker="MU", id="rev", parent="artifact",
             data=dict(summary=summary, policy=dict(title="unchanged"))),
        dict(kind="policy_catalog", ticker="MU", id="P1:rev", data=summary),
        dict(kind="policy_change", ticker="MU", id="change:ALL", parent="ALL",
             route="P1:rev", sort="2026-10-19", day="2026-10-19",
             data=dict(change_id="change", type="ADD")),
    ])
    planned = plan(store, Source(), "MU", {})
    apply(store, planned)
    assert not plan(store, Source(), "MU", {})
    assert store.get("policy_catalog", "MU", "P1:rev", old)["shell_ids"] == []
    assert store.get("policy_catalog", "MU", "P1:rev")["shell_ids"] == ["S1", "S2"]
    assert store.get("policy_detail", "MU", "rev")["policy"] == dict(title="unchanged")
    with store.connect() as db:
        seq = store.highwater(db)
    for shell in ("ALL", "S1", "S2"):
        assert metrics(store, "MU", seq, shell, None)["added"] == 1
        changes = store.page("policy_change", "MU", seq, parent=shell)
        assert len(changes) == 1 and changes[0]["data"]["change_id"] == "change"
