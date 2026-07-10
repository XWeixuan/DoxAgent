"""Lightweight, range-bounded persistence for revenue audit runs and results."""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from doxagent.monitoring.schema import canonical_json
from doxagent.revenue_audit.schema import (
    RevenueAuditResult,
    RevenueAuditRun,
    RevenueAuditRunStatus,
    RevenueBasis,
)

JsonObject = dict[str, Any]


class RevenueAuditRepository(Protocol):
    def get_run(
        self,
        *,
        ticker: str,
        audit_date: date,
        method_version: str,
        config_fingerprint: str,
    ) -> RevenueAuditRun | None: ...

    def save_run(self, run: RevenueAuditRun) -> RevenueAuditRun: ...

    def save_results(self, results: list[RevenueAuditResult]) -> None: ...

    def overview(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject: ...

    def latency_losses(
        self,
        *,
        ticker: str,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject: ...

    def trend(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> list[JsonObject]: ...

    def list_results(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[RevenueAuditResult], str | None]: ...

    def result_detail(
        self,
        *,
        ticker: str,
        trading_record_id: str,
        method_version: str,
        config_fingerprint: str,
    ) -> list[RevenueAuditResult]: ...


class InMemoryRevenueAuditRepository:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, date, str, str], RevenueAuditRun] = {}
        self._results: dict[str, RevenueAuditResult] = {}

    def get_run(
        self,
        *,
        ticker: str,
        audit_date: date,
        method_version: str,
        config_fingerprint: str,
    ) -> RevenueAuditRun | None:
        item = self._runs.get((_ticker(ticker), audit_date, method_version, config_fingerprint))
        return item.model_copy(deep=True) if item is not None else None

    def save_run(self, run: RevenueAuditRun) -> RevenueAuditRun:
        key = (
            _ticker(run.ticker),
            run.audit_date,
            run.method_version,
            run.config_fingerprint,
        )
        existing = self._runs.get(key)
        if existing is not None and existing.run_id != run.run_id:
            run = run.model_copy(update={"run_id": existing.run_id})
        self._runs[key] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    def save_results(self, results: list[RevenueAuditResult]) -> None:
        for result in results:
            self._results[result.result_id] = result.model_copy(deep=True)

    def overview(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject:
        rows = self._filtered(
            ticker=ticker,
            basis=basis,
            date_from=date_from,
            date_to=date_to,
            method_version=method_version,
            config_fingerprint=config_fingerprint,
        )
        return _aggregate_overview(
            rows,
            self._latest_run_status(
                ticker,
                date_from,
                date_to,
                method_version,
                config_fingerprint,
            ),
        )

    def latency_losses(
        self,
        *,
        ticker: str,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject:
        rows = [
            item
            for item in self._results.values()
            if item.ticker == _ticker(ticker)
            and date_from <= item.audit_date <= date_to
            and item.method_version == method_version
            and item.config_fingerprint == config_fingerprint
        ]
        return _latency_losses_from_rows(rows)

    def trend(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> list[JsonObject]:
        rows = self._filtered(
            ticker=ticker,
            basis=basis,
            date_from=date_from,
            date_to=date_to,
            method_version=method_version,
            config_fingerprint=config_fingerprint,
        )
        grouped: dict[date, list[RevenueAuditResult]] = {}
        for item in rows:
            grouped.setdefault(item.audit_date, []).append(item)
        return [
            {"date": day.isoformat(), **_aggregate_day(grouped[day])} for day in sorted(grouped)
        ]

    def list_results(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[RevenueAuditResult], str | None]:
        rows = self._filtered(
            ticker=ticker,
            basis=basis,
            date_from=date_from,
            date_to=date_to,
            method_version=method_version,
            config_fingerprint=config_fingerprint,
        )
        if status:
            rows = [item for item in rows if item.status.value == status]
        rows.sort(key=lambda item: (item.intent_generated_at, item.result_id), reverse=True)
        cursor_value = _decode_cursor(cursor)
        if cursor_value is not None:
            rows = [
                item
                for item in rows
                if (item.intent_generated_at.isoformat(), item.result_id) < cursor_value
            ]
        page_size = max(1, min(limit, 200))
        selected = rows[: page_size + 1]
        has_more = len(selected) > page_size
        page = selected[:page_size]
        next_cursor = _cursor_for(page[-1]) if has_more and page else None
        return [item.model_copy(deep=True) for item in page], next_cursor

    def result_detail(
        self,
        *,
        ticker: str,
        trading_record_id: str,
        method_version: str,
        config_fingerprint: str,
    ) -> list[RevenueAuditResult]:
        rows = [
            item.model_copy(deep=True)
            for item in self._results.values()
            if item.ticker == _ticker(ticker)
            and item.trading_record_id == trading_record_id
            and item.method_version == method_version
            and item.config_fingerprint == config_fingerprint
        ]
        rows.sort(key=lambda item: item.basis.value)
        return rows

    def _filtered(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> list[RevenueAuditResult]:
        return [
            item.model_copy(deep=True)
            for item in self._results.values()
            if item.ticker == _ticker(ticker)
            and item.basis is basis
            and date_from <= item.audit_date <= date_to
            and item.method_version == method_version
            and item.config_fingerprint == config_fingerprint
        ]

    def _latest_run_status(
        self,
        ticker: str,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> str:
        rows = [
            run
            for run in self._runs.values()
            if run.ticker == _ticker(ticker)
            and date_from <= run.audit_date <= date_to
            and run.method_version == method_version
            and run.config_fingerprint == config_fingerprint
        ]
        if not rows:
            return RevenueAuditRunStatus.NOT_STARTED.value
        return max(rows, key=lambda item: item.started_at).status.value


class SQLiteRevenueAuditRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_run(
        self,
        *,
        ticker: str,
        audit_date: date,
        method_version: str,
        config_fingerprint: str,
    ) -> RevenueAuditRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select payload_json
                from revenue_audit_runs
                where ticker = ? and audit_date = ? and method_version = ?
                  and config_fingerprint = ?
                limit 1
                """,
                (
                    _ticker(ticker),
                    audit_date.isoformat(),
                    method_version,
                    config_fingerprint,
                ),
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def save_run(self, run: RevenueAuditRun) -> RevenueAuditRun:
        with self._connect() as conn:
            conn.execute(
                """
                insert into revenue_audit_runs
                    (run_id, ticker, audit_date, method_version, config_fingerprint,
                     status, started_at, completed_at, payload_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(ticker, audit_date, method_version, config_fingerprint)
                do update set
                    status = excluded.status,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at,
                    payload_json = excluded.payload_json
                """,
                (
                    run.run_id,
                    _ticker(run.ticker),
                    run.audit_date.isoformat(),
                    run.method_version,
                    run.config_fingerprint,
                    run.status.value,
                    run.started_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    canonical_json(run.model_dump(mode="json")),
                ),
            )
        resolved = self.get_run(
            ticker=run.ticker,
            audit_date=run.audit_date,
            method_version=run.method_version,
            config_fingerprint=run.config_fingerprint,
        )
        if resolved is None:
            raise RuntimeError("Revenue audit run was not persisted.")
        return resolved

    def save_results(self, results: list[RevenueAuditResult]) -> None:
        if not results:
            return
        rows = [
            (
                item.result_id,
                item.run_id,
                item.trading_record_id,
                _ticker(item.ticker),
                item.source_message_id,
                item.audit_date.isoformat(),
                item.basis.value,
                item.status.value,
                item.side,
                item.size_bucket,
                item.decision_source,
                item.trigger_policy,
                item.intent_generated_at.isoformat(),
                item.notional_usd,
                item.simulated_pnl_usd,
                item.simulated_return_pct,
                item.method_version,
                item.config_fingerprint,
                canonical_json(item.model_dump(mode="json")),
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
            )
            for item in results
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                insert into revenue_audit_results
                    (result_id, run_id, trading_record_id, ticker, source_message_id,
                     audit_date, basis, status, side, size_bucket, decision_source,
                     trigger_policy, intent_generated_at, notional_usd,
                     simulated_pnl_usd, simulated_return_pct, method_version,
                     config_fingerprint, payload_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(result_id) do update set
                    run_id = excluded.run_id,
                    status = excluded.status,
                    notional_usd = excluded.notional_usd,
                    simulated_pnl_usd = excluded.simulated_pnl_usd,
                    simulated_return_pct = excluded.simulated_return_pct,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def overview(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject:
        params = _range_params(
            ticker, basis, date_from, date_to, method_version, config_fingerprint
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                select
                    count(*) as trade_intent_count,
                    sum(case when status <> 'unsupported_action' then 1 else 0 end)
                        as auditable_trade_count,
                    sum(case when status = 'audited' then 1 else 0 end) as audited_trade_count,
                    sum(case when status = 'audited' then simulated_pnl_usd end) as pnl_usd,
                    sum(case when status = 'audited' then notional_usd end) as notional_usd,
                    sum(case when status = 'audited' and simulated_pnl_usd > 0 then 1 else 0 end)
                        as win_count
                from revenue_audit_results
                where ticker = ? and basis = ? and audit_date between ? and ?
                  and method_version = ? and config_fingerprint = ?
                """,
                params,
            ).fetchone()
            status_row = conn.execute(
                """
                select status
                from revenue_audit_runs
                where ticker = ? and audit_date between ? and ?
                  and method_version = ? and config_fingerprint = ?
                order by started_at desc
                limit 1
                """,
                (
                    _ticker(ticker),
                    date_from.isoformat(),
                    date_to.isoformat(),
                    method_version,
                    config_fingerprint,
                ),
            ).fetchone()
        return _overview_from_sql_rows(row, status_row)

    def latency_losses(
        self,
        *,
        ticker: str,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject:
        return {
            "capture_loss": self._pair_loss(
                ticker=ticker,
                left=RevenueBasis.IDEAL_SIGNAL,
                right=RevenueBasis.MESSAGE_BUS,
                date_from=date_from,
                date_to=date_to,
                method_version=method_version,
                config_fingerprint=config_fingerprint,
            ),
            "decision_loss": self._pair_loss(
                ticker=ticker,
                left=RevenueBasis.MESSAGE_BUS,
                right=RevenueBasis.SYSTEM_EXECUTABLE,
                date_from=date_from,
                date_to=date_to,
                method_version=method_version,
                config_fingerprint=config_fingerprint,
            ),
            "total_latency_loss": self._pair_loss(
                ticker=ticker,
                left=RevenueBasis.IDEAL_SIGNAL,
                right=RevenueBasis.SYSTEM_EXECUTABLE,
                date_from=date_from,
                date_to=date_to,
                method_version=method_version,
                config_fingerprint=config_fingerprint,
            ),
        }

    def trend(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> list[JsonObject]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select
                    audit_date,
                    count(*) as trade_intent_count,
                    sum(case when status <> 'unsupported_action' then 1 else 0 end)
                        as auditable_trade_count,
                    sum(case when status = 'audited' then 1 else 0 end) as audited_trade_count,
                    sum(case when status = 'audited' then simulated_pnl_usd end) as pnl_usd,
                    sum(case when status = 'audited' then notional_usd end) as notional_usd
                from revenue_audit_results
                where ticker = ? and basis = ? and audit_date between ? and ?
                  and method_version = ? and config_fingerprint = ?
                group by audit_date
                order by audit_date asc
                """,
                _range_params(
                    ticker, basis, date_from, date_to, method_version, config_fingerprint
                ),
            ).fetchall()
        return [_trend_row(row) for row in rows]

    def list_results(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[RevenueAuditResult], str | None]:
        conditions = [
            "ticker = ?",
            "basis = ?",
            "audit_date between ? and ?",
            "method_version = ?",
            "config_fingerprint = ?",
        ]
        params: list[object] = list(
            _range_params(ticker, basis, date_from, date_to, method_version, config_fingerprint)
        )
        if status:
            conditions.append("status = ?")
            params.append(status)
        cursor_value = _decode_cursor(cursor)
        if cursor_value is not None:
            conditions.append(
                "(intent_generated_at < ? or (intent_generated_at = ? and result_id < ?))"
            )
            params.extend([cursor_value[0], cursor_value[0], cursor_value[1]])
        page_size = max(1, min(limit, 200))
        params.append(page_size + 1)
        sql = (
            "select payload_json from revenue_audit_results where "
            + " and ".join(conditions)
            + " order by intent_generated_at desc, result_id desc limit ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        items = [_result_from_row(row) for row in rows]
        has_more = len(items) > page_size
        page = items[:page_size]
        next_cursor = _cursor_for(page[-1]) if has_more and page else None
        return page, next_cursor

    def result_detail(
        self,
        *,
        ticker: str,
        trading_record_id: str,
        method_version: str,
        config_fingerprint: str,
    ) -> list[RevenueAuditResult]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select payload_json
                from revenue_audit_results
                where ticker = ? and trading_record_id = ? and method_version = ?
                  and config_fingerprint = ?
                order by basis asc
                limit 3
                """,
                (
                    _ticker(ticker),
                    trading_record_id,
                    method_version,
                    config_fingerprint,
                ),
            ).fetchall()
        return [_result_from_row(row) for row in rows]

    def _pair_loss(
        self,
        *,
        ticker: str,
        left: RevenueBasis,
        right: RevenueBasis,
        date_from: date,
        date_to: date,
        method_version: str,
        config_fingerprint: str,
    ) -> JsonObject:
        with self._connect() as conn:
            row = conn.execute(
                """
                select
                    count(*) as matched_trade_count,
                    sum(l.simulated_pnl_usd - r.simulated_pnl_usd) as pnl_usd,
                    sum(l.simulated_pnl_usd) as left_pnl,
                    sum(l.notional_usd) as left_notional,
                    sum(r.simulated_pnl_usd) as right_pnl,
                    sum(r.notional_usd) as right_notional
                from revenue_audit_results l
                join revenue_audit_results r
                  on r.trading_record_id = l.trading_record_id
                 and r.method_version = l.method_version
                 and r.config_fingerprint = l.config_fingerprint
                where l.ticker = ? and l.basis = ? and r.basis = ?
                  and l.audit_date between ? and ?
                  and l.method_version = ? and l.config_fingerprint = ?
                  and l.status = 'audited' and r.status = 'audited'
                """,
                (
                    _ticker(ticker),
                    left.value,
                    right.value,
                    date_from.isoformat(),
                    date_to.isoformat(),
                    method_version,
                    config_fingerprint,
                ),
            ).fetchone()
        return _pair_loss_payload(row)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists revenue_audit_runs (
                    run_id text primary key,
                    ticker text not null,
                    audit_date text not null,
                    method_version text not null,
                    config_fingerprint text not null,
                    status text not null,
                    started_at text not null,
                    completed_at text,
                    payload_json text not null,
                    unique(ticker, audit_date, method_version, config_fingerprint)
                );

                create table if not exists revenue_audit_results (
                    result_id text primary key,
                    run_id text not null,
                    trading_record_id text not null,
                    ticker text not null,
                    source_message_id text not null,
                    audit_date text not null,
                    basis text not null,
                    status text not null,
                    side text not null,
                    size_bucket text not null,
                    decision_source text not null,
                    trigger_policy text,
                    intent_generated_at text not null,
                    notional_usd real,
                    simulated_pnl_usd real,
                    simulated_return_pct real,
                    method_version text not null,
                    config_fingerprint text not null,
                    payload_json text not null,
                    created_at text not null,
                    updated_at text not null,
                    unique(trading_record_id, basis, method_version, config_fingerprint)
                );

                create index if not exists revenue_audit_results_scope_idx
                    on revenue_audit_results(
                        ticker, basis, audit_date desc, method_version,
                        config_fingerprint, intent_generated_at desc, result_id desc
                    );
                create index if not exists revenue_audit_results_record_idx
                    on revenue_audit_results(
                        ticker, trading_record_id, method_version, config_fingerprint
                    );
                create index if not exists revenue_audit_runs_scope_idx
                    on revenue_audit_runs(
                        ticker, audit_date desc, method_version, config_fingerprint, started_at desc
                    );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode = wal")
        conn.execute("pragma foreign_keys = on")
        return conn


def _aggregate_overview(rows: list[RevenueAuditResult], status: str) -> JsonObject:
    audited = [item for item in rows if item.status.value == "audited"]
    auditable = [item for item in rows if item.status.value != "unsupported_action"]
    pnl = sum(item.simulated_pnl_usd or 0.0 for item in audited) if audited else None
    notional = sum(item.notional_usd or 0.0 for item in audited) if audited else None
    return {
        "trade_intent_count": len(rows),
        "auditable_trade_count": len(auditable),
        "audited_trade_count": len(audited),
        "coverage_rate": len(audited) / len(auditable) if auditable else None,
        "simulated_pnl_usd": pnl,
        "simulated_return_pct": (pnl / notional * 100) if pnl is not None and notional else None,
        "win_rate": (
            sum(1 for item in audited if (item.simulated_pnl_usd or 0) > 0) / len(audited)
            if audited
            else None
        ),
        "status": status,
    }


def _aggregate_day(rows: list[RevenueAuditResult]) -> JsonObject:
    overview = _aggregate_overview(rows, RevenueAuditRunStatus.COMPLETED.value)
    return {
        "pnl_usd": overview["simulated_pnl_usd"],
        "return_pct": overview["simulated_return_pct"],
        "trade_intent_count": overview["trade_intent_count"],
        "auditable_trade_count": overview["auditable_trade_count"],
        "audited_trade_count": overview["audited_trade_count"],
        "coverage_rate": overview["coverage_rate"],
        "incomplete": bool(
            overview["auditable_trade_count"]
            and overview["audited_trade_count"] < overview["auditable_trade_count"]
        ),
    }


def _overview_from_sql_rows(
    row: sqlite3.Row | None,
    status_row: sqlite3.Row | None,
) -> JsonObject:
    total = int(row["trade_intent_count"] or 0) if row is not None else 0
    auditable = int(row["auditable_trade_count"] or 0) if row is not None else 0
    audited = int(row["audited_trade_count"] or 0) if row is not None else 0
    pnl = float(row["pnl_usd"]) if row is not None and row["pnl_usd"] is not None else None
    notional = (
        float(row["notional_usd"]) if row is not None and row["notional_usd"] is not None else None
    )
    wins = int(row["win_count"] or 0) if row is not None else 0
    return {
        "trade_intent_count": total,
        "auditable_trade_count": auditable,
        "audited_trade_count": audited,
        "coverage_rate": audited / auditable if auditable else None,
        "simulated_pnl_usd": pnl,
        "simulated_return_pct": pnl / notional * 100 if pnl is not None and notional else None,
        "win_rate": wins / audited if audited else None,
        "status": (
            str(status_row["status"])
            if status_row is not None
            else RevenueAuditRunStatus.NOT_STARTED.value
        ),
    }


def _trend_row(row: sqlite3.Row) -> JsonObject:
    total = int(row["trade_intent_count"] or 0)
    auditable = int(row["auditable_trade_count"] or 0)
    audited = int(row["audited_trade_count"] or 0)
    pnl = float(row["pnl_usd"]) if row["pnl_usd"] is not None else None
    notional = float(row["notional_usd"]) if row["notional_usd"] is not None else None
    return {
        "date": str(row["audit_date"]),
        "pnl_usd": pnl,
        "return_pct": pnl / notional * 100 if pnl is not None and notional else None,
        "trade_intent_count": total,
        "auditable_trade_count": auditable,
        "audited_trade_count": audited,
        "coverage_rate": audited / auditable if auditable else None,
        "incomplete": bool(auditable and audited < auditable),
    }


def _latency_losses_from_rows(rows: list[RevenueAuditResult]) -> JsonObject:
    by_record = {(item.trading_record_id, item.basis): item for item in rows}

    def pair(left: RevenueBasis, right: RevenueBasis) -> JsonObject:
        pairs: list[tuple[RevenueAuditResult, RevenueAuditResult]] = []
        record_ids = {item.trading_record_id for item in rows}
        for record_id in record_ids:
            l_item = by_record.get((record_id, left))
            r_item = by_record.get((record_id, right))
            if (
                l_item is not None
                and r_item is not None
                and l_item.status.value == "audited"
                and r_item.status.value == "audited"
            ):
                pairs.append((l_item, r_item))
        if not pairs:
            return {"matched_trade_count": 0, "pnl_usd": None, "return_pct_points": None}
        left_pnl = sum(item.simulated_pnl_usd or 0.0 for item, _ in pairs)
        right_pnl = sum(item.simulated_pnl_usd or 0.0 for _, item in pairs)
        left_notional = sum(item.notional_usd or 0.0 for item, _ in pairs)
        right_notional = sum(item.notional_usd or 0.0 for _, item in pairs)
        return {
            "matched_trade_count": len(pairs),
            "pnl_usd": left_pnl - right_pnl,
            "return_pct_points": (
                left_pnl / left_notional * 100 - right_pnl / right_notional * 100
                if left_notional and right_notional
                else None
            ),
        }

    return {
        "capture_loss": pair(RevenueBasis.IDEAL_SIGNAL, RevenueBasis.MESSAGE_BUS),
        "decision_loss": pair(RevenueBasis.MESSAGE_BUS, RevenueBasis.SYSTEM_EXECUTABLE),
        "total_latency_loss": pair(RevenueBasis.IDEAL_SIGNAL, RevenueBasis.SYSTEM_EXECUTABLE),
    }


def _pair_loss_payload(row: sqlite3.Row | None) -> JsonObject:
    if row is None or not row["matched_trade_count"]:
        return {"matched_trade_count": 0, "pnl_usd": None, "return_pct_points": None}
    left_notional = float(row["left_notional"] or 0)
    right_notional = float(row["right_notional"] or 0)
    left_pnl = float(row["left_pnl"] or 0)
    right_pnl = float(row["right_pnl"] or 0)
    return {
        "matched_trade_count": int(row["matched_trade_count"]),
        "pnl_usd": float(row["pnl_usd"] or 0),
        "return_pct_points": (
            left_pnl / left_notional * 100 - right_pnl / right_notional * 100
            if left_notional and right_notional
            else None
        ),
    }


def _range_params(
    ticker: str,
    basis: RevenueBasis,
    date_from: date,
    date_to: date,
    method_version: str,
    config_fingerprint: str,
) -> tuple[object, ...]:
    return (
        _ticker(ticker),
        basis.value,
        date_from.isoformat(),
        date_to.isoformat(),
        method_version,
        config_fingerprint,
    )


def _run_from_row(row: sqlite3.Row) -> RevenueAuditRun:
    return RevenueAuditRun.model_validate_json(str(row["payload_json"]))


def _result_from_row(row: sqlite3.Row) -> RevenueAuditResult:
    return RevenueAuditResult.model_validate_json(str(row["payload_json"]))


def _cursor_for(item: RevenueAuditResult) -> str:
    raw = canonical_json([item.intent_generated_at.isoformat(), item.result_id])
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or len(payload) != 2:
        return None
    return str(payload[0]), str(payload[1])


def _ticker(value: str) -> str:
    return value.strip().upper()
