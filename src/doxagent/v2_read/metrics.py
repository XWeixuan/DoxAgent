"""Revision-aware metric reductions with Decimal and cross-day identities."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from doxagent.api_v2.dto import available, coverage, missing

from .repository import ReadStore


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def compare(
    current: Decimal | None, previous: Decimal | None, *, applicable: bool
) -> dict[str, Any]:
    if not applicable:
        return missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE")
    if current is None or previous is None:
        return missing("NOT_RECORDED")
    if previous == 0:
        return missing("PREVIOUS_ZERO", "NOT_APPLICABLE")
    return available(decimal_text((current - previous) / previous * 100))


class Metrics:
    def __init__(self, store: ReadStore) -> None:
        self.store = store
        self._values = {}

    def prime(self, names, tickers, seq, windows, *, distinct=()):
        """One dimensional reduction for all cards and selected ticker rows."""
        names = set(names)
        names |= {name + suffix for name in list(names) for suffix in ("_samples", "_provisional")}
        windows = list(dict.fromkeys(None if days is None else tuple(days) for days in windows))
        wanted_days = set(day for days in windows if days is not None for day in days)
        if None in windows:
            wanted_days.add("*")
        if not tickers:
            return
        where = "metric IN (SELECT value FROM json_each(?)) AND ticker IN (SELECT value FROM json_each(?)) AND day IN (SELECT value FROM json_each(?)) AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
        import json
        with self.store.connect() as db:
            rows = list(db.execute("SELECT metric,ticker,day,value FROM " + self.store.metric_table(seq) + " WHERE " + where,
                                   (json.dumps(sorted(names)), json.dumps(tickers), json.dumps(sorted(wanted_days)), seq, seq)))
            distinct_rows = list(db.execute(
                "SELECT metric,ticker,day,coalesce(json_extract(dimensions,'$.business_key'),entity) FROM " + self.store.metric_table(seq,"contributions") + " WHERE metric IN (SELECT value FROM json_each(?)) AND ticker IN (SELECT value FROM json_each(?)) AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) AND CAST(value AS NUMERIC)!=0" +
                ("" if None in windows else " AND day IN (SELECT value FROM json_each(?))"),
                (json.dumps(list(distinct)), json.dumps(tickers), seq, seq, *([] if None in windows else [json.dumps(sorted(wanted_days))])))) if distinct else []
        for window in windows:
            selected_days = set(window) if window is not None else {"*"}
            totals = {}
            for metric,ticker,day,value in rows:
                if day in selected_days:
                    key = (metric,ticker)
                    totals[key] = totals.get(key,Decimal(0)) + Decimal(value)
            identities = {}
            for metric,ticker,day,identity in distinct_rows:
                if window is None or day in selected_days:
                    identities.setdefault((metric,ticker),set()).add(identity)
            for selected in ([ticker] for ticker in tickers):
                for name in names:
                    self._values[(name,tuple(selected),seq,window,False,None)] = totals.get((name,selected[0]),Decimal(0))
                for name in distinct:
                    self._values[(name,tuple(selected),seq,window,True,None)] = Decimal(len(identities.get((name,selected[0]),())))
            for name in names:
                self._values[(name,tuple(tickers),seq,window,False,None)] = sum((totals.get((name,ticker),Decimal(0)) for ticker in tickers),Decimal(0))
            for name in distinct:
                self._values[(name,tuple(tickers),seq,window,True,None)] = Decimal(sum(len(identities.get((name,ticker),())) for ticker in tickers))

    def complete(self, identity, seq, days):
        if not days:
            return False  # ALL includes unproven historical data and has no prior window.
        from datetime import date, datetime, timedelta

        from doxagent.semantic_clock import boundary

        requirements = {
            "messages": {"bus": {"standard_messages", "stream_items", "stream_members"}},
            "trade_triggered": {"runtime": {"runtime_v2_trade_records", "runtime_values"}},
            "trade_executed": {"runtime": {"te_fills", "te_executions"}},
            "processed_cases": {"runtime": {"runtime_v2_cases"}},
            "policy_hits": {"runtime": {"runtime_v2_cases", "runtime_v2_w3_cases"}},
            "policy_ar_hits": {"runtime": {"runtime_v2_cases", "runtime_v2_w3_cases"}},
        }.get(identity)
        if not requirements:
            return False
        import json

        start = boundary(date.fromisoformat(min(days)))
        end = boundary(date.fromisoformat(max(days)) + timedelta(days=1))
        for source, tables in requirements.items():
            value = self.store.get("capture_coverage", "", source, seq)
            if (
                not value
                or not (value["complete"] or value.get("closed_end_at"))
                or not tables <= set(json.loads(value["tables_json"]))
            ):
                return False
            if (
                datetime.fromisoformat(value["started_at"]) > start
                or datetime.fromisoformat(value.get("closed_end_at") or value["end_at"]) < end
            ):
                return False
        return True

    def value(
        self,
        metric: str,
        tickers: list[str],
        seq: int,
        *,
        days: list[str] | None,
        distinct: bool = False,
        dimensions: str | None = None,
    ) -> Decimal:
        key = (metric,tuple(tickers),seq,None if days is None else tuple(days),distinct,dimensions)
        if key in self._values:
            return self._values[key]
        where = ["metric=?", "valid_from<=?", "(valid_to IS NULL OR valid_to>?)"]
        parameters: list[Any] = [metric, seq, seq]
        where.append("ticker IN (" + ",".join("?" for _ in tickers) + ")" if tickers else "0")
        parameters.extend(tickers)
        if days is not None:
            where.append("day IN (" + ",".join("?" for _ in days) + ")" if days else "0")
            parameters.extend(days)
        elif not distinct:
            where.append("day='*'")
        if dimensions is not None:
            where.append("dimensions=?")
            parameters.append(dimensions)
        with self.store.connect() as db:
            if distinct:
                where.append("CAST(value AS NUMERIC) != 0")
                row = db.execute(
                    "SELECT COUNT(*) FROM (SELECT DISTINCT ticker,"
                    "coalesce(json_extract(dimensions,'$.business_key'),entity) "
                    "FROM " + self.store.metric_table(seq,"contributions") + " WHERE " + " AND ".join(where) + ")",
                    parameters,
                ).fetchone()
                return Decimal(row[0])
            # Exact Decimal summation; no SQLite binary-floating-point money conversion.
            return sum(
                (
                    Decimal(r[0])
                    for r in db.execute(
                        "SELECT value FROM " + self.store.metric_table(seq) + " WHERE " + " AND ".join(where), parameters
                    )
                ),
                Decimal(0),
            )

    def metric(
        self,
        identity: str,
        tickers: list[str],
        seq: int,
        *,
        days: list[str] | None,
        previous_days: list[str] | None = None,
        unit: str = "COUNT",
        distinct: bool = False,
        complete: bool = False,
        previous_complete: bool = False,
    ) -> dict[str, Any]:
        complete = complete or self.complete(identity, seq, days)
        previous_complete = previous_complete or self.complete(identity, seq, previous_days)
        current = self.value(identity, tickers, seq, days=days, distinct=distinct)
        previous = (
            self.value(identity, tickers, seq, days=previous_days, distinct=distinct)
            if previous_days
            else None
        )
        observed = self.value(identity + "_samples", tickers, seq, days=days) > 0
        provisional = self.value(identity + "_provisional", tickers, seq, days=days) > 0
        return {
            "metric_id": identity,
            "unit": unit,
            "current": available(decimal_text(current))
            if complete or current or observed
            else missing(),
            "previous": available(decimal_text(previous))
            if previous is not None and previous_complete
            else missing(),
            "change_pct": compare(current, previous, applicable=complete and previous_complete),
            "current_coverage": coverage(complete=complete),
            "previous_coverage": coverage(complete=previous_complete) if previous_days else None,
            "provisional": provisional,
        }

    def quotient(
        self,
        identity: str,
        numerator: str,
        denominator: str,
        tickers: list[str],
        seq: int,
        *,
        days: list[str] | None,
        previous_days: list[str] | None,
        unit: str,
        comparison_applicable: bool = False,
    ) -> dict[str, Any]:
        def calculate(window: list[str] | None) -> Decimal | None:
            divisor = self.value(denominator, tickers, seq, days=window)
            return self.value(numerator, tickers, seq, days=window) / divisor if divisor else None

        current = calculate(days)
        previous = calculate(previous_days) if previous_days else None
        return {
            "metric_id": identity,
            "unit": unit,
            "current": available(decimal_text(current)) if current is not None else missing(),
            "previous": available(decimal_text(previous)) if previous is not None else missing(),
            "change_pct": compare(current, previous, applicable=comparison_applicable),
            "current_coverage": coverage(),
            "previous_coverage": coverage() if previous_days else None,
            "provisional": False,
        }
