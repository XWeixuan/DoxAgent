import json
from decimal import Decimal, localcontext

import pytest
from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.api_v2.cost import Costs
from doxagent.api_v2.cost_report import report
from doxagent.api_v2.dto import validate
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.cost_backfill import reproject
from doxagent.v2_read.pricing import normalize, price
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.usage import observations
from tests.v2_backend.test_api import OfflineAuth


def view(seq, *, complete=True):
    return {
        "seq": seq,
        "as_of": "2026-10-21T06:00:00Z",
        "freshness": "FRESH",
        "wire": {
            "page": "COST",
            "ticker": None,
            "period": {
                "selected": "PREVIOUS_TRADING_DAY",
                "current": {"trading_days": ["2026-10-20"]},
            },
        },
        "capture_proofs": {
            source: {
                "tables_json": json.dumps(tables),
                "complete": complete,
                "started_at": "2026-10-19T00:00:00Z",
                "end_at": "2026-10-22T00:00:00Z",
            }
            for source, tables in (
                ("runtime", ["runtime_v2_turns", "runtime_values"]),
                ("initialization", ["v2_model_invocations", "initialization_nodes"]),
            )
        },
        "cost_source_health": {
            s: {"head": 1, "checkpoint": 1, "error": None} for s in ("runtime", "initialization")
        },
    }


def contributions(identity, ticker, rnd, values, model="qwen3.8-flash", provider="bailian"):
    return [
        {
            "metric": key,
            "ticker": ticker,
            "entity": identity,
            "day": "2026-10-20",
            "dimensions": {
                "scope": "API",
                "node": "W1",
                "round": rnd,
                "model": model,
                "provider": provider,
            },
            "value": value,
        }
        for key, value in values.items()
    ]


def test_report_all_tickers_round_models_filters_and_coverage(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    known = observations(
        "qwen3.8-flash",
        "bailian",
        "API",
        "2026-10-20T06:00:00Z",
        {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10},
    )
    counts = [
        *contributions("a", "REMOVED", "R2", known),
        *contributions("b", "GLOBAL", "R3", known),
        *contributions("c", "MU", "R2", observations("qwen3.8-flash", "bailian", "API", None, {})),
    ]
    seq = store.ingest("test", "a", [], contributions=counts)
    costs = Costs(store, None)
    data = report(costs, view(seq), {"scope": "API"})
    validate("CostReport", data)
    assert data["totals"]["observed_attempts"] == 3
    assert data["totals"]["usage_known_attempts"] == 2
    assert data["totals"]["input_tokens"]["current"]["value"] == "200"
    assert data["totals"]["cache_hit_ratio"]["current"]["value"] == "0.4"
    assert data["totals"]["cache_ratio_known_attempts"] == 2
    assert data["totals"]["coverage"]["state"] == "PARTIAL"
    assert data["filter_choices"]["tickers"] == ["GLOBAL", "MU", "REMOVED"]
    selected = report(
        costs, view(seq), {"scope": "API", "ticker_selector": "REMOVED", "node_id": "W1R2"}
    )
    assert selected["totals"]["observed_attempts"] == 1
    assert selected["totals"]["input_tokens"]["current_coverage"]["state"] == "COMPLETE"
    assert selected["totals"]["coverage"]["state"] == "COMPLETE"
    assert selected["trend"]["points"][0]["total_tokens"]["value"] == "110"
    assert len(selected["breakdown"]["items"]) == len(selected["rows"]) == 1
    incomplete = report(
        costs, view(seq, complete=False), {"scope": "API", "ticker_selector": "REMOVED"}
    )
    assert incomplete["source_coverage"]["state"] == "UNKNOWN"
    assert "SOURCE_GAP" in incomplete["totals"]["coverage"]["reasons"]
    all_view = view(seq)
    all_view["wire"]["period"] = {"selected": "ALL"}
    assert report(costs, all_view, {"scope": "API"})["totals"]["observed_attempts"] == 3


def test_vertex_native_cache_ttls_and_openrouter_no_double_count():
    raw = {
        "input_tokens": 100,
        "cache_read_input_tokens": 8000,
        "cache_creation_input_tokens": 2000,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 2000},
        "output_tokens": 500,
    }
    usage = normalize("vertex", {"provider_usage": raw}, ttl="1h")
    assert usage["input_tokens"] == 10100
    assert usage["input_tokens_uncached"] == 100
    assert price("claude-haiku-5-5", "vertex", None, usage, endpoint="global")[
        "total_cost_usd"
    ] == Decimal(".000740")
    assert price("claude-haiku-5-5", "vertex", None, usage, endpoint="us-east5")[
        "total_cost_usd"
    ] == Decimal(".000814")
    existing = normalize(
        "openrouter",
        {
            "input_tokens": 10100,
            "cached_input_tokens": 8000,
            "cache_write_input_tokens": 2000,
            "output_tokens": 500,
        },
        ttl="1h",
    )
    assert existing["input_tokens"] == usage["input_tokens"]
    assert existing["input_tokens_uncached"] == 100
    reported = price(
        "anthropic/claude-haiku-5.5",
        "openrouter",
        None,
        {
            **existing,
            "provider_usage": {
                "cost": ".0009",
                "cost_details": {"upstream_inference_cost": ".0009"},
            },
        },
    )
    assert reported["total_cost_usd"] == Decimal(".0009")
    assert reported["cost_basis"] == "REPORTED"
    assert reported["input_cost_usd"] is None  # Don't present an estimate as a billed breakdown.
    partial = normalize("vertex", {"provider_usage": {"input_tokens": 100, "output_tokens": 5}})
    assert partial["input_tokens"] is None and partial["output_tokens"] == 5


