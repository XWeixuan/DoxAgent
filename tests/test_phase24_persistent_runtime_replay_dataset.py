from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from doxagent.models import AgentName, AgentResult, ResultStatus
from doxagent.monitoring.schema import (
    EventStreamItem,
    FetchedExternalMessage,
    MonitoringSourceConfig,
    SourceType,
    TickerSourceBinding,
)
from doxagent.persistent_runtime import (
    AgentRunnerO3Worker,
    O3PrimaryAction,
    O3Result,
    O3RuntimeBudget,
)
from doxagent.persistent_runtime.datasets import (
    build_dataset,
    clean_events_for_dataset,
    compact_runtime_context,
    count_monitoring_sqlite_events,
    export_monitoring_sqlite_dataset,
    fetch_live_dataset,
    load_runtime_context_from_exports,
    read_dataset,
    write_dataset,
)
from doxagent.persistent_runtime.replay import RuntimeDatasetReplayer, main
from doxagent.persistent_runtime.schema import RuntimeSourceMessage


def _event(
    *,
    event_id: str,
    stream_offset: int,
    source_type: SourceType,
    published_at: datetime,
    ticker: str = "MU",
    batch_window_id: str | None = None,
) -> EventStreamItem:
    source_id = "benzinga_news" if source_type is SourceType.MEDIA else "stocktwits_messages"
    message_id = f"std_{event_id}"
    metadata: dict[str, str] = {}
    if batch_window_id is not None:
        metadata["batch_window_id"] = batch_window_id
    payload: dict[str, object] = {
        "standard_message_id": message_id,
        "ticker": ticker,
        "source_id": source_id,
        "source_type": source_type.value,
        "title": f"{ticker} test event {event_id}",
        "body": f"{ticker} runtime replay body {event_id}",
        "symbols": [ticker],
        "published_at": published_at.isoformat(),
        "collected_at": published_at.isoformat(),
        "metadata": metadata,
    }
    return EventStreamItem(
        event_id=event_id,
        stream_offset=stream_offset,
        standard_message_id=message_id,
        event_time=published_at,
        ticker=ticker,
        source_id=source_id,
        payload=payload,
        consumed=True,
    )


class FakeReplayService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str]]] = []

    def execute_event(
        self,
        event: EventStreamItem,
        *,
        context: dict[str, Any] | None = None,
        mark_consumed: Any = None,
    ) -> object:
        self.calls.append(("media", "", [event.event_id]))
        if mark_consumed is not None:
            mark_consumed(event.event_id)
        return object()

    def execute_social_batch(
        self,
        messages: list[RuntimeSourceMessage],
        *,
        ticker: str,
        batch_window_id: str,
        context: dict[str, Any] | None = None,
    ) -> list[object]:
        self.calls.append(
            ("social", batch_window_id, [message.source_message_id for message in messages])
        )
        return [object() for _ in messages]


class FakeHistoricalCollector:
    def __init__(self, published_at: datetime) -> None:
        self.published_at = published_at

    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        return [
            FetchedExternalMessage(
                source_id=source.source_id,
                binding_id=binding.binding_id,
                ticker=binding.ticker,
                source_type=source.source_type,
                interface_type=source.interface_type,
                raw_payload={
                    "id": "provider-mu-older",
                    "headline": "MU historical event",
                    "summary": "MU dataset builder should keep historical events.",
                },
                provider_message_id="provider-mu-older",
                source_url="https://example.test/mu-older",
                source_published_at=self.published_at,
                metadata={"provider": "fake"},
            )
        ]


class FakeCollectorRegistry:
    def __init__(self, published_at: datetime) -> None:
        self.collector = FakeHistoricalCollector(published_at)

    def collector_for(self, source: MonitoringSourceConfig) -> FakeHistoricalCollector:
        return self.collector


class FakeStructuredRunner:
    def run(self, task: object) -> AgentResult:
        return AgentResult(
            task_id="task_structured",
            agent_name=AgentName.O3_TRADING_STRATEGY,
            status=ResultStatus.SUCCEEDED,
            payload={
                "runtime": "react",
                "structured": O3Result(
                    primary_action=O3PrimaryAction.ARCHIVE,
                    reasoning="wrapped react result",
                ).model_dump(mode="json"),
            },
        )


