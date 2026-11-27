from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from doxagent.message_bus_v2.extra_relevance import extra_allows
from doxagent.message_bus_v2.live import LiveDeliveryWorker
from doxagent.message_bus_v2.schema import AcquisitionMode, TickerSourceBinding
from tests.test_cross_source_live_dedup import pipeline  # noqa: F401

RULES = {
    "groups": [
        {
            "id": "exclude",
            "all": [{"regex": "."}],
            "none": [{"regex": "gaming chair", "field": "title"}],
        },
        {
            "id": "business_rescue",
            "any": [{"literal": "DRAM", "field": "body", "whole_word": True}],
        },
    ]
}


def binding(rules=RULES):
    return TickerSourceBinding(
        binding_id="MU:shared", ticker="MU", source_id="shared", extra_relevance=rules
    )


def message(title, body=""):
    return SimpleNamespace(title=title, summary="", body=body)


def test_optional_filter_exclusion_rescue_and_isolation():
    assert extra_allows(binding(None), message("gaming chair"))
    assert not extra_allows(binding(), message("gaming chair"))
    assert extra_allows(binding(), message("gaming chair", "DRAM prices rise"))
    assert extra_allows(binding(), message("Memory maker plans expansion"))
    assert extra_allows(binding(None), message(""))
    assert binding().extra_relevance["groups"][0]["none"][0]["field"] == "title"


def test_persisted_extension_is_readable_by_old_strict_binding_model(tmp_path):
    import json
    from pydantic import ConfigDict, create_model
    from tests.fixtures.message_bus_v2 import _bus

    repo, _ = _bus(tmp_path / "bus.db")
    original = binding().model_copy(update={"source_parameters": {"feed_proxy_url": "http://feed"}})
    raw = json.loads(repo._json(original))
    assert "extra_relevance" not in raw
    previous = create_model(
        "PreviousBinding", __config__=ConfigDict(extra="forbid"),
        **{name: (field.annotation, ...) for name, field in TickerSourceBinding.model_fields.items()
           if name != "extra_relevance"},
    )
    assert previous.model_validate(raw).source_parameters["_binding_extra_relevance_v1"]
    current = TickerSourceBinding.model_validate(raw)
    assert current.extra_relevance == original.extra_relevance
    assert current.source_parameters == original.source_parameters
    assert "_binding_extra_relevance_v1" not in current.model_dump()["source_parameters"]
    assert "extra_relevance" not in current.model_dump(mode="json")
    assert current.model_dump()["extra_relevance"] == current.extra_relevance
    assert not extra_allows(current, message("gaming chair"))
    assert "_binding_extra_relevance_v1" not in json.loads(repo._json(binding(None)))["source_parameters"]


def test_reject_conflicting_persisted_and_public_extra_rules():
    with pytest.raises(ValidationError, match="conflicting"):
        TickerSourceBinding.model_validate({**binding().model_dump(),
            "source_parameters": {"_binding_extra_relevance_v1": None}})


def test_source_tails_are_only_trimmed_for_optional_gate_without_mutating_input():
    rules = {"groups":[{"id":"core", "any":[{"regex":"DRAM", "field":"body"}]}]}
    configured = binding(rules).model_copy(update={"source_id":"technews_hardware"})
    raw = message("Application news", "Main software story. 延伸閱讀： DRAM prices rise")
    assert not extra_allows(configured, raw)
    assert raw.body.endswith("DRAM prices rise")
    assert extra_allows(configured.model_copy(update={"extra_relevance":None}), raw)
    assert extra_allows(configured, message("Industry news", "DRAM demand grows. 延伸閱讀： Other news"))


@pytest.mark.parametrize(
    "rules",
    [
        {},
        {"groups": []},
        {"groups": [{"id": "bad", "none": [{"literal": "x"}]}]},
        {"groups": [{"id": "bad", "any": [{"regex": "("}]}]},
    ],
)
def test_reject_invalid_rules(rules):
    with pytest.raises((ValidationError, ValueError)):
        binding(rules)


async def test_live_last_gate_blocks_without_publication_or_model_call():
    worker = object.__new__(LiveDeliveryWorker)
    worker._decision = AsyncMock()
    worker.bus = SimpleNamespace(storage=SimpleNamespace(prepare=AsyncMock()))
    await worker.publish(
        {"ticker": "MU"},
        SimpleNamespace(acquisition_mode=AcquisitionMode.BY_DISTRIBUTION),
        binding(),
        message("gaming chair"),
    )
    worker.bus.storage.prepare.assert_not_awaited()
    assert worker._decision.await_args.args[2] == "IRRELEVANT"
    assert worker._decision.await_args.kwargs["error"] == "EXTRA_REGEX_FILTERED"


