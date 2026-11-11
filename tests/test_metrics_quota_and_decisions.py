from datetime import UTC, datetime, timedelta

import pytest

from doxagent.api_v2.article_samples import select_articles
from doxagent.v2_read.decision_metrics import project, span_union
from doxagent.v2_read.repository import ReadStore


@pytest.mark.parametrize(
    "recent,old,expected", [(0, 3, 3), (0, 8, 5), (4, 8, 5), (5, 8, 5), (19, 8, 19), (25, 8, 20)]
)
def test_quota_is_articles_not_successful_publications(tmp_path, recent, old, expected):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_article_sample_index()
    now = datetime(2026, 11, 12, tzinfo=UTC)
    rows = []
    for i in range(recent + old):
        at = now - timedelta(minutes=i) if i < recent else now - timedelta(days=2, minutes=i)
        rows.append(
            dict(
                kind="native:live_deliveries",
                ticker="MU",
                id=str(i),
                parent=str(i),
                source_id="news",
                data=dict(first_discovered_at=at.isoformat(), standard_id=None),
            )
        )
    store.ingest("test", "quota", rows)
    v = select_articles(store, "MU", ["news"], now)["news"]
    assert v["article_samples"] == expected
    assert v["body_total"] == expected  # Unpublished/pending articles remain in the cohort.
    assert v["latencies"] == []


def test_union_subtracts_parallel_calls_only_once():
    t = datetime(2026, 11, 12, tzinfo=UTC)
    def at(s):
        return t + timedelta(seconds=s)
    assert span_union([(at(0), at(10)), (at(0), at(10)), (at(15), at(25))], at(0), at(30)) == 20


@pytest.mark.parametrize(
    "duration,mean_samples,long", [(180, 1, 0), (181, 1, 1), (600, 1, 1), (601, 0, 1), (3600, 0, 1)]
)
def test_long_tag_does_not_inherit_mean_cutoff(tmp_path, duration, mean_samples, long):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    begin = datetime(2026, 11, 12, tzinfo=UTC)
    summary = dict(case_id="c", semantic_day="2026-11-12", received_at=begin.isoformat())
    native = dict(
        runtime_mode="REALTIME",
        w3_trade_decision={"decision": "NO_TRADE"},
        w2_final={"policy_related": True},
    )
    attempt = dict(
        phase="trade_decision",
        status="SUCCEEDED",
        model_interval={
            "start": begin.isoformat(),
            "end": (begin + timedelta(seconds=duration)).isoformat(),
        },
        model_completed_at=begin.timestamp() + duration,
    )
    incoming = [
        dict(kind="native:runtime_v2_cases", id="c", data=native),
        dict(kind="attempt", id="j", parent="c", data=attempt),
    ]
    result, tag, counts = project(store, "MU", summary, incoming)
    values = {x["metric"]: x["value"] for x in counts}
    assert values["w3_model_le600_samples"] == mean_samples
    assert values["long_w3_cases"] == long
    assert bool(result["runtime_tags"]) == bool(long)
    assert values["non_model_samples"] == 0
