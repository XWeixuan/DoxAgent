import json
import sqlite3

import httpx
import pytest

from doxagent.persistent_runtime_v2 import model_clock
from doxagent.persistent_runtime_v2.openrouter import W2R1OpenRouterClient
from doxagent.v2_read.native_content import MARKER
from doxagent.v2_read.outbox import SourceOutbox
from tests.test_w2_r1_openrouter import bailian, request, stream_response


@pytest.mark.parametrize("status", [200, 403])
def test_openrouter_records_send_and_stream_end_even_on_failure(status):
    interval = {}
    token = model_clock.current.set(interval)

    def handler(req):
        assert interval.get("start") and not interval.get("end")
        return stream_response() if status == 200 else httpx.Response(status)

    try:
        client = W2R1OpenRouterClient(
            bailian(),
            api_key="offline",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        if status == 200:
            client.complete(request())
        else:
            from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError

            with pytest.raises(RuntimeResponsesError):
                client.complete(request())
        assert interval["end"] >= interval["start"]
    finally:
        model_clock.current.reset(token)


def test_effect_receipt_serving_keeps_effective_clock_without_loading_unrelated_evidence(tmp_path):
    path = tmp_path / "runtime.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE runtime_v2_effects(effect_id TEXT PRIMARY KEY,"
            "case_id TEXT,payload_json TEXT)"
        )
    source = SourceOutbox(path, "runtime")
    source.migrate(("runtime_v2_effects",))
    payload = json.dumps(
        {
            "decision_effective_at": "2026-11-13T00:00:00Z",
            "unused_result": {MARKER: "not-a-readable-evidence-file", "bytes": 123},
        }
    ).encode()
    pointer = {MARKER: source.content.files.put(payload), "bytes": len(payload)}
    value = dict(
        effect_id="e", case_id="c", effect_type="route_to_w3", status="COMPLETED", payload=pointer
    )
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO runtime_v2_effects VALUES(?,?,?)", ("e", "c", json.dumps(value)))
    event = source.event(source.head(), serving=True, projection=True)
    assert not event.get("serving_skipped")
    assert event["native_value"] == dict(
        effect_id="e",
        case_id="c",
        effect_type="route_to_w3",
        payload={"decision_effective_at": "2026-11-13T00:00:00Z"},
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO runtime_v2_effects VALUES(?,?,?)",
            ("unused", "c", json.dumps(dict(effect_id="unused", case_id="c", payload={}))),
        )
    assert source.event(source.head(), serving=True)["serving_skipped"]