def test_regex_timeout_never_turns_negative_clause_into_pass(monkeypatch):
    import doxagent.message_bus_v2.relevance as matcher

    monkeypatch.setattr(
        matcher.regex, "search", lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
    )
    with pytest.raises(TimeoutError):
        extra_allows(binding(), message("gaming chair"))


def test_existing_configure_preserves_filter_and_explicit_null_clears(tmp_path):
    from tests.fixtures.message_bus_v2 import _bus

    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    repo.save_source(
        repo.get_source("benzinga_news").model_copy(
            update={"acquisition_mode": AcquisitionMode.BY_DISTRIBUTION}
        )
    )
    b = bus.update_binding("MU:benzinga_news", {"extra_relevance": RULES}, actor="user")
    same = bus.configure_binding(ticker="MU", source_id="benzinga_news", actor="user")
    assert same.extra_relevance == b.extra_relevance
    assert (
        bus.update_binding(b.binding_id, {"extra_relevance": None}, actor="user").extra_relevance
        is None
    )


def test_extra_filter_cannot_be_enabled_for_other_acquisition_modes(tmp_path):
    from tests.fixtures.message_bus_v2 import _bus

    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    for mode in [
        AcquisitionMode.BY_SEARCH,
        AcquisitionMode.BY_TICKER,
        AcquisitionMode.MASSIVE_DISTRIBUTION,
    ]:
        repo.save_source(
            repo.get_source("benzinga_news").model_copy(update={"acquisition_mode": mode})
        )
        with pytest.raises(ValueError, match="requires by_distribution"):
            bus.update_binding("MU:benzinga_news", {"extra_relevance": RULES}, actor="user")


def test_binding_control_cas_roundtrip_invalid_edit_and_remove(tmp_path):
    from doxagent.api_v2.errors import ApiFailure
    from doxagent.v2_control.bindings import Bindings
    from tests.fixtures.message_bus_v2 import _bus

    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    repo.save_source(
        repo.get_source("benzinga_news").model_copy(
            update={"acquisition_mode": AcquisitionMode.BY_DISTRIBUTION}
        )
    )
    from doxagent.v2_control.bindings import migrate

    with repo.transaction() as db:
        migrate(db)
    control = Bindings(repo.path)
    old = control.get("MU", "MU:benzinga_news")
    saved = control.mutate(
        "MU",
        "MU:benzinga_news",
        "PATCH",
        {"extra_relevance": RULES},
        "tester",
        "extra-add-001",
        old["control_etag"],
    )
    assert saved["effective"]["extra_relevance"] == binding().extra_relevance
    with pytest.raises(ApiFailure):
        control.mutate(
            "MU",
            "MU:benzinga_news",
            "PATCH",
            {"extra_relevance": None},
            "tester",
            "extra-stale-001",
            old["control_etag"],
        )
    with pytest.raises((ApiFailure, ValueError, ValidationError)):
        control.mutate(
            "MU",
            "MU:benzinga_news",
            "PATCH",
            {"extra_relevance": {"groups": []}},
            "tester",
            "extra-invalid-001",
            saved["control_etag"],
        )
    assert (
        control.get("MU", "MU:benzinga_news")["effective"]["extra_relevance"]
        == binding().extra_relevance
    )
    removed = control.mutate(
        "MU",
        "MU:benzinga_news",
        "PATCH",
        {"extra_relevance": None},
        "tester",
        "extra-remove-001",
        saved["control_etag"],
    )
    assert removed["effective"]["extra_relevance"] is None


@pytest.mark.parametrize(
    "rules,title,body", [(None, "gaming chair", ""), (RULES, "gaming chair", "DRAM price increase")]
)
async def test_live_allowed_uses_original_publication_path(rules, title, body):
    worker = object.__new__(LiveDeliveryWorker)
    worker._decision = AsyncMock()
    worker.repository = SimpleNamespace(prepare_publication=object(), publish=object())
    storage = SimpleNamespace(
        prepare=AsyncMock(return_value={"original": "publication"}),
        call=AsyncMock(return_value=True),
    )
    worker.bus = SimpleNamespace(storage=storage)
    await worker.publish(
        {},
        SimpleNamespace(acquisition_mode=AcquisitionMode.BY_DISTRIBUTION),
        binding(rules),
        message(title, body),
    )
    storage.prepare.assert_awaited_once()
    storage.call.assert_awaited_once()
    worker._decision.assert_not_awaited()


