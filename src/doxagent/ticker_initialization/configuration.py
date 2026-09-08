"""Candidate monitoring configuration isolated from live polling and message payloads."""

from __future__ import annotations

import json
import re
from pathlib import Path

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import TickerSourceBinding
from doxagent.message_bus_v2.service import MessageBusV2Service


def candidate_bus_path(live_path: str | Path, initialization_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", initialization_id) or initialization_id in {
        ".",
        "..",
    }:
        raise ValueError("invalid initialization identity")
    live = Path(live_path).resolve()
    return live.parent / "initialization-configurations" / initialization_id / "message_bus.sqlite3"


class CandidateConfiguration:
    """Copies configuration only; never copies stream/raw data or runtime offsets."""

    def __init__(self, live_path: str | Path, initialization_id: str, ticker: str) -> None:
        self.live = MessageBusV2Repository(live_path)
        self.candidate = MessageBusV2Repository(candidate_bus_path(live_path, initialization_id))
        self.ticker = ticker.strip().upper()
        self.initialization_id = initialization_id

    def prepare(self, *, snapshot_from: str | Path | None = None) -> Path:
        self._snapshot(snapshot_from=snapshot_from)
        service = MessageBusV2Service(self.candidate)
        service.bootstrap()
        service.materialize_default_bindings(self.ticker)
        return self.candidate.path

    def _snapshot(self, *, snapshot_from: str | Path | None = None) -> Path:
        with self.candidate.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS configuration_snapshot (ticker TEXT PRIMARY KEY)"
            )
            if db.execute(
                "SELECT 1 FROM configuration_snapshot WHERE ticker=?", (self.ticker,)
            ).fetchone():
                return self.candidate.path
            source = Path(snapshot_from).resolve() if snapshot_from else self.live.path.resolve()
            if not source.is_file():
                raise ValueError("configuration snapshot source does not exist")
            db.execute("ATTACH DATABASE ? AS live", (str(source),))
            for table in (
                "source_definitions",
                "source_definition_revisions",
                "default_profiles",
                "default_profile_revisions",
            ):
                db.execute(f"INSERT INTO {table} SELECT * FROM live.{table}")
            db.execute(
                """INSERT INTO ticker_source_bindings
                SELECT * FROM live.ticker_source_bindings WHERE ticker=?""",
                (self.ticker,),
            )
            db.execute(
                """INSERT INTO ticker_source_binding_revisions
                SELECT r.* FROM live.ticker_source_binding_revisions r
                JOIN live.ticker_source_bindings b ON b.binding_id=r.binding_id
                WHERE b.ticker=?""",
                (self.ticker,),
            )
            # Deliberately no ticker_monitoring_states: a candidate is never pollable.
            db.execute("INSERT INTO configuration_snapshot VALUES(?)", (self.ticker,))
        return self.candidate.path

    def install(self) -> None:
        """Atomically install the ticker portfolio; retain all consumer/poll history."""
        bindings = self.candidate.list_bindings(ticker=self.ticker)
        if not any(binding.enabled and binding.tombstoned_at is None for binding in bindings):
            raise ValueError("candidate has no usable monitoring binding")
        with self.live.transaction() as db:
            from doxagent.v2_control.mirror import state_in

            control = state_in(db, self.ticker)
            if control and not (control["analysis_allowed"] or control.get("admission_allowed")):
                raise ValueError("V2 control has stopped configuration admission")
            if (
                control
                and control.get("minimum_epoch")
                and (control.get("initialization_id") != self.initialization_id)
            ):
                raise ValueError("V2 configuration belongs to a removed initialization")
            db.execute("""CREATE TABLE IF NOT EXISTS initialization_config_receipts (
                initialization_id TEXT PRIMARY KEY, ticker TEXT NOT NULL,
                previous_bindings TEXT NOT NULL, previous_operation TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS initialization_config_head (
                ticker TEXT PRIMARY KEY, initialization_id TEXT NOT NULL)""")
            old_receipt = db.execute(
                "SELECT previous_operation FROM initialization_config_receipts "
                "WHERE initialization_id=?",
                (self.initialization_id,),
            ).fetchone()
            if old_receipt:
                head = db.execute(
                    "SELECT initialization_id FROM initialization_config_head WHERE ticker=?",
                    (self.ticker,),
                ).fetchone()
                current = head[0] if head else None
                if current == self.initialization_id:
                    return
                if current != old_receipt[0]:
                    raise ValueError("configuration installation was superseded")
            db.execute("ATTACH DATABASE ? AS candidate", (str(self.candidate.path.resolve()),))
            previous = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data_json FROM ticker_source_bindings WHERE ticker=?", (self.ticker,)
                )
            ]
            previous_head = db.execute(
                "SELECT initialization_id FROM initialization_config_head WHERE ticker=?",
                (self.ticker,),
            ).fetchone()
            for binding in bindings:
                source = self.candidate.get_source(binding.source_id)
                if source is None:
                    raise ValueError(f"candidate source missing: {binding.source_id}")
                existing = self.live.get_source(binding.source_id)
                semantic_fields = {"kind", "adapter_ref", "parameter_schema", "scheduler_group"}
                if existing and existing.model_dump(include=semantic_fields) != source.model_dump(
                    include=semantic_fields
                ):
                    # Shared source mutation must not silently affect other tickers.
                    raise ValueError(f"shared source changed: {binding.source_id}")
                db.execute(
                    """INSERT OR IGNORE INTO source_definitions
                    SELECT * FROM candidate.source_definitions WHERE source_id=?""",
                    (binding.source_id,),
                )
                db.execute(
                    """INSERT OR IGNORE INTO source_definition_revisions
                    SELECT * FROM candidate.source_definition_revisions WHERE source_id=?""",
                    (binding.source_id,),
                )
            db.execute("DELETE FROM ticker_source_bindings WHERE ticker=?", (self.ticker,))
            for binding in bindings:
                highest = (
                    db.execute(
                        "SELECT max(version) FROM ticker_source_binding_revisions "
                        "WHERE binding_id=?",
                        (binding.binding_id,),
                    ).fetchone()[0]
                    or 0
                )
                source = self.live.get_source(binding.source_id) or self.candidate.get_source(
                    binding.source_id
                )
                assert source is not None
                installed = binding.model_copy(
                    update={
                        "version": max(binding.version, highest + 1),
                        "source_version": source.version,
                    }
                )
                self.live._save_binding_tx(db, installed)
            db.execute(
                "INSERT OR IGNORE INTO initialization_config_receipts VALUES(?,?,?,?)",
                (
                    self.initialization_id,
                    self.ticker,
                    json.dumps(previous),
                    previous_head[0] if previous_head else None,
                ),
            )
            db.execute(
                """INSERT INTO initialization_config_head VALUES(?,?)
                ON CONFLICT(ticker) DO UPDATE SET initialization_id=excluded.initialization_id""",
                (self.ticker, self.initialization_id),
            )

    def rollback(self) -> bool:
        """Compensate only this installation; never overwrite a subsequent activation."""
        if not self.installed():
            return False
        with self.live.transaction() as db:
            head = db.execute(
                "SELECT initialization_id FROM initialization_config_head WHERE ticker=?",
                (self.ticker,),
            ).fetchone()
            if not head or head[0] != self.initialization_id:
                return False
            receipt = db.execute(
                "SELECT * FROM initialization_config_receipts WHERE initialization_id=?",
                (self.initialization_id,),
            ).fetchone()
            db.execute("DELETE FROM ticker_source_bindings WHERE ticker=?", (self.ticker,))
            for payload in json.loads(receipt["previous_bindings"]):
                binding = TickerSourceBinding.model_validate(payload)
                self.live._save_binding_tx(db, binding)
            if receipt["previous_operation"]:
                db.execute(
                    "UPDATE initialization_config_head SET initialization_id=? WHERE ticker=?",
                    (receipt["previous_operation"], self.ticker),
                )
            else:
                db.execute("DELETE FROM initialization_config_head WHERE ticker=?", (self.ticker,))
            return True

    def installed(self) -> bool:
        with self.live._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='initialization_config_receipts'"
            ).fetchone()
            return bool(
                exists
                and db.execute(
                    "SELECT 1 FROM initialization_config_receipts WHERE initialization_id=?",
                    (self.initialization_id,),
                ).fetchone()
            )

    def current_head(self) -> str | None:
        with self.live._connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='initialization_config_head'"
            ).fetchone():
                return None
            row = db.execute(
                "SELECT initialization_id FROM initialization_config_head WHERE ticker=?",
                (self.ticker,),
            ).fetchone()
            return str(row[0]) if row else None

    def previous_head(self) -> str | None:
        if not self.installed():
            return None
        with self.live._connect() as db:
            row = db.execute(
                "SELECT previous_operation FROM initialization_config_receipts "
                "WHERE initialization_id=?",
                (self.initialization_id,),
            ).fetchone()
            return str(row[0]) if row and row[0] else None
