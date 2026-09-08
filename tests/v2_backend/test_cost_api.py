from doxagent.api_v2.cost import Costs
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.usage import observations


def test_cost_known_zero_missing_cached_and_channel_filter_are_separate(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    values = observations(
        "qwen3.8-flash",
        "bailian",
        "API",
        "2026-09-08T12:00:00Z",
        {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 3},
    )

    def ingest(identity, values):
        return store.ingest(
            "runtime",
            identity,
            [],
            contributions=[
                {
                    "metric": key,
                    "ticker": "MU",
                    "entity": identity,
                    "day": "2026-09-08",
                    "dimensions": {
                        "scope": "API",
                        "provider": "bailian",
                        "model": "qwen3.8-flash",
                        "node": "W1",
                    },
                    "value": amount,
                }
                for key, amount in values.items()
            ],
        )

    seq = ingest("call-a", values)
    view = {"seq": seq, "wire": {"page": "COST", "period": {"selected": "ALL"}}}
    costs = Costs(store, None)
    totals = costs.totals("MU", view, {"scope": "API"})
    assert totals["requests"]["value"] == 1
    assert totals["cached_input_tokens"]["current"]["value"] == "0"
    assert totals["cache_hit_ratio"]["current"]["value"] == "0"
    assert totals["average_total_tokens_per_request"]["value"] == "23"
    missing = observations(
        "qwen3.8-flash",
        "bailian",
        "API",
        "2026-09-08T12:00:00Z",
        {"input_tokens": 20, "output_tokens": 3},
    )
    seq = ingest("call-b", missing)
    totals = costs.totals("MU", {**view, "seq": seq}, {"scope": "API"})
    assert totals["requests"]["value"] == 2
    assert totals["cache_hit_ratio"]["current"]["value"] is None
    assert totals["unpriced_request_count"]["value"] == 1
    assert missing["output_cost_usd"] is not None
    assert missing["input_cost_usd"] is None
    assert (
        observations(
            "qwen3.8-flash",
            "other-provider",
            "API",
            "2026-09-08T12:00:00Z",
            {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 3},
        )["total_cost_usd"]
        is None
    )
