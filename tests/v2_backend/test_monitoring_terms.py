import copy
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.api_v2.auth import Principal
from doxagent.api_v2.errors import ApiFailure
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.bindings import Bindings
from doxagent.v2_control.monitoring_terms import MonitoringTermsControl
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.repository import ReadStore
from tests.test_message_bus_monitoring_terms import _terms


def test_monitoring_terms_atomic_cas_retry_and_preview(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    control = MonitoringTermsControl(repo.path)
    empty = control.get_terms("MU")
    assert empty["revision"] == 0 and empty["configuration"] is None
    config = _terms().model_dump(mode="json")
    for key in ("ticker", "expected_revision"):
        config.pop(key)
    check = control.validate_config("MU", config)
    assert check["valid"] is True and check["search_previews"] == []
    first = control.put_terms("MU", config, "tester", "first-key", empty["control_etag"])
    assert first["revision"] == 1
    assert control.put_terms("MU", config, "tester", "first-key", empty["control_etag"]) == first
    with pytest.raises(ApiFailure) as conflict:
        control.put_terms(
            "MU",
            {**config, "definition": {"relevant": "changed", "irrelevant": "other"}},
            "tester",
            "first-key",
            empty["control_etag"],
        )
    assert conflict.value.status == 409
    with pytest.raises(ApiFailure) as stale:
        control.put_terms("MU", config, "tester", "second-key", empty["control_etag"])
    assert stale.value.status == 412
    assert control.get_terms("MU")["revision"] == 1

    invalid = copy.deepcopy(config)
    invalid["l2"]["en"]["groups"][0]["any"] = [{"regex": "["}]
    result = control.validate_config("MU", invalid)
    assert not result["valid"] and "/l2/en/groups/0/any/0/regex" in result["issues"][0]["path"]
    with pytest.raises(ApiFailure) as bad:
        control.put_terms("MU", invalid, "tester", "invalid-key", first["control_etag"])
    assert bad.value.status == 422 and bad.value.fields
    assert control.get_terms("MU")["revision"] == 1

    updated = copy.deepcopy(config)
    updated["definition"]["relevant"] = "new rationale"
    second = control.put_terms("MU", updated, "tester", "second-valid-key", first["control_etag"])
    assert second["revision"] == 2
    assert second["configuration"]["l1_concepts"] == first["configuration"]["l1_concepts"]
    assert repo.get_source("reuters_site_search") is not None


def test_monitoring_terms_http_contract(tmp_path, monkeypatch):
    class Auth:
        async def authenticate(self, token):
            if token != "test":
                raise ApiFailure("UNAUTHORIZED", 401)
            return Principal("tester", "DEVELOPER", time.time() + 3600)

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    MessageBusV2Service(repo).bootstrap()
    monkeypatch.setenv("DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH", str(repo.path))
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=Auth())
    app.state.control = SimpleNamespace(get=lambda ticker: {} if ticker == "MU" else None)
    path = PREFIX + "/tickers/MU/message-bus/monitoring-terms"
    config = _terms().model_dump(mode="json", exclude={"ticker", "expected_revision"})
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        headers = {"Authorization": "Bearer test"}
        initial = client.get(path, headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["data"]["data"]["revision"] == 0
        check = client.post(path + "/validate", json={"configuration": config}, headers=headers)
        assert check.status_code == 200 and check.json()["data"]["valid"]
        write_headers = {
            **headers,
            "If-Match": initial.headers["etag"],
            "Idempotency-Key": "create-terms",
        }
        first = client.put(path, json={"configuration": config}, headers=write_headers)
        assert first.status_code == 200, first.text
        assert first.json()["data"]["revision"] == 1
        again = client.put(path, json={"configuration": config}, headers=write_headers)
        assert again.status_code == 200 and again.json()["data"] == first.json()["data"]
        assert client.get(path, headers=headers).json()["data"]["data"]["revision"] == 1
        assert (
            client.put(
                path,
                json={"configuration": config},
                headers={**write_headers, "Idempotency-Key": "stale-key"},
            ).status_code
            == 412
        )


def test_google_legacy_terms_become_read_only_after_unified_apply(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("MU")
    SourceOutbox(repo.path, "bus").migrate()
    terms = MonitoringTermsControl(repo.path)
    empty = terms.get_terms("MU")
    config = _terms().model_dump(mode="json", exclude={"ticker", "expected_revision"})
    terms.put_terms("MU", config, "tester", "unified-google", empty["control_etag"])
    binding = bus.configure_binding(
        ticker="MU",
        source_id="google_news_search_rss",
        source_parameters={"search_terms": ["old query"]},
        actor="user",
    )
    adapter = Bindings(repo.path)
    current = adapter.get("MU", binding.binding_id)
    assert current["monitoring_terms_usage"]["managed_parameter_paths"] == [
        "/source_parameters/search_terms"
    ]
    with pytest.raises(ApiFailure) as error:
        adapter.mutate(
            "MU",
            binding.binding_id,
            "PATCH",
            {"source_parameters": {"search_terms": ["ignored edit"]}},
            "tester",
            "google-edit-key",
            current["control_etag"],
        )
    assert error.value.code == "PARAMETER_NOT_WRITABLE"
    updated = adapter.mutate(
        "MU",
        binding.binding_id,
        "PATCH",
        {"polling": {"target_interval_seconds": 61}},
        "tester",
        "google-interval-key",
        current["control_etag"],
    )
    assert updated["effective"]["polling"]["target_interval_seconds"] == 61
    reuters = adapter.get("MU", "MU:reuters_site_search")
    assert reuters["monitoring_terms_usage"]["managed_parameter_paths"] == [
        "/source_parameters/company_short_name"
    ]
    with pytest.raises(ApiFailure) as ignored:
        adapter.mutate(
            "MU", "MU:reuters_site_search", "PATCH",
            {"source_parameters": {"company_short_name": "ignored"}},
            "tester", "reuters-ignored", reuters["control_etag"],
        )
    assert ignored.value.code == "PARAMETER_NOT_WRITABLE"
    preview = terms.validate_config("MU", config)["search_previews"]
    google = next(item for item in preview if item["source_id"] == "google_news_search_rss")
    assert (
        google["queries"]
        and google["queries"][0]["query_key"]
        != next(
            item
            for item in terms.get_terms("MU")["consumers"]
            if item["source_id"] == "google_news_search_rss"
        )["queries"][0]["query_key"]
    )


def test_terms_and_idempotency_receipt_roll_back_together(tmp_path, monkeypatch):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    MessageBusV2Service(repo).bootstrap()
    control = MonitoringTermsControl(repo.path)
    config = _terms().model_dump(mode="json", exclude={"ticker", "expected_revision"})
    original = MonitoringTermsService.apply

    def interrupted(self, value, *, actor):
        original(self, value, actor=actor)
        raise RuntimeError("failure before command receipt")

    with monkeypatch.context() as patch:
        patch.setattr(MonitoringTermsService, "apply", interrupted)
        with pytest.raises(RuntimeError):
            control.put_terms(
                "MU", config, "tester", "retry-after-crash", control.get_terms("MU")["control_etag"]
            )
    assert control.get_terms("MU")["revision"] == 0
    assert (
        control.put_terms(
            "MU", config, "tester", "retry-after-crash", control.get_terms("MU")["control_etag"]
        )["revision"]
        == 1
    )
