from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.repository import ReadStore, encode, instant

from .dto import coverage
from .errors import ApiFailure


class Views:
    def __init__(self, store: ReadStore, calendar: PageCalendar) -> None:
        self.store, self.calendar = store, calendar

    def create(
        self,
        owner: str,
        page: str,
        ticker: str | None,
        period: str | None,
        refresh: str = "OPEN",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        if page not in {
            "OVERVIEW",
            "RESEARCH",
            "EXPECTATIONS",
            "POLICIES",
            "EVENTS",
            "MESSAGE_BUS",
            "RUNTIME",
            "COST",
        }:
            raise ApiFailure("VALIDATION_FAILED", 422)
        if (page == "OVERVIEW") != (ticker is None):
            raise ApiFailure("VALIDATION_FAILED", 422)
        if refresh not in {"OPEN", "MANUAL", "MINUTE"} or (
            refresh == "MINUTE" and page not in {"MESSAGE_BUS", "RUNTIME"}
        ):
            raise ApiFailure("VALIDATION_FAILED", 422)
        if page in {"RESEARCH", "EXPECTATIONS"} and period:
            raise ApiFailure("VALIDATION_FAILED", 422)
        if page == "OVERVIEW" and period == "ALL":
            raise ApiFailure("VALIDATION_FAILED", 422)
        with self.store.connect() as db:
            seq = self.store.highwater(db)
            rows = db.execute(
                "SELECT ticker,payload FROM objects WHERE kind='ticker' "
                "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                "ORDER BY ticker",
                (seq, seq),
            ).fetchall()
            tickers = [r[0] for r in rows if not json.loads(r[1])["removed"]]
            if ticker and ticker not in tickers:
                raise ApiFailure("TICKER_NOT_FOUND", 404)
            first = db.execute("SELECT MIN(at) FROM commits").fetchone()[0]
            source_health = db.execute(
                "SELECT error,checked_at FROM source_health WHERE source='runtime'"
            ).fetchone()
            maintenance = None
            if (
                source_health
                and not source_health[0]
                and (now - datetime.fromisoformat(source_health[1])).total_seconds() < 30
            ):
                maintenance = bool(
                    db.execute(
                        "SELECT 1 FROM objects WHERE kind='native:runtime_tasks' AND ticker IN "
                        "(SELECT value FROM json_each(?)) AND valid_from<=? "
                        "AND (valid_to IS NULL OR valid_to>?) "
                        "AND json_extract(payload,'$.inputs.control_epoch') IS NOT NULL "
                        "AND json_extract(payload,'$.kind') IN ('MAINTENANCE','SELECTION') "
                        "AND json_extract(payload,'$.status') IN ('PENDING','RUNNING','RETRY') "
                        "AND julianday(json_extract(payload,'$.due_at'))<=julianday(?) LIMIT 1",
                        (encode([ticker] if ticker else tickers), seq, seq, instant(now)),
                    ).fetchone()
                )
        activation = self.store.get("activation", ticker or "", "active", seq)
        clock = self.calendar.clock(now, maintenance_pending=maintenance)
        selected = period or "PREVIOUS_TRADING_DAY"
        if selected not in {
            "PREVIOUS_TRADING_DAY",
            "CURRENT_TRADING_DAY",
            "TRADING_DAYS_7",
            "TRADING_DAYS_30",
            "ALL",
        }:
            raise ApiFailure("VALIDATION_FAILED", 422)
        window = (
            None
            if page in {"RESEARCH", "EXPECTATIONS"}
            else self.calendar.period(
                selected, now, first_at=datetime.fromisoformat(first) if first else None
            )
        )
        value = {
            "view_id": "pending",
            "expires_at": instant(now + timedelta(days=1)),
            "page": page,
            "ticker": ticker,
            "clock": clock,
            "period_options": [
                {
                    "period": p,
                    "selectable": p != "CURRENT_TRADING_DAY" or clock["is_trading_day"]["value"],
                    "reason": "NON_TRADING_DAY"
                    if p == "CURRENT_TRADING_DAY" and not clock["is_trading_day"]["value"]
                    else None,
                }
                for p in (
                    "PREVIOUS_TRADING_DAY",
                    "CURRENT_TRADING_DAY",
                    "TRADING_DAYS_7",
                    "TRADING_DAYS_30",
                    "ALL",
                )
                if p != "ALL" or page != "OVERVIEW"
            ],
            "period": window,
            "activation": {
                "state": "AVAILABLE" if activation else "NOT_PRODUCED",
                "data": activation,
                "reason": None if activation else "NO_ACTIVE_REVISION",
                "coverage": coverage(),
            },
            "coverage": coverage(),
        }
        snapshot = {
            "seq": seq,
            "wire": value,
            "tickers": tickers if ticker is None else [ticker],
            "as_of": instant(now),
            "freshness": self.store.freshness(now),
        }
        identity = self.store.save_token(
            owner, self.scope(page, ticker), snapshot, view=True, now=now
        )
        value["view_id"] = identity
        return value

    @staticmethod
    def scope(page: str, ticker: str | None) -> str:
        return hashlib.sha256(encode([page, ticker]).encode()).hexdigest()

    def get(self, owner: str, identity: str, ticker: str | None = None) -> dict[str, Any]:
        try:
            value = self.store.token(identity, owner, view=True)
        except TimeoutError:
            raise ApiFailure("VIEW_EXPIRED", 410) from None
        except ValueError:
            raise ApiFailure("INVALID_VIEW", 400) from None
        if ticker is not None and value["wire"]["ticker"] != ticker:
            raise ApiFailure("SCOPE_MISMATCH", 400)
        value["wire"]["view_id"] = identity
        return value

    def page(
        self,
        owner: str,
        kind: str,
        ticker: str,
        *,
        view: dict[str, Any] | None,
        view_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        **filters: Any,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ApiFailure("VALIDATION_FAILED", 422)
        scope = hashlib.sha256(encode([kind, ticker, view_id, filters, limit]).encode()).hexdigest()
        after = None
        if cursor:
            try:
                token = self.store.token(cursor, owner, scope=scope)
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
            seq, after = token["seq"], tuple(token["after"])
        elif view:
            seq = view["seq"]
        else:
            with self.store.connect() as db:
                seq = self.store.highwater(db)
        rows = self.store.page(kind, ticker, seq, limit=limit + 1, after=after, **filters)
        more, rows = len(rows) > limit, rows[:limit]
        bounded = []
        size = 0
        for row in rows:
            size += len(encode(row["data"]).encode("utf-8"))
            if size > 220000:
                if not bounded:
                    raise ApiFailure("RESOURCE_TOO_LARGE", 413)
                more = True
                break
            bounded.append(row)
        rows = bounded
        next_cursor = (
            self.store.save_token(
                owner, scope, {"seq": seq, "after": [rows[-1]["sort"], rows[-1]["id"]]}
            )
            if more
            else None
        )
        return {
            "items": [row["data"] for row in rows],
            "next_cursor": next_cursor,
            "has_more": more,
            "snapshot_id": view_id or str(seq),
            "limit": limit,
        }
