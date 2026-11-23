"""Drive Runtime-owned exits in the existing separate Transport process."""
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace

_owner=None


def collect(settings):
    global _owner
    path=Path(settings.persistent_runtime_v2_sqlite_path)
    control=Path(settings.ticker_initialization_control_path)
    if not path.is_file() or not control.is_file():return {'next_seconds':900,'waiting':'runtime_authority'}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.01)) as db:
        if db.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('runtime_resource_exit_meta','runtime_case_resource_refs')").fetchone()[0]!=2:return {'next_seconds':60,'waiting':'runtime_writer_schema'}
    if _owner is None or _owner.journal.path!=path:
        from .journal import RuntimeJournal
        from doxagent.ticker_initialization.repository import InitializationRepository
        _owner=SimpleNamespace(settings=settings,journal=RuntimeJournal(path,initialize=False,busy_timeout_seconds=.01),control=InitializationRepository(control,initialize=False))
    from .owner_exit import register_legacy,drain
    from .history_exit import collect as histories
    legacy=register_legacy(_owner.journal)
    report=drain(_owner.journal,settings,seconds=.05)
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.workflows.codex_document3.history_exit import audit_case_evidence
    report['legacy_trade_evidence_ready']=audit_case_evidence(SourceOutbox(path,'runtime'),limit=64)
    report['legacy']=legacy;report['history']=histories(_owner)
    report['next_seconds']=60 if report['processed'] or not legacy['complete'] or not report['legacy_trade_evidence_ready'] or report['history'].get('next_seconds')==60 else 900
    return report
