from __future__ import annotations

from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.service import MonitoringBusService
from doxagent.monitoring.viewer import MonitoringViewerRuntime, build_remote_monitoring_command
from doxagent.settings import DoxAgentSettings


def _settings(**overrides: object) -> DoxAgentSettings:
    defaults: dict[str, object] = {
        "monitoring_storage_mode": "memory",
        "monitoring_remote_ssh_alias": "doxagent-hk",
        "monitoring_remote_path": "/root/doxagent",
        "monitoring_remote_timeout_seconds": 5,
    }
    defaults.update(overrides)
    return DoxAgentSettings(**defaults)


def test_remote_monitoring_command_quotes_path_and_args() -> None:
    command = build_remote_monitoring_command(
        _settings(monitoring_remote_path="/root/dox agent"),
        ["bind", "AAPL", "--source", "newswire_rss", "--rss-url", "https://example.test/a b"],
    )

    assert "cd '/root/dox agent'" in command
    assert "'https://example.test/a b'" in command
    assert "docker compose exec -T debug-viewer" in command
    assert "python -m doxagent.monitoring.cli" in command


def test_local_viewer_status_includes_meta_and_snapshot() -> None:
    runtime = MonitoringViewerRuntime(_settings())
    payload = runtime.local_status(limit=5)

    assert payload["scope"] == "local"
    assert payload["ok"] is True
    assert payload["meta"]["remote_alias"] == "doxagent-hk"
    assert payload["sources"]
    assert payload["recent_events"] == []


def test_local_viewer_bind_updates_user_configuration() -> None:
    runtime = MonitoringViewerRuntime(_settings())
    result = runtime.bind(
        {
            "ticker": "AAPL",
            "source_id": "tikhub_x_search",
            "keywords": "AAPL earnings, Apple AI",
            "search_terms": "Apple event",
            "enabled": True,
            "replace": True,
            "poll_interval_seconds": 900,
        },
        scope="local",
    )
    config = runtime.service.get_ticker_config("AAPL")
    source = runtime.service.repository.get_source("tikhub_x_search")

    assert result["ok"] is True
    assert source is not None
    assert source.poll_interval_seconds == 900
    by_parameter = config["by_parameter_sources"]
    assert by_parameter[0]["binding"]["parameters"]["keywords"] == [
        "AAPL earnings",
        "Apple AI",
    ]


def test_local_viewer_unbind_removes_user_configuration() -> None:
    runtime = MonitoringViewerRuntime(_settings())
    runtime.bind(
        {
            "ticker": "AAPL",
            "source_id": "benzinga_news",
            "enabled": True,
        },
        scope="local",
    )

    result = runtime.unbind(
        {
            "ticker": "AAPL",
            "source_id": "benzinga_news",
        },
        scope="local",
    )

    assert result["ok"] is True
    assert result["removed"] is True
    assert runtime.service.repository.get_binding("AAPL", "benzinga_news") is None


def test_local_viewer_delete_ticker_removes_all_user_configuration() -> None:
    runtime = MonitoringViewerRuntime(_settings())
    for source_id in ["benzinga_news", "finnhub_company_news"]:
        runtime.bind(
            {
                "ticker": "AAPL",
                "source_id": source_id,
                "enabled": True,
            },
            scope="local",
        )

    result = runtime.delete_ticker({"ticker": "AAPL"}, scope="local")

    assert result["ok"] is True
    assert result["deleted_count"] == 2
    assert runtime.service.repository.list_bindings(ticker="AAPL") == []


def test_local_poll_due_returns_result_payload() -> None:
    service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=None)
    runtime = MonitoringViewerRuntime(_settings())
    runtime.service = service

    payload = runtime.poll_due(scope="local")

    assert payload["ok"] is True
    assert payload["results"] == []