def test_dataset_cleaning_sorts_reassigns_offsets_and_adds_social_batch_metadata() -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    events = [
        _event(
            event_id="evt_late",
            stream_offset=9,
            source_type=SourceType.MEDIA,
            published_at=base + timedelta(minutes=10),
        ),
        _event(
            event_id="evt_social",
            stream_offset=2,
            source_type=SourceType.SOCIAL,
            published_at=base + timedelta(minutes=1),
        ),
    ]

    cleaned = clean_events_for_dataset(events, ticker="mu")

    assert [event.event_id for event in cleaned] == ["evt_social", "evt_late"]
    assert [event.stream_offset for event in cleaned] == [1, 2]
    assert cleaned[0].consumed is False
    metadata = cleaned[0].payload["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["batch_window_id"] == "202606200001"
    assert metadata["item_id"] == "std_evt_social"


def test_dataset_jsonl_roundtrip_preserves_manifest_and_events(tmp_path: Path) -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    dataset = build_dataset(
        (
            _event(
                event_id=f"evt_{index}",
                stream_offset=index,
                source_type=SourceType.MEDIA,
                published_at=base + timedelta(minutes=index),
            )
            for index in [2, 1]
        ),
        ticker="MU",
        source_types=[SourceType.MEDIA],
        window_start=base,
        window_end=base + timedelta(days=7),
    )

    event_path, manifest_path = write_dataset(dataset, tmp_path / "mu-media.jsonl")
    loaded = read_dataset(event_path, manifest_path=manifest_path)

    assert loaded.manifest.ticker == "MU"
    assert loaded.manifest.event_count == 2
    assert [event.event_id for event in loaded.events] == ["evt_1", "evt_2"]


def test_sqlite_export_reports_empty_dataset_when_monitoring_db_is_absent(tmp_path: Path) -> None:
    missing_db = tmp_path / "missing.sqlite3"

    assert count_monitoring_sqlite_events(missing_db, ticker="MU", days=7) == 0

    dataset = export_monitoring_sqlite_dataset(missing_db, ticker="MU", days=7)

    assert dataset.events == []
    assert dataset.manifest.ticker == "MU"
    assert dataset.manifest.source["found"] is False


def test_replay_cli_exports_empty_sqlite_dataset_without_running_runtime(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "mu.jsonl"

    exit_code = main(
        [
            "export-sqlite",
            "--sqlite-path",
            str(tmp_path / "missing.sqlite3"),
            "--ticker",
            "MU",
            "--days",
            "7",
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    loaded = read_dataset(out_path)
    assert loaded.events == []
    assert loaded.manifest.ticker == "MU"


def test_replay_cli_dry_run_validates_order_without_runtime_execution(tmp_path: Path) -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    dataset = build_dataset(
        [
            _event(
                event_id="evt_social",
                stream_offset=1,
                source_type=SourceType.SOCIAL,
                published_at=base,
                batch_window_id="window-a",
            ),
            _event(
                event_id="evt_media",
                stream_offset=2,
                source_type=SourceType.MEDIA,
                published_at=base + timedelta(minutes=1),
            ),
        ],
        ticker="MU",
    )
    event_path, _ = write_dataset(dataset, tmp_path / "mu.jsonl")

    exit_code = main(["dry-run", str(event_path), "--interval-ms", "0"])

    assert exit_code == 0


def test_replay_cli_sample_dataset_writes_one_stride_slice(tmp_path: Path) -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    dataset = build_dataset(
        [
            _event(
                event_id=f"evt_{index}",
                stream_offset=index,
                source_type=SourceType.MEDIA,
                published_at=base + timedelta(minutes=index),
            )
            for index in range(1, 11)
        ],
        ticker="MU",
        source_types=[SourceType.MEDIA],
    )
    event_path, _ = write_dataset(dataset, tmp_path / "mu.jsonl")

    exit_code = main(
        [
            "sample-dataset",
            str(event_path),
            "--stride",
            "5",
            "--out",
            str(tmp_path / "mu-1of5.jsonl"),
        ]
    )

    assert exit_code == 0
    sampled = read_dataset(tmp_path / "mu-1of5.jsonl")
    assert [event.event_id for event in sampled.events] == ["evt_1", "evt_6"]
    assert [event.stream_offset for event in sampled.events] == [1, 2]


def test_agent_runner_o3_worker_accepts_react_structured_payload_wrapper() -> None:
    event = _event(
        event_id="evt_o3_wrapper",
        stream_offset=1,
        source_type=SourceType.MEDIA,
        published_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    result = AgentRunnerO3Worker(FakeStructuredRunner()).judge(
        RuntimeSourceMessage.from_event(event),
        {},
        budget=O3RuntimeBudget(),
    )

    assert result.primary_action is O3PrimaryAction.ARCHIVE


def test_replay_cli_writes_runtime_sqlite_records(tmp_path: Path) -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    dataset = build_dataset(
        [
            _event(
                event_id="evt_known",
                stream_offset=1,
                source_type=SourceType.MEDIA,
                published_at=base,
            )
        ],
        ticker="MU",
        source_types=[SourceType.MEDIA],
    )
    event_path, _ = write_dataset(dataset, tmp_path / "mu.jsonl")
    source_export = {
        "export_metadata": {"run_id": "run_source"},
        "stable_documents": {
            "global_research": {
                "doc_global": {"document": {"ticker": "MU", "document_id": "doc_global"}}
            },
            "expectation_unit": {
                "expectation_1": {"document": {"ticker": "MU", "expectation_id": "expectation_1"}}
            },
            "known_events": {
                "doc_ke": {
                    "document": {
                        "ticker": "MU",
                        "events": [
                            {
                                "event_id": "KE_1",
                                "core_fact": "MU runtime replay body evt_known",
                                "duplicate_detection_keys": ["runtime replay body"],
                            }
                        ],
                    }
                }
            },
            "monitoring_config": {
                "doc_config": {"document": {"ticker": "MU", "monitoring_items": []}}
            },
            "monitoring_policy": {
                "doc_policy": {"document": {"ticker": "MU", "direct_trade_rules": []}}
            },
        },
    }
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source_export), encoding="utf-8")
    runtime_db = tmp_path / "runtime.sqlite3"

    exit_code = main(
        [
            "replay",
            str(event_path),
            "--source-run-export",
            str(source_path),
            "--runtime-sqlite-path",
            str(runtime_db),
            "--source-type",
            "media",
        ]
    )

    assert exit_code == 0
    assert runtime_db.exists()
    conn = sqlite3.connect(runtime_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("select payload_json from persistent_runtime_executions").fetchone()
    assert row is not None
    payload = json.loads(str(row["payload_json"]))
    assert payload["timing"]["runtime"]["total_ms"] >= 0
    assert payload["timing"]["event_layer"]["event_id"] == "evt_known"
    assert payload["timing"]["replay_layer"]["source_type"] == "media"


def test_live_dataset_backfill_keeps_events_inside_requested_window() -> None:
    now = datetime(2026, 6, 26, tzinfo=UTC)
    registry = FakeCollectorRegistry(now - timedelta(days=1))

    result = fetch_live_dataset(
        ticker="MU",
        days=7,
        source_type=SourceType.MEDIA,
        source_ids=["finnhub_company_news"],
        collectors=registry,
        now=now,
    )

    assert len(result.dataset.events) == 1
    assert result.dataset.events[0].payload["provider_message_id"] == "provider-mu-older"
    assert result.ingest_results[0].historical_skipped_count == 0


def test_replayer_flushes_social_batches_before_later_media_events_in_order() -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    service = FakeReplayService()
    consumed: list[str] = []
    replayer = RuntimeDatasetReplayer(service, mark_consumed=consumed.append)
    events = [
        _event(
            event_id="evt_media",
            stream_offset=3,
            source_type=SourceType.MEDIA,
            published_at=base + timedelta(minutes=3),
        ),
        _event(
            event_id="evt_social_2",
            stream_offset=2,
            source_type=SourceType.SOCIAL,
            published_at=base + timedelta(minutes=2),
            batch_window_id="window-a",
        ),
        _event(
            event_id="evt_social_1",
            stream_offset=1,
            source_type=SourceType.SOCIAL,
            published_at=base + timedelta(minutes=1),
            batch_window_id="window-a",
        ),
        _event(
            event_id="evt_social_b",
            stream_offset=4,
            source_type=SourceType.SOCIAL,
            published_at=base + timedelta(minutes=4),
            batch_window_id="window-b",
        ),
    ]

    summary = replayer.replay(events)

    assert service.calls == [
        ("social", "window-a", ["std_evt_social_1", "std_evt_social_2"]),
        ("media", "", ["evt_media"]),
        ("social", "window-b", ["std_evt_social_b"]),
    ]
    assert consumed == ["evt_social_1", "evt_social_2", "evt_media", "evt_social_b"]
    assert summary.media_events == 1
    assert summary.social_events == 3
    assert summary.social_batches == 2
    assert summary.records == 4


def test_replayer_can_isolate_media_only_without_social_flushes() -> None:
    base = datetime(2026, 6, 20, tzinfo=UTC)
    service = FakeReplayService()
    replayer = RuntimeDatasetReplayer(service)

    summary = replayer.replay(
        [
            _event(
                event_id="evt_social",
                stream_offset=1,
                source_type=SourceType.SOCIAL,
                published_at=base,
                batch_window_id="window-a",
            ),
            _event(
                event_id="evt_media",
                stream_offset=2,
                source_type=SourceType.MEDIA,
                published_at=base + timedelta(minutes=1),
            ),
        ],
        source_type=SourceType.MEDIA,
    )

    assert service.calls == [("media", "", ["evt_media"])]
    assert summary.media_events == 1
    assert summary.social_events == 0
    assert summary.social_batches == 0


def test_runtime_context_loader_reports_missing_document3_export_and_keeps_doc1_doc2(
    tmp_path: Path,
) -> None:
    source_export = {
        "export_metadata": {"run_id": "run_source"},
        "stable_documents": {
            "global_research": {
                "doc_global": {"document": {"ticker": "MU", "document_id": "doc_global"}}
            },
            "expectation_unit": {
                "expectation_1": {
                    "document": {
                        "ticker": "MU",
                        "expectation_id": "expectation_1",
                    }
                }
            },
        },
    }
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source_export), encoding="utf-8")

    bundle = load_runtime_context_from_exports(
        source_run_export=source_path,
        document3_export=tmp_path / "missing-document3.json",
    )

    assert bundle.source_run_id == "run_source"
    assert bundle.context["global_research"]["document_id"] == "doc_global"
    assert len(bundle.context["expectation_units"]) == 1
    assert bundle.missing == ["known_events", "monitoring_config", "monitoring_policy"]
    assert bundle.diagnostics == [
        f"document3 export not found or empty: {tmp_path / 'missing-document3.json'}",
        "no direct_trade or push_to_agent monitoring policies were extracted.",
    ]


def test_runtime_context_loader_extracts_document3_and_excludes_legacy_cache_rules(
    tmp_path: Path,
) -> None:
    source_export = {
        "export_metadata": {"run_id": "run_source"},
        "stable_documents": {
            "global_research": {
                "doc_global": {"document": {"ticker": "MU", "document_id": "doc_global"}}
            },
            "expectation_unit": {
                "expectation_1": {
                    "document": {
                        "ticker": "MU",
                        "expectation_id": "expectation_1",
                    }
                }
            },
        },
    }
    document3_export = {
        "export_metadata": {"run_id": "run_doc3"},
        "stable_documents": {
            "known_events": {
                "doc_ke": {
                    "document": {
                        "ticker": "MU",
                        "events": [{"event_id": "KE_1", "core_fact": "MU event"}],
                    }
                }
            },
            "monitoring_config": {
                "doc_config": {"document": {"ticker": "MU", "monitoring_items": []}}
            },
            "monitoring_policy": {
                "doc_policy": {
                    "document": {
                        "ticker": "MU",
                        "direct_trade_rules": [{"rule_id": "DTC_1"}],
                        "push_to_agent_rules": [{"rule_id": "EBA_1"}],
                        "cache_rules": [{"rule_id": "CACHE_OLD"}],
                    }
                }
            },
        },
    }
    source_path = tmp_path / "source.json"
    document3_path = tmp_path / "doc3.json"
    source_path.write_text(json.dumps(source_export), encoding="utf-8")
    document3_path.write_text(json.dumps(document3_export), encoding="utf-8")

    bundle = load_runtime_context_from_exports(
        source_run_export=source_path,
        document3_export=document3_path,
    )

    assert bundle.complete_for_runtime is True
    assert bundle.document3_run_id == "run_doc3"
    assert bundle.context["known_events"] == [{"event_id": "KE_1", "core_fact": "MU event"}]
    assert bundle.context["monitoring_policies"] == [
        {"rule_id": "DTC_1", "policy_type": "direct_trade"},
        {"rule_id": "EBA_1", "policy_type": "push_to_agent"},
    ]
    assert bundle.diagnostics == [
        "monitoring_policy contains legacy cache_rules; runtime context excludes them "
        "from W2 monitoring_policies."
    ]


def test_compact_runtime_context_keeps_bounded_o3_surface() -> None:
    context = {
        "document_source_run_id": "run_source",
        "document3_run_id": "run_doc3",
        "global_research": {"ticker": "MU", "long": "x" * 20_000},
        "expectation_units": [
            {
                "expectation_id": "E1",
                "expectation_name": "Memory cycle",
                "market_view": "y" * 2_000,
                "realized_facts_summary": "z" * 2_000,
                "event_monitoring_direction": "watch pricing",
            }
        ],
        "known_events_document": {"ticker": "MU"},
        "known_events": [
            {
                "event_id": "KE1",
                "core_fact": "a" * 2_000,
                "duplicate_detection_keys": [str(index) for index in range(20)],
            }
        ],
        "monitoring_policies": [
            {
                "policy_id": "P1",
                "policy_type": "direct_trade",
                "trigger_condition": "b" * 2_000,
                "reasoning": "c" * 2_000,
            },
            {"policy_id": "OLD", "policy_type": "cache"},
        ],
    }

    compact = compact_runtime_context(context)

    assert compact["ticker"] == "MU"
    assert "global_research" not in compact
    assert compact["known_events"][0]["core_fact"].endswith("...")
    assert len(compact["known_events"][0]["duplicate_detection_keys"]) == 12
    assert compact["monitoring_policies"] == [
        {
            "policy_id": "P1",
            "policy_type": "direct_trade",
            "scope": None,
            "trigger": None,
            "trigger_condition": "b" * 497 + "...",
            "confirmation": None,
            "action": None,
            "risk_guard": None,
            "reasoning": "c" * 497 + "...",
        }
    ]
    assert len(json.dumps(compact)) < 5_000
