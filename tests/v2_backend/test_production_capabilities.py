from datetime import timedelta

from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.schema import ExecutionProfile
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_api import OfflineAuth


def test_new_ticker_mode_capabilities_use_binding_and_live_workers(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    journal = RuntimeJournal(tmp_path / "runtime.db")
    control = ControlRepository(journal)
    control.migrate()
    execution = ExecutionRepository(journal)
    for environment, account, account_mode in [
        ("PAPER", "DU123", "PAPER"),
        ("LIVE", "U123", "LIVE_CASH"),
    ]:
        revision = execution.import_profile(
            ExecutionProfile(
                profile_id=environment,
                environment=environment,
                account_mode=account_mode,
                expected_account_id=account,
                port=4002,
                client_id=1,
            )
        )
        control.bind("MU", environment + "_TRADING", revision)
    for worker in ("control", "delivery", "executor"):
        journal.set("v2_workers", worker, {"heartbeat_at": journal.clock().isoformat()})
    assert control.get("MU") is None
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:

        def capabilities(ticker):
            response = client.get(
                PREFIX + "/capabilities",
                params={"ticker": ticker},
                headers={"Authorization": "Bearer offline"},
            )
            assert response.status_code == 200
            return response.json()["data"]

        assert capabilities("MU")["paper_trading"]["available"]
        assert capabilities("MU")["live_trading"]["available"]
        assert not capabilities("NVDA")["paper_trading"]["available"]
        journal.set(
            "v2_workers",
            "executor",
            {"heartbeat_at": (journal.clock() - timedelta(minutes=1)).isoformat()},
        )
        assert not capabilities("MU")["live_trading"]["available"]
        assert capabilities("MU")["monitoring"]["available"]
    with journal.transaction() as db:
        assert db.execute("SELECT count(*) FROM te_jobs").fetchone()[0] == 0