@pytest.mark.parametrize("incoming,expected", [(99999, ".1"), (100000, ".5"), (100001, ".5")])
def test_vertex_long_context_counts_cached_tokens(incoming, expected):
    usage = normalize(
        "vertex",
        {
            "provider_usage": {
                "input_tokens": 1,
                "cache_read_input_tokens": incoming - 1,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
            }
        },
        ttl="1h",
    )
    assert (
        price("claude-haiku-5-5", "vertex", None, usage, endpoint="global")[
            "noncached_input_cost_usd"
        ]
        == Decimal(expected) / 1000000
    )


def test_glm_exact_provider_region_price_and_partial_output():
    usage = normalize(
        "bailian",
        {"input_tokens": 1000000, "cached_input_tokens": 500000, "output_tokens": 1000000},
    )
    cost = price("ZHIPU/GLM-5.3-FlashX", "bailian", None, usage, region="cn-beijing")
    assert cost["calculated_cost_native"] == Decimal("8.285")
    assert price("ZHIPU/GLM-5.3-FlashX", "other", None, usage)["total_cost_usd"] is None
    assert (
        price("ZHIPU/GLM-5.3-FlashX", "bailian", None, usage, region="us")["total_cost_usd"] is None
    )
    partial = observations(
        "qwen3.8-flash",
        "bailian",
        "API",
        "2026-10-20T06:00:00Z",
        {"input_tokens": None, "output_tokens": 1},
    )
    assert partial["output_cost_usd"] is not None
    assert partial["total_cost_usd"] is None


