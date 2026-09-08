import asyncio

import pytest

from doxagent.api_v2.errors import ApiFailure
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.v2_control.bindings import Bindings
from doxagent.v2_read.outbox import SourceOutbox
from tests.test_message_bus_v2 import _bus, _input


def test_binding_cas_secret_preservation_and_atomic_buffer_delete(tmp_path, monkeypatch):
    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    source = repo.get_source("benzinga_news")
    source.parameter_schema["properties"]["api_key"] = {"type": "string", "writeOnly": True}
    repo.save_source(source)
    binding = bus.update_binding(
        "MU:benzinga_news",
        {
            "source_parameters": {"api_key": "fixture-secret", "search_terms": ["MU"]},
            "streaming": {"publication_mode": "buffered", "buffer": {"max_items": 10}},
        },
        actor="user",
    )
    SourceOutbox(repo.path, "bus").migrate()
    adapter = Bindings(repo.path)
    initial = adapter.get("MU", binding.binding_id)
    assert "fixture-secret" not in str(initial)
    assert "/source_parameters/api_key" in initial["redacted_parameter_paths"]
    changed = adapter.mutate(
        "MU",
        binding.binding_id,
        "PATCH",
        {"source_parameters": {}, "polling": {"target_interval_seconds": 90}},
        "alice",
        "patch-key-01",
        initial["control_etag"],
    )
    assert repo.get_binding(binding.binding_id).source_parameters == {"api_key": "fixture-secret"}
    assert changed["effective"]["polling"]["target_interval_seconds"] == 90
    assert (
        adapter.mutate(
            "MU",
            binding.binding_id,
            "PATCH",
            {"source_parameters": {}, "polling": {"target_interval_seconds": 90}},
            "alice",
            "patch-key-01",
            initial["control_etag"],
        )
        == changed
    )
    with pytest.raises(ApiFailure) as error:
        adapter.mutate(
            "MU",
            binding.binding_id,
            "PATCH",
            {"enabled": False},
            "alice",
            "patch-key-02",
            initial["control_etag"],
        )
    assert error.value.status == 412
    source.version += 1
    repo.save_source(source)
    with pytest.raises(ApiFailure) as error:
        adapter.mutate(
            "MU",
            binding.binding_id,
            "PATCH",
            {"enabled": False},
            "alice",
            "patch-key-03",
            changed["control_etag"],
        )
    assert error.value.status == 412
    current = repo.get_binding(binding.binding_id)
    asyncio.run(
        bus.accept_message(
            source=source, binding=current, message=_input("buffered"), bootstrap=False
        )
    )
    assert len(repo.list_buffer(binding.binding_id)) == 1
    etag = adapter.get("MU", binding.binding_id)["control_etag"]

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected crash before receipt commit")

    with monkeypatch.context() as context:
        context.setattr(MessageBusV2Service, "_audit", fail_audit)
        with pytest.raises(RuntimeError):
            adapter.mutate("MU", binding.binding_id, "DELETE", {}, "alice", "delete-key-1", etag)
    assert len(repo.list_buffer(binding.binding_id)) == 1
    assert repo.latest_stream_offset("MU") == 0
    assert repo.get_binding(binding.binding_id) is not None
    deleted = adapter.mutate("MU", binding.binding_id, "DELETE", {}, "alice", "delete-key-1", etag)
    assert deleted["removed"] is True
    assert repo.get_binding(binding.binding_id) is None
    assert repo.list_buffer(binding.binding_id) == []
    assert repo.latest_stream_offset("MU") == 1
    assert (
        adapter.mutate("MU", binding.binding_id, "DELETE", {}, "alice", "delete-key-1", etag)
        == deleted
    )
