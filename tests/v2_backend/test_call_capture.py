import sqlite3
from types import SimpleNamespace

import pytest

from cdecr import usage_capture
from doxagent.ticker_initialization.usage_capture import flush, migrate, publish


def test_actual_retry_has_new_identity_and_unknown_is_not_zero(monkeypatch, tmp_path):
    receipts = []
    monkeypatch.setattr(usage_capture, "observer", receipts.append)

    def failed(**kwargs):
        raise RuntimeError("provider error")

    with pytest.raises(RuntimeError):
        usage_capture.call(failed, _usage_provider="bailian", _usage_node="N2", model="m")
    result = SimpleNamespace(
        usage={"input_tokens": 20, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 0}}
    )
    assert (
        usage_capture.call(
            lambda **kw: result, _usage_provider="bailian", _usage_node="N2", model="m"
        )
        is result
    )
    assert receipts[0]["invocation_id"] == receipts[1]["invocation_id"]
    assert receipts[1]["invocation_id"] != receipts[3]["invocation_id"]
    assert receipts[1]["usage"]["input_tokens"] is None
    assert receipts[3]["usage"]["cached_input_tokens"] == 0
    path = tmp_path / "init.db"
    with sqlite3.connect(path) as db:
        migrate(db)
    receipt = {**receipts[3], "ticker": "MU", "initialization_id": "init"}
    publish(path, receipt)
    publish(path, receipt)
    assert flush(path) == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM v2_model_invocations").fetchone()[0] == 1
    assert flush(path, selected=path.parent / "already-drained.json") == 0