def test_cost_backfill_replaces_round_and_withdraws_old_price_without_other_changes(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    record = {
        "kind": "usage",
        "ticker": "MU",
        "id": "call",
        "day": "2026-10-20",
        "data": {
            "scope": "API",
            "node": "W1",
            "model": "qwen3.8-flash",
            "provider": "bailian",
            "usage": {"input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 0},
            "timing": {"first_started_at": {"value": "2026-10-20T06:00:00Z"}},
        },
    }
    new, counts = reproject(record)
    new["data"]["round"] = "R2"
    for item in counts:
        item["dimensions"]["round"] = "R2"
    first = store.ingest(
        "runtime",
        "a",
        [record],
        contributions=[
            {
                "metric": "total_cost_usd",
                "ticker": "MU",
                "entity": "call",
                "day": "2026-10-20",
                "value": "1",
            },
            {
                "metric": "unrelated",
                "ticker": "MU",
                "entity": "call",
                "day": "2026-10-20",
                "value": 7,
            },
        ],
    )
    seq = store.ingest(
        "cost_backfill",
        "a",
        [new],
        contributions=counts,
        expected_usage_versions=[("MU", "call", first)],
    )
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM contributions_current "
                "WHERE metric='total_cost_usd' AND dimensions='{}'"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT value FROM contributions_current WHERE metric='unrelated'"
            ).fetchone()[0]
            == "7"
        )
    again = store.ingest(
        "cost_backfill",
        "b",
        [new],
        contributions=counts,
        expected_usage_versions=[("MU", "call", seq)],
    )
    assert again == seq
    assert "round" not in store.get("usage", "MU", "call", first)
    with pytest.raises(ValueError, match="CONCURRENT_UPDATE"):
        store.ingest(
            "cost_backfill", "stale", [record], expected_usage_versions=[("MU", "call", first)]
        )
    unknown = {**new, "data": {**new["data"], "usage": {}}}
    changed, missing = reproject(unknown)
    store.ingest("cost_backfill", "missing", [changed], contributions=missing)
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM contributions_current WHERE metric='total_cost_usd'"
            ).fetchone()[0]
            == 0
        )


