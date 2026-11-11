from datetime import UTC, datetime, timedelta

import pytest

from doxagent.api_v2.article_samples import select_articles
from doxagent.v2_read.decision_metrics import project, span_union


def test_runtime_metrics_http_schema_includes_new_buckets(tmp_path):
    from doxagent.api_v2.runtime import runtime_metrics_data
    from doxagent.api_v2.dto import validate
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    view = {'seq': 0, 'wire': {'period': {'selected': 'CURRENT_TRADING_DAY',
        'current': {'trading_days': ['2026-11-12']}, 'previous': None, 'comparison_applicable': False}}}
    validate('RuntimeMetrics', runtime_metrics_data(store, 'MU', view))
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


def test_capture_requires_verified_publication_time(tmp_path):
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    now = datetime(2026, 11, 12, tzinfo=UTC)
    found = now - timedelta(minutes=5)
    rows = []
    for i, (basis, verified) in enumerate([('EXACT', True), ('DATE', True), ('EXACT', False)]):
        rows.extend([
            dict(kind='native:live_deliveries', ticker='MU', id=str(i), parent=str(i), source_id='news',
                 data=dict(first_discovered_at=found.isoformat(), standard_id='m'+str(i))),
            dict(kind='message', ticker='MU', id='m'+str(i), data=dict(
                pipeline={'discovered_at': found.isoformat()}, stream_published_at=(found+timedelta(seconds=5)).isoformat(),
                source_published_at=(found-timedelta(seconds=300)).isoformat(),
                publication_time_basis=basis, publication_time_verified=verified)),
        ])
    store.ingest('test', 'precision', rows)
    result = select_articles(store, 'MU', ['news'], now)['news']
    assert result['latencies'] == [5, 5, 5]
    assert result['capture'] == [305]


def test_non_model_includes_unbounded_wait_and_stops_at_effective_commit(tmp_path):
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    begin = datetime(2026, 11, 12, tzinfo=UTC)
    at = lambda n: (begin + timedelta(seconds=n)).isoformat()
    native = dict(runtime_mode='REALTIME', created_at=at(0), w3_trade_decision={'decision':'NO_TRADE'})
    summary = dict(case_id='c', semantic_day='2026-11-12', received_at=at(0))
    records = [dict(kind='native:runtime_v2_cases',id='c',data=native)]
    for i, (start, end, phase) in enumerate([(0,10,None),(0,10,None),(15,25,'trade_decision'),(3601,3700,'post_decision_operations')]):
        records.append(dict(kind='attempt',id=str(i),parent='c',data=dict(phase=phase,status='SUCCEEDED',
            model_interval={'start':at(start),'end':at(end)},model_completed_at=begin.timestamp()+end)))
    records.append(dict(kind='native:runtime_v2_effects',id='e',parent='c',data={'payload':{'decision_effective_at':at(3600)}}))
    result, _, counts = project(store,'MU',summary,records)
    assert result['non_model_seconds'] == 3580
    assert {c['metric']:c['value'] for c in counts}['non_model_samples'] == 1


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
