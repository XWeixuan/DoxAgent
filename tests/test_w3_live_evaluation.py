import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.w3_backtest.live_evaluation import EvaluationWorker
from doxagent.w3_backtest.live_history import validate


def test_history_bridge_rejects_non_history_and_other_ticker():
    base=dict(ticker="COHR", bar="5 mins", duration="14400 S",
              venue="SMART", end=datetime.now(UTC).isoformat())
    validate(base, "COHR")
    for changes in (dict(ticker="MU"), dict(bar="placeOrder"), dict(venue="ACCOUNT"),
                    dict(duration="1 Y"), dict(end="2099-01-01T00:00:00Z")):
        with pytest.raises(ValueError):
            validate({**base, **changes}, "COHR")


def test_local_worker_rejects_operations_before_dispatch():
    worker=object.__new__(EvaluationWorker)
    for phase in ("post_decision_operations", "trade_execution", "operations"):
        with pytest.raises(ValueError, match="only template"):
            asyncio.run(worker.run(SimpleNamespace(task_phase=phase)))


def test_local_worker_dispatches_only_first_phase():
    calls=[]
    class Client:
        async def run(self, request):
            calls.append(request.task_phase)
            return "recorded"
    worker=object.__new__(EvaluationWorker)
    worker.client=Client()
    for phase in ("w3_template", "trade_decision"):
        assert asyncio.run(worker.run(SimpleNamespace(task_phase=phase)))=="recorded"
    assert calls==["w3_template","trade_decision"]
