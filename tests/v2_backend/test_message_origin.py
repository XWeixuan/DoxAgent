import asyncio

from doxagent.v2_control.mirror import apply
from tests.test_message_bus_v2 import _bus, _input


def test_ingestion_origin_is_immutable_across_delayed_duplicates(tmp_path):
    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    source = repo.get_source("benzinga_news")
    binding = repo.get_binding("MU:benzinga_news")

    def accept(message):
        return asyncio.run(
            bus.accept_message(source=source, binding=binding, message=message, bootstrap=False)
        )

    old = accept(_input("legacy"))
    assert "v2_control_origin" not in repo.get_raw(old.raw_message_id).metadata
    with repo.transaction() as db:
        apply(
            db,
            {
                "ticker": "MU",
                "epoch": 3,
                "revision": 1,
                "mode": "MESSAGE_MONITORING",
                "analysis_allowed": True,
            },
        )
    duplicate = accept(_input("legacy"))
    assert duplicate.raw_message_id == old.raw_message_id
    assert "v2_control_origin" not in repo.get_raw(old.raw_message_id).metadata
    message = _input("managed")
    message.metadata["v2_control_origin"] = {"epoch": 999}
    result = accept(message)
    origin = repo.get_raw(result.raw_message_id).metadata["v2_control_origin"]
    assert origin["epoch"] == 3
    assert repo.get_standard(result.standard_message_id).metadata["v2_control_origin"] == origin


def test_nested_secrets_and_optional_parameters_are_not_exposed():
    import pytest

    from doxagent.api_v2.errors import ApiFailure
    from doxagent.v2_control.bindings import merge_parameters, public_parameters

    value = {
        "clients": [{"api_key": "private"}],
        "url": "https://user:password@example.test/data",
        "q": "news",
    }
    schema = {"properties": {"optional": {"type": "string"}}}
    public, redacted, writable = public_parameters(value, schema)
    assert public == {"q": "news"}
    assert set(redacted) == {"/source_parameters/clients", "/source_parameters/url"}
    assert "/source_parameters/optional" in writable
    assert merge_parameters(value, {"q": "changed"}, schema) == {**value, "q": "changed"}
    with pytest.raises(ApiFailure):
        merge_parameters(value, {"clients": []}, schema)
