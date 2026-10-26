import sqlite3
from unittest.mock import MagicMock

from doxagent.message_bus_v2 import factory
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings


def test_bus_startup_opens_provisioned_runtime_without_writer_lock(tmp_path,monkeypatch):
    path=tmp_path/'runtime.db'
    RuntimeJournal(path)
    settings=DoxAgentSettings(_env_file=None,dashscope_api_key=None,
        persistent_runtime_v2_sqlite_path=str(path),
        ticker_initialization_control_path=str(tmp_path/'init.db'))
    monkeypatch.setattr(factory,'build_message_bus_v2_service',lambda _: (MagicMock(),MagicMock()))
    for name in ('build_crawler_plane_service','AdapterRegistry','GlobalPollScheduler'):
        monkeypatch.setattr(factory,name,MagicMock())
    monkeypatch.setattr('doxagent.ticker_initialization.repository.InitializationRepository',MagicMock())
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        runtime=factory.build_message_bus_v2_runtime(settings)
        assert runtime.scheduler.runtime_orchestration.journal.path==path