@pytest.mark.parametrize("regex,score", [(True, None), (False, 0.9)])
async def test_legacy_regex_and_model_positive_both_obey_second_gate(regex, score):
    from doxagent.message_bus_v2.distribution import DistributionWorker

    worker = object.__new__(DistributionWorker)
    worker._read = AsyncMock(return_value=binding())
    worker.repository = SimpleNamespace(finish_delivery=object())
    worker.bus = SimpleNamespace(
        repository=SimpleNamespace(get_binding=object()), storage=SimpleNamespace(call=AsyncMock())
    )
    await worker._deliver(
        {"ticker": "MU", "delivery_id": "d", "claim_token": "c"},
        SimpleNamespace(source_id="shared", acquisition_mode=AcquisitionMode.BY_DISTRIBUTION),
        message("gaming chair"),
        regex_hit=regex,
        jev_result=score,
        jev_attempts=0,
        error_code=None,
    )
    assert worker.bus.storage.call.await_args.args[3] == "NOT_RELEVANT"


@pytest.mark.parametrize("filtered", [False, True])
@pytest.mark.parametrize("route", ["regex", "model"])
async def test_real_live_publication_and_first_layer_model_is_unchanged(pipeline, filtered, route):  # noqa: F811
    import asyncio

    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
    from doxagent.message_bus_v2.schema import DistributionPolicy, UpdateActor
    from tests.test_cross_source_live_dedup import capture, input_message, publications
    from tests.test_message_bus_distribution import _terms

    repo, bus, extractor = pipeline
    source = repo.get_source("finnhub_company_news")
    repo.save_source(
        source.model_copy(
            update={
                "acquisition_mode": AcquisitionMode.BY_DISTRIBUTION,
                "distribution_policy": DistributionPolicy(jev_enabled=True),
            }
        )
    )
    if filtered:
        bus.update_binding(
            "MU:finnhub_company_news", {"extra_relevance": RULES}, actor=UpdateActor.USER
        )
    MonitoringTermsService(repo).apply(
        _terms("MU", "Micron" if route == "regex" else "not-present"), actor="test"
    )
    extractor.content = "Gaming chair specification and upholstery. " * 120
    capture(
        pipeline,
        source.source_id,
        input_message(980, "https://publisher.test/extra-gate", title="Micron gaming chair"),
    )
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    try:
        await hub.run_once()
        worker = LiveDeliveryWorker(bus)

        async def classify(_message, definitions, *, on_positive):
            assert set(definitions) == {"MU"}
            await on_positive("MU", 0.9, 1)
            return {"MU": 0.9}, {"MU": 1}, {}

        worker.classifier._jev = AsyncMock(side_effect=classify)
        await worker.run_available()
        if worker.decisions:
            await asyncio.gather(*worker.decisions)
        assert len(publications(pipeline)) == (0 if filtered else 1)
        if filtered:
            row = bus.live_queue.repository.deliveries("IRRELEVANT")[0]
            assert row["last_error"] == "EXTRA_REGEX_FILTERED"
            assert row["regex_result"] == (1 if route == "regex" else 0)
            if route == "model":
                assert row["jev_result"] == 0.9
        assert worker.classifier._jev.await_count == (1 if route == "model" else 0)
    finally:
        await hub.close()


async def test_concurrent_extra_filter_edit_is_fenced_before_native_commit(pipeline):  # noqa: F811
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
    from tests.test_cross_source_live_dedup import capture, input_message, publications
    from tests.test_message_bus_distribution import _terms

    repo, bus, extractor = pipeline
    source = repo.get_source("finnhub_company_news").model_copy(
        update={"acquisition_mode": AcquisitionMode.BY_DISTRIBUTION}
    )
    repo.save_source(source)
    MonitoringTermsService(repo).apply(_terms("MU", "Micron"), actor="test")
    capture(
        pipeline,
        source.source_id,
        input_message(981, "https://publisher.test/config-race", title="Micron gaming chair"),
    )
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    try:
        await hub.run_once()
        worker = LiveDeliveryWorker(bus)
        live = bus.live_queue.repository
        row = live.deliveries()[0]
        msg, source, old_binding, _ = worker._prepare(row)
        row["_extra_relevance_checked"] = None
        row["_positive_regex"] = True
        prepared = live.prepare_publication(bus, row, source, old_binding, msg)
        bus.update_binding(old_binding.binding_id, {"extra_relevance": RULES}, actor="user")
        with pytest.raises(RuntimeError, match="live_publication_extra_relevance_changed"):
            live.publish(prepared)
        assert not publications(pipeline)
    finally:
        await hub.close()
