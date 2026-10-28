"""Refresh configuration off the acquisition loop; retain its last good snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from ..collection_override import CollectionControl, matches
from ..monitoring_terms import MonitoringTermsService
from ..repository import MessageBusV2Repository
from ..schema import utc_now
from doxagent.semantic_clock import semantic_day


def install_revision(repo):
    """Only configuration mutations invalidate compiled collection plans."""
    tables={"source_definitions":"OLD.version IS NOT NEW.version OR OLD.enabled IS NOT NEW.enabled",
        "ticker_source_bindings":"OLD.version IS NOT NEW.version OR OLD.enabled IS NOT NEW.enabled OR OLD.tombstoned_at IS NOT NEW.tombstoned_at",
        "ticker_monitoring_states":"OLD.status IS NOT NEW.status",
        "ticker_monitoring_terms":"OLD.current_revision IS NOT NEW.current_revision",
        "bus_collection_control":"OLD.revision IS NOT NEW.revision OR OLD.data_json IS NOT NEW.data_json"}
    with repo.transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS bus_configuration(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL)")
        db.execute("INSERT OR IGNORE INTO bus_configuration VALUES(1,0)")
        for table,condition in tables.items():
            for event in ("INSERT","UPDATE","DELETE"):
                when=" WHEN "+condition if event=="UPDATE" else ""
                db.execute(f"CREATE TRIGGER IF NOT EXISTS bus_config_{table}_{event} AFTER {event} ON {table}{when} "
                           "BEGIN UPDATE bus_configuration SET revision=revision+1 WHERE id=1; END")


class ConfigurationLoader:
    def __init__(self):
        self.current=None
        self.key=None

    def __call__(self,settings,*,seeds=False):
        import sqlite3
        from datetime import timedelta
        path=Path(settings.message_bus_v2_sqlite_path).resolve()
        with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=.2) as db:
            revision=db.execute("SELECT revision FROM bus_configuration WHERE id=1").fetchone()[0]
        runtime=Path(settings.persistent_runtime_v2_sqlite_path).resolve()
        external=[]
        if runtime.is_file():
            with sqlite3.connect(runtime.as_uri()+"?mode=ro",uri=True,timeout=.2) as db:
                external=db.execute("SELECT namespace,key,payload FROM runtime_values "
                    "WHERE namespace IN ('pause','calendar_overrides') ORDER BY namespace,key").fetchall()
        now=utc_now();day=semantic_day(now)
        # The one-minute first-open transition is independent of config writes.
        from doxagent.semantic_clock import boundary
        key=(revision,day,now>=boundary(day,minute=1),tuple(external))
        if seeds or self.current is None or key!=self.key:
            self.current=load(settings,seeds=seeds)
            self.key=key
        self.current.at=now.isoformat()
        return self.current


@dataclass
class Configuration:
    sources: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    terms: dict = field(default_factory=dict)
    seeds: dict = field(default_factory=dict)
    ir_seeds: dict = field(default_factory=dict)
    override: dict = field(default_factory=dict)
    pauses: dict = field(default_factory=dict)
    realtime: bool = False
    at: str = ""
    calendar_day: str = ""

    def allowed(self, binding, now):
        current = self.bindings.get(binding.binding_id)
        source = self.sources.get(binding.source_id)
        return bool(
            current
            and source
            and current.version == binding.version
            and current.enabled
            and current.polling.enabled
            and source.enabled
            and current.polling.active_at(now)
            and self.pauses.get(binding.ticker) != "all"
            and (
                source.source_id in {"corporate_ir_press_releases", "sec_edgar"}
                or (
                    self.realtime
                    and (
                        not self.calendar_day or self.calendar_day == semantic_day(now).isoformat()
                    )
                )
                or matches(self.override, binding.ticker, binding.source_id, now)
            )
        )


def load(settings, *, seeds=False):
    from doxagent.persistent_runtime_v2.calendar import MarketCalendar
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.semantic_clock import semantic_day, boundary
    from datetime import timedelta
    from ..corporate_ir.repository import get_state
    from ..corporate_ir.schema import endpoints

    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=False)
    result = Configuration(at=utc_now().isoformat())
    # WAL read snapshot, never an initialization or heartbeat write.
    with repo.connection_scope() as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        result.sources = {s.source_id: s for s in repo.list_sources()}
        running = {t.ticker for t in repo.list_ticker_states() if t.status.value == "running"}
        result.bindings = {
            b.binding_id: b for b in repo.list_bindings(active_only=True) if b.ticker in running
        }
        terms = MonitoringTermsService(repo)
        result.terms = {t: terms.get(t) for t in running}
        result.override = CollectionControl(repo).status()
        if seeds:
            for b in result.bindings.values():
                result.seeds[b.binding_id] = repo.get_poll_state(b)
                s = result.sources.get(b.source_id)
                if s and s.source_id == "corporate_ir_press_releases":
                    for spec in endpoints(s, b):
                        result.ir_seeds[(b.binding_id, spec.endpoint_id)] = get_state(
                            repo, b.binding_id, spec.endpoint_id
                        )
    if Path(settings.persistent_runtime_v2_sqlite_path).is_file():
        journal = RuntimeJournal(
            settings.persistent_runtime_v2_sqlite_path, initialize=False, busy_timeout_seconds=0.5
        )
        calendar = MarketCalendar(SimpleNamespace(get=journal.get, set=lambda *args: None))
        day = semantic_day(utc_now())
        result.calendar_day = day.isoformat()
        result.realtime = calendar.is_session(day)
        if not calendar.is_session(day - timedelta(days=1)) and utc_now() < boundary(day, minute=1):
            result.realtime = False
        result.pauses = {t: journal.get("pause", t) for t in running}
    else:
        # Test/development without Runtime still uses the real exchange calendar.
        import exchange_calendars

        result.realtime = exchange_calendars.get_calendar("XNYS").is_session(utc_now().date())
    return result