def test_global_cost_http_and_scope_binding(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    values = observations(
        "qwen3.8-flash",
        "bailian",
        "API",
        "2026-10-20T06:00:00Z",
        {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
    )
    store.ingest("test", "a", [], contributions=contributions("a", "REMOVED", "R2", values))
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=OfflineAuth())
    client = TestClient(app)
    headers = {"Authorization": "Bearer offline"}
    context = client.get(PREFIX + "/read-context?page=COST&period=ALL", headers=headers)
    assert context.status_code == 200, context.text
    response = client.get(
        PREFIX + "/audit/cost/report",
        params={"view_id": context.json()["data"]["view_id"], "scope": "API"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["data"]["totals"]["observed_attempts"] == 1
    assert (
        client.get(PREFIX + "/read-context?page=RUNTIME&period=ALL", headers=headers).status_code
        == 422
    )


def test_compact_cost_ledger_late_usage_and_stale_receipt_preserve_frozen_totals(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    costs = Costs(store, None)

    def settle(event, position, incoming):
        record = {
            "kind": "usage",
            "ticker": "MU",
            "id": "call",
            "day": "2026-10-20",
            "data": {
                "scope": "API",
                "node": "W1",
                "provider": "bailian",
                "model": "qwen3.8-flash",
                "round": "R2",
                "usage": {"input_tokens": incoming, "cached_input_tokens": 0, "output_tokens": 10},
                "timing": {"first_started_at": {"value": "2026-10-20T06:00:00Z"}},
            },
        }
        record, counts = reproject(record)
        return store.ingest("runtime", event, [record], position=position, contributions=counts)

    first = settle("first", 1, 100)
    second = settle("later", 2, 150)
    assert (
        report(costs, view(first), {"scope": "API"})["totals"]["input_tokens"]["current"]["value"]
        == "100"
    )
    assert (
        report(costs, view(second), {"scope": "API"})["totals"]["input_tokens"]["current"]["value"]
        == "150"
    )
    settle("stale", 1, 1)
    with store.connect() as db:
        seq = store.highwater(db)
        assert (
            db.execute("SELECT count(*) FROM contributions_current WHERE entity='call'").fetchone()[
                0
            ]
            == 0
        )
        assert (
            db.execute("SELECT count(*) FROM object_current WHERE kind='usage'").fetchone()[0] == 1
        )
    assert (
        report(costs, view(seq), {"scope": "API"})["totals"]["input_tokens"]["current"]["value"]
        == "150"
    )


def test_embedding_input_only_usage_is_billable_and_not_a_cache_miss():
    from doxagent.v2_read.usage import accounting

    usage = {
        "input_tokens": 1000000,
        "output_tokens": None,
        "cached_input_tokens": None,
        "total_tokens": 1000000,
    }
    normalized, money = accounting(
        "qwen3.7-text-embedding", "bailian", "API", "2026-10-20T06:00:00Z", usage
    )
    assert normalized["input_tokens"] == normalized["total_tokens"] == 1000000
    assert money["calculated_cost_native"] == Decimal("0.5")
    values = observations("qwen3.7-text-embedding", "bailian", "API", "2026-10-20T06:00:00Z", usage)
    assert values["usage_known_attempts"] == 1 and values["unpriced_requests"] == 0
    assert values["cache_ratio_input_tokens_samples"] == 0
    assert values["cache_inapplicable_attempts"] == 1


def test_backfill_yields_to_sqlite_writer_and_reloads_same_batch(tmp_path, monkeypatch):
    import sqlite3

    from doxagent.v2_read.cost_backfill import CostBackfillStore, run

    store = CostBackfillStore(tmp_path / "retry.db")
    store.migrate()
    store.ingest(
        "fixture",
        "call",
        [
            {
                "kind": "usage",
                "ticker": "MU",
                "id": "embedding",
                "day": "2026-10-20",
                "data": {
                    "scope": "API",
                    "node": "EMBEDDING",
                    "provider": "bailian",
                    "model": "qwen3.7-text-embedding",
                    "usage": {"input_tokens": 1000000, "total_tokens": 1000000},
                },
            }
        ],
    )
    original = store.ingest
    attempts = []

    def interrupted(*args, **kwargs):
        attempts.append(kwargs["expected_usage_versions"])
        if len(attempts) == 1:
            error = sqlite3.OperationalError("database is locked")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "ingest", interrupted)
    monkeypatch.setattr("doxagent.v2_read.cost_backfill.time.sleep", lambda _: None)
    result = run(store, batch_size=8, delay=0)
    assert result["scanned"] == result["reprojected"] == 1
    assert result["busy_retries"] == 1
    assert attempts[0] == attempts[1]
    with store.connect() as db:
        value = db.execute(
            "SELECT value FROM metric_buckets_current "
            "WHERE ticker='MU' AND day='*' AND metric='requests'"
        ).fetchone()[0]
    assert value == "1"


def test_sql_decimal_reduction_preserves_money_ticker_facets_and_omits_retired_rows(tmp_path):
    store = ReadStore(tmp_path / "exact.db")
    store.migrate()
    amounts = {"MU": "12345678901234567.12345678901", "GLOBAL": "0.000000000001"}
    counts = []
    for ticker, amount in amounts.items():
        counts.extend(
            contributions(
                ticker,
                ticker,
                "R2",
                {
                    "requests": 1,
                    "total_cost_usd": amount,
                    "total_cost_usd_samples": 1,
                    "normalization_v1_attempts": 1,
                },
            )
        )
    counts.extend(contributions("retired", "OLD", "UNKNOWN_ROUND", {"requests": 0}))
    seq = store.ingest("test", "exact", [], contributions=counts)
    costs = Costs(store, None)
    for ticker in ["ALL", *amounts]:
        data = report(costs, view(seq), {"scope": "API", "ticker_selector": ticker})
        validate("CostReport", data)
        with localcontext() as context:
            context.prec = 64
            expected = (
                sum(map(Decimal, amounts.values())) if ticker == "ALL" else Decimal(amounts[ticker])
            )
        assert Decimal(data["totals"]["total_cost_usd"]["current"]["value"]) == expected
        assert data["filter_choices"]["tickers"] == ["GLOBAL", "MU"]
        assert data["filter_choices"]["nodes"] == ["W1R2"]
        assert data["row_count"] == 1
        assert data["rows"][0]["round"] == "R2"
