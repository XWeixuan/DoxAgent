"""Explicit quote capture followed by SQLite-only frozen report reads."""

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from doxagent.trade_execution.sessions import Sessions
from doxagent.v2_read.calendar import CalendarCache, PageCalendar

from .ledger import ZERO, dec, state_at, unrealized
from .market import IbQuotes
from .projection import account_key, activation

PERIODS = (
    "CURRENT_TRADING_DAY",
    "PREVIOUS_TRADING_DAY",
    "TRADING_DAYS_7",
    "TRADING_DAYS_30",
    "ALL",
)
MODES = {"ACTUAL", "ALL", "UNEXECUTED"}


def total(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values, ZERO)


def number(value):
    return format(value, "f") if value is not None else None


class RevenueService:
    def __init__(self, store, journal=None, market=None, clock=None, balance_provider=None):
        self.store, self.journal = store, journal
        self.market = market or IbQuotes()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.calendar = PageCalendar()
        self._evaluation_lock = threading.Lock()
        self.balance_provider = balance_provider

    @staticmethod
    def expected_exit(record, frame):
        if "_sessions" not in frame:
            frame["_sessions"] = Sessions(
                CalendarCache(
                    {
                        ("calendar_overrides", key): value
                        for key, value in frame.get("calendar_overrides", {}).items()
                    }
                )
            )
        return (
            frame["_sessions"]
            .exit_at(
                datetime.fromisoformat(record["intent_at"]), record.get("exit_offset_minutes", 30)
            )
            .astimezone(UTC)
            .isoformat()
        )

    def objects(self, kind, ticker, seq=None, parent=None):
        # Only compact summaries/caches, never native execution history in report reads.
        with self.store.connect() as db:
            where, params = "kind=? AND ticker=?", [kind, ticker]
            if parent is not None:
                where += " AND parent=?"
                params.append(parent)
            if seq is None:
                query = "SELECT payload FROM object_current WHERE " + where
            else:
                from doxagent.v2_read.serving_schema import history_relation

                query = (
                    "SELECT payload FROM object_current WHERE "
                    + where
                    + " AND valid_from<=? UNION ALL SELECT payload FROM "
                    + history_relation(db)
                    + " WHERE "
                    + where
                    + " AND valid_from<=? AND valid_to>?"
                )
                params = [*params, seq, *params, seq, seq]
            return [json.loads(r[0]) for r in db.execute(query, params)]

    def window(self, period, frame):
        if period not in PERIODS:
            raise ValueError("INVALID_PERIOD")
        calendar = (
            PageCalendar(
                CalendarCache(
                    {
                        ("calendar_overrides", key): value
                        for key, value in frame.get("calendar_overrides", {}).items()
                    }
                )
            )
            if frame.get("calendar_overrides")
            else self.calendar
        )
        window = calendar.period(
            period,
            datetime.fromisoformat(frame["as_of"]),
            first_at=datetime.fromisoformat(frame["since"]),
            semantic=period == "CURRENT_TRADING_DAY",
        )["current"]
        start = max(
            datetime.fromisoformat(window["start_at"]), datetime.fromisoformat(frame["since"])
        )
        end = min(datetime.fromisoformat(window["end_at"]), datetime.fromisoformat(frame["as_of"]))
        # Before launch has no records; keep a zero-length, valid window.
        end = max(start, end)
        return start.isoformat(), end.isoformat()

    @staticmethod
    def mark_id(asset, at):
        return hashlib.sha256(json.dumps([asset, at]).encode()).hexdigest()

    def quotes(self, requests):
        """Historical BID/ASK is immutable and shared by asset/causal second."""
        normalized, cached, fresh = {}, {}, set()
        for asset, at, historical in requests:
            key = (
                (
                    asset,
                    datetime.fromisoformat(at).astimezone(UTC).replace(microsecond=0).isoformat(),
                    True,
                )
                if historical
                else (asset, at, False)
            )
            normalized[(asset, at, historical)] = key
            saved = (
                self.store.get("revenue_historical_quote", "", self.mark_id(asset, key[1]))
                if historical
                else None
            )
            if saved:
                cached[key] = saved
            else:
                fresh.add(key)
        fetched = self.market.batch(sorted(fresh)) if fresh else {}
        saved = []
        for (asset, at, historical), value in fetched.items():
            if historical and value and (value.get("bid") or value.get("ask")):
                saved.append(
                    dict(
                        kind="revenue_historical_quote",
                        ticker="",
                        id=self.mark_id(asset, at),
                        parent=asset,
                        sort=at,
                        data=value,
                    )
                )
        if saved:
            self.store.ingest("revenue_quote_cache", uuid4().hex, saved)
        cached.update(fetched)
        return {original: cached.get(key) for original, key in normalized.items()}

    def mark(self, asset, at, frame):
        if at == frame["as_of"]:
            return frame["marks"].get(asset)
        if "_marks" in frame:
            return frame["_marks"].get((asset, at))
        return self.store.get("revenue_mark", "", self.mark_id(asset, at))

    def records(self, frame, environment):
        if environment not in {"LIVE", "PAPER"}:
            raise ValueError("INVALID_ENVIRONMENT")
        key = frame["accounts"].get(environment)
        return [
            r
            for r in self.objects("revenue_record", environment, frame["seq"])
            if r["account_key"] in {key, account_key(None)}
        ]

    def capture(self, owner):
        enabled = activation(self.store)
        if not enabled:
            raise ValueError("REVENUE_NOT_ACTIVATED")
        now = self.clock()
        with self.store.connect() as db:
            seq = self.store.highwater(db)
        accounts = {env: value["account_key"] for env, value in enabled["opening"].items()}
        overrides = {}
        balances = {}
        if self.journal:
            from doxagent.trade_execution.capital import CapitalLedger
            from doxagent.trade_execution.configuration import account_pin
            from doxagent.trade_execution.repository import ExecutionRepository

            ledger = CapitalLedger(ExecutionRepository(self.journal, initialize=False))
            with self.journal.transaction(write=False) as db:
                overrides = {
                    r[0]: json.loads(r[1])
                    for r in db.execute(
                        "SELECT key,payload FROM runtime_values "
                        "WHERE namespace='calendar_overrides'"
                    )
                }
                for env in ("LIVE", "PAPER"):
                    pin = account_pin(db, env)
                    if pin:
                        account = pin["profile"]["expected_account_id"]
                        accounts[env] = account_key(account)
                        lots = [
                            json.loads(r[0])
                            for r in db.execute(
                                "SELECT payload FROM te_lots WHERE account=?", (account,)
                            )
                        ]
                        positions = [
                            dict(
                                asset=lot["ticker"], qty=str(lot["remaining_qty"]), sign=lot["sign"]
                            )
                            for lot in lots
                            if dec(lot["remaining_qty"])
                        ]
                        funded = db.execute(
                            "SELECT 1 FROM te_capital_events "
                            "WHERE account=? AND kind='ALLOCATION' LIMIT 1",
                            (account,),
                        ).fetchone()
                        book = (
                            ledger.book(account, db=db)
                            if funded
                            else dict(cash=ZERO, provisional=bool(positions))
                        )
                        balances[env] = dict(
                            cash=number(book["cash"]) if not book["provisional"] else None,
                            positions=positions,
                        )
        frame = dict(
            seq=seq,
            as_of=now.isoformat(),
            since=enabled["since"],
            accounts=accounts,
            opening=enabled["opening"],
            marks={},
            calendar_overrides=overrides,
            balances=balances,
        )
        if self.balance_provider:
            frame["balances"] = self.balance_provider(frame)
        records = self.records(frame, "PAPER") + self.records(frame, "LIVE")
        requests = set()
        for balance in frame["balances"].values():
            requests.update(
                (position["asset"], frame["as_of"], False) for position in balance["positions"]
            )
        for record in records:
            for leg in record["legs"]:
                if dec(leg["qty"]) > 0:
                    requests.add((leg["asset"], frame["as_of"], False))
        # Capture just the necessary period boundaries, never dense historical bars.
        for period in PERIODS:
            for at in self.window(period, frame):
                if at in {frame["as_of"], frame["since"]}:
                    continue
                for record in records:
                    for leg in record["legs"]:
                        cached = self.mark(leg["asset"], at, frame)
                        side = "bid" if leg["sign"] == 1 else "ask"
                        if dec(state_at(leg, at)["qty"]) and not (cached or {}).get(side):
                            requests.add((leg["asset"], at, True))
        quotes = self.quotes(requests)
        frame["as_of"] = self.clock().astimezone(UTC).isoformat()
        persisted = []
        for (asset, at, historical), quote in quotes.items():
            if quote is None:
                continue
            if not historical:
                at = frame["as_of"]
                frame["marks"][asset] = quote
            persisted.append(
                dict(
                    kind="revenue_mark",
                    ticker="",
                    id=self.mark_id(asset, at),
                    parent=asset,
                    sort=at,
                    data={**quote, "observed_for": at, "asset": asset},
                )
            )
        # An observed historical point helps the curve without a background sampler.
        if persisted:
            self.store.ingest("revenue_mark", uuid4().hex, persisted)
        token = self.store.save_token(owner, "revenue", frame, view=True)
        return dict(frame_id=token, as_of=frame["as_of"], since=frame["since"])

    def frame(self, owner, identity):
        from doxagent.api_v2.errors import ApiFailure

        try:
            return self.store.token(identity, owner, view=True, scope="revenue")
        except TimeoutError as exc:
            raise ApiFailure("VIEW_EXPIRED", 410) from exc
        except ValueError as exc:
            raise ApiFailure("VALIDATION_FAILED", 422) from exc

    def upl(self, record, at, frame):
        if record["kind"] != "ACTUAL":
            return ZERO
        if not record["legs"]:
            return None
        return total(
            unrealized(leg, at, self.mark(leg["asset"], at, frame)) for leg in record["legs"]
        )

    @staticmethod
    def realized(record, start, end):
        if record["kind"] != "ACTUAL":
            return ZERO
        if not record["legs"]:
            return None
        result = ZERO
        for leg in record["legs"]:
            points = [
                p
                for p in leg["timeline"]
                if start <= p["at"] < end and p["kind"] in {"EXIT", "FIFO_OFFSET"}
            ]
            if points and (not leg["basis_known"] or any(not p["realized_known"] for p in points)):
                return None
            result += sum((dec(p["delta"]) for p in points), ZERO)
        return result

    def actual_net(self, record, start, end, frame):
        r, opening, closing = (
            self.realized(record, start, end),
            self.upl(record, start, frame),
            self.upl(record, end, frame),
        )
        return None if None in (r, opening, closing) else r + closing - opening

    def hypothesis(self, record, frame=None):
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    1,
                    record["id"],
                    record["intent_at"],
                    record["direction"],
                    record.get("main_cap"),
                    record.get("exit_offset_minutes", 30),
                ]
            ).encode()
        ).hexdigest()
        return fingerprint, frame["_hypotheses"].get(
            fingerprint
        ) if frame is not None and "_hypotheses" in frame else self.store.get(
            "revenue_hypothesis", record["environment"], fingerprint
        )

    def evaluate(self, owner, frame_id, environment, identities):
        # Serialize the small historical batches so fast filter changes reuse
        # completed cache entries instead of duplicating IB historical requests.
        with self._evaluation_lock:
            return self._evaluate(owner, frame_id, environment, identities)

    def _evaluate(self, owner, frame_id, environment, identities):
        frame = self.frame(owner, frame_id)
        if (
            not isinstance(identities, list)
            or len(identities) > 12
            or any(not isinstance(i, str) for i in identities)
        ):
            raise ValueError("INVALID_RECORD_IDS")
        if environment not in {"LIVE", "PAPER"}:
            raise ValueError("INVALID_ENVIRONMENT")
        allowed = {
            identity: self.store.get("revenue_record", environment, identity, frame["seq"])
            for identity in dict.fromkeys(identities)
        }
        selected, requests = [], set()
        for identity in dict.fromkeys(identities):
            record = allowed.get(identity)
            if not record or record["kind"] != "UNEXECUTED":
                continue
            if record["account_key"] not in {frame["accounts"].get(environment), account_key(None)}:
                continue
            # A late fill observed after the frame invalidates hypothetical eligibility.
            current = self.store.get("revenue_record", environment, identity)
            if current and current["kind"] != "UNEXECUTED":
                continue
            fingerprint, cached = self.hypothesis(record)
            if cached and cached["evaluated_for"] == frame_id:
                continue
            if cached and cached["state"] == "PRICED":
                continue
            exit_at = self.expected_exit(record, frame)
            selected.append((fingerprint, record, exit_at))
            if exit_at < frame["as_of"]:
                requests.update(
                    (record["ticker"], at, True) for at in (record["intent_at"], exit_at)
                )
        quotes = self.quotes(requests)
        output = []
        for fingerprint, record, exit_at in selected:
            entry = quotes.get((record["ticker"], record["intent_at"], True))
            exit_quote = quotes.get((record["ticker"], exit_at, True))
            ep = (entry or {}).get("ask" if record["direction"] == "LONG" else "bid")
            xp = (exit_quote or {}).get("bid" if record["direction"] == "LONG" else "ask")
            roi = (
                ((dec(xp) - dec(ep)) / dec(ep)) * (1 if record["direction"] == "LONG" else -1)
                if ep and xp
                else None
            )
            amount = (
                roi * dec(record["main_cap"])
                if roi is not None and record.get("main_cap")
                else None
            )
            data = dict(
                state="PRICED"
                if roi is not None
                else "WAIT_EXIT"
                if exit_at >= frame["as_of"]
                else "UNPRICEABLE",
                entry_at=record["intent_at"],
                exit_at=exit_at,
                entry_price=ep,
                exit_price=xp,
                roi=number(roi),
                amount=number(amount),
                evaluated_for=frame_id,
            )
            output.append(
                dict(
                    kind="revenue_hypothesis",
                    ticker=environment,
                    id=fingerprint,
                    parent=record["id"],
                    data=data,
                )
            )
        if output:
            self.store.ingest("revenue_hypothesis", uuid4().hex, output)
        return dict(evaluated=len(output))

    def row(self, record, start, end, frame, include_hypothesis):
        public = {
            k: record[k]
            for k in (
                "id",
                "case_id",
                "ticker",
                "direction",
                "environment",
                "title",
                "intent_at",
                "kind",
                "completed",
                "reason",
                "status",
            )
        }
        legs = []
        for leg in record["legs"]:
            amount = unrealized(leg, frame["as_of"], self.mark(leg["asset"], frame["as_of"], frame))
            state = state_at(leg, frame["as_of"])
            cost = dec(state["cost"])
            legs.append(
                {k: v for k, v in leg.items() if k not in {"timeline", "known", "basis_known"}}
                | dict(
                    holding_price=number(cost / dec(state["qty"])) if dec(state["qty"]) else None,
                    upl=number(amount),
                    roi=number(amount / cost) if amount is not None and cost else None,
                )
            )
        net = self.actual_net(record, start, end, frame) if record["kind"] == "ACTUAL" else None
        capitals = frame.get("_capital")
        if capitals is None:
            capitals = self.objects(
                "revenue_capital",
                "",
                frame["seq"],
                frame["accounts"].get(record["environment"], "missing"),
            )
        attributed = [
            c
            for c in capitals
            if c["kind"] == "ADJUSTMENT"
            and c.get("execution_id") == record["id"]
            and start <= c["at"] < end
        ]
        if net is not None:
            net += sum((dec(c["amount"]) for c in attributed), ZERO)
        fees = total(
            dec(leg_item["fees"]) if leg_item["fees"] is not None else None for leg_item in legs
        )
        if fees is not None:
            fees += sum((-dec(c["amount"]) for c in attributed if c.get("fee_kind")), ZERO)
        notional = sum((dec(leg_item["notional"]) for leg_item in legs), ZERO)
        hypothesis = (
            self.hypothesis(record, frame)[1]
            if include_hypothesis and record["kind"] == "UNEXECUTED"
            else None
        )
        # The frozen report never continues counting a result known to have an actual fill.
        latest = None
        if hypothesis:
            if "_actual_now" in frame:
                latest = {"kind": "ACTUAL"} if record["id"] in frame["_actual_now"] else None
            else:
                latest = self.store.get("revenue_record", record["environment"], record["id"])
        if latest and latest["kind"] == "ACTUAL":
            hypothesis = None
        if hypothesis and hypothesis["exit_at"] < start:
            hypothesis = None
        if hypothesis and hypothesis["exit_at"] >= end:
            hypothesis = {
                **hypothesis,
                "state": "WAIT_EXIT",
                "exit_price": None,
                "roi": None,
                "amount": None,
            }
        public.update(
            fees=number(fees),
            legs=legs,
            net=number(net),
            roi=number(net / notional) if net is not None and notional else None,
            upl=number(self.upl(record, frame["as_of"], frame)),
            hypothesis=hypothesis,
        )
        return public

    def report(self, owner, frame_id, environment, period="ALL", mode="ACTUAL", offset=0, limit=12):
        if mode not in MODES or not 0 <= offset or not 1 <= limit <= 12:
            raise ValueError("INVALID_REVENUE_QUERY")
        frame = self.frame(owner, frame_id)
        start, end = self.window(period, frame)
        marks = self.objects("revenue_mark", "")
        frame["_marks"] = {
            (q["asset"], q["observed_for"]): q
            for q in marks
            if q.get("asset") and q.get("observed_for", "") <= frame["as_of"]
        }
        frame["_capital"] = self.objects(
            "revenue_capital", "", frame["seq"], frame["accounts"].get(environment, "missing")
        )
        if mode != "ACTUAL":
            with self.store.connect() as db:
                frame["_hypotheses"] = {
                    r[0]: json.loads(r[1])
                    for r in db.execute(
                        "SELECT id,payload FROM object_current "
                        "WHERE kind='revenue_hypothesis' AND ticker=?",
                        (environment,),
                    )
                }
                frame["_actual_now"] = {
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM object_current WHERE kind='revenue_record' "
                        "AND ticker=? AND json_extract(payload,'$.kind')='ACTUAL'",
                        (environment,),
                    )
                }
        records = self.records(frame, environment)
        actual = [r for r in records if r["kind"] == "ACTUAL"]

        def in_period(r):
            if start <= r.get("recorded_at", r["intent_at"]) < end:
                return True
            if (
                mode != "ACTUAL"
                and r["kind"] == "UNEXECUTED"
                and start <= self.expected_exit(r, frame) < end
            ):
                return True
            return start <= r["intent_at"] < end or any(
                start <= p["at"] < end
                or (
                    p["at"] < end
                    and dec(p["qty"]) > 0
                    and (
                        not r["completed"]
                        or any(
                            q["at"] >= start for leg_item in r["legs"] for q in leg_item["timeline"]
                        )
                    )
                )
                for leg in r["legs"]
                for p in leg["timeline"]
            )

        period_records = [r for r in records if in_period(r)]
        selected = [
            r
            for r in period_records
            if mode == "ALL"
            or r["kind"] == mode
            or (mode == "UNEXECUTED" and r["kind"] == "PENDING")
        ]
        selected.sort(key=lambda r: (r["intent_at"], r["id"]), reverse=True)
        rows = [
            self.row(r, start, end, frame, mode != "ACTUAL")
            for r in selected[offset : offset + limit]
        ]
        holdings = [
            self.row(r, start, end, frame, False)
            for r in actual
            if any(dec(leg_item["qty"]) > 0 for leg_item in r["legs"])
        ]
        realized = total(self.realized(r, start, end) for r in actual)
        net = total(self.actual_net(r, start, end, frame) for r in actual)
        upl = total(self.upl(r, frame["as_of"], frame) for r in actual)
        capitals = frame["_capital"]
        charges = [c for c in capitals if c["kind"] == "ADJUSTMENT" and start <= c["at"] < end]
        extra = sum((dec(c["amount"]) for c in charges), ZERO)
        realized = realized + extra if realized is not None else None
        net = net + extra if net is not None else None
        # Fee timing follows each actual allocation, including entry fees of open positions.
        fee_values = []
        for r in actual:
            for leg in r["legs"]:
                # Derive charged fees from the change in realized+remaining net entry fees.
                if any(start <= p["at"] < end for p in leg["timeline"]):
                    # Per-fill fees are retained explicitly by the ledger below.
                    fee_values.extend(
                        dec(p["fee"]) if p.get("fee") is not None else None
                        for p in leg["timeline"]
                        if start <= p["at"] < end
                    )
        fees = total(fee_values)
        fees = (
            fees + sum((-dec(c["amount"]) for c in charges if c.get("fee_kind")), ZERO)
            if fees is not None
            else None
        )
        complete = [
            r
            for r in period_records
            if r["kind"] == "ACTUAL"
            and r["completed"]
            and start
            <= max(
                (p["at"] for leg_item in r["legs"] for p in leg_item["timeline"]),
                default=r["intent_at"],
            )
            < end
        ]
        results = [
            total(
                dec(leg_item["realized"]) if leg_item["realized"] is not None else None
                for leg_item in r["legs"]
            )
            for r in complete
        ]
        results = [
            value
            + sum(
                (
                    dec(c["amount"])
                    for c in capitals
                    if c["kind"] == "ADJUSTMENT"
                    and c.get("execution_id") == r["id"]
                    and c["at"] < end
                ),
                ZERO,
            )
            if value is not None
            else None
            for r, value in zip(complete, results, strict=True)
        ]
        hypothesis_events, pending_hypotheses = [], []
        hypothesis_total = 0
        if mode != "ACTUAL":
            for r in period_records:
                if r["kind"] != "UNEXECUTED":
                    continue
                if r["id"] in frame["_actual_now"]:
                    continue
                h = self.hypothesis(r, frame)[1]
                if start <= self.expected_exit(r, frame) < end:
                    hypothesis_total += 1
                if self.expected_exit(r, frame) >= start and (
                    not h or (h["state"] != "PRICED" and h["evaluated_for"] != frame_id)
                ):
                    pending_hypotheses.append(r)
                if h and h["state"] == "PRICED" and start <= h["exit_at"] < end:
                    hypothesis_events.append(h)
            results = (results if mode == "ALL" else []) + [
                dec(h["roi"]) for h in hypothesis_events
            ]
        hypo_net = sum(
            (dec(h["amount"]) for h in hypothesis_events if h["amount"] is not None), ZERO
        )
        known_hypo_usd = sum(h["amount"] is not None for h in hypothesis_events)
        shown_net = (
            net
            if mode == "ACTUAL"
            else hypo_net
            if mode == "UNEXECUTED"
            else net + hypo_net
            if net is not None
            else None
        )
        opening_account = frame["opening"].get(environment, {})
        base = (
            opening_account.get("capital")
            if opening_account.get("account_key") == frame["accounts"].get(environment)
            else "0"
            if frame["accounts"].get(environment)
            else None
        )
        balance = frame.get("balances", {}).get(environment)
        asset_values = [dec(balance["cash"]) if balance and balance["cash"] is not None else None]
        for position in (balance or {}).get("positions", []):
            price = (frame["marks"].get(position["asset"]) or {}).get(
                "bid" if position["sign"] == 1 else "ask"
            )
            asset_values.append(
                dec(price) * dec(position["qty"]) * position["sign"] if price is not None else None
            )
        strategy_value = total(asset_values)
        opening_net = total(self.actual_net(r, frame["since"], start, frame) for r in actual)
        prior_flows = sum(
            (dec(c["amount"]) for c in capitals if c["kind"] == "ALLOCATION" and c["at"] < start),
            ZERO,
        )
        prior_adjust = sum(
            (dec(c["amount"]) for c in capitals if c["kind"] == "ADJUSTMENT" and c["at"] < start),
            ZERO,
        )
        duration = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        weighted = sum(
            (
                dec(c["amount"])
                * dec(
                    (datetime.fromisoformat(end) - datetime.fromisoformat(c["at"])).total_seconds()
                    / duration
                )
                for c in capitals
                if duration and c["kind"] == "ALLOCATION" and start <= c["at"] < end
            ),
            ZERO,
        )
        denominator = (
            dec(base) + opening_net + prior_flows + prior_adjust + weighted
            if base is not None and opening_net is not None
            else None
        )
        roi = (
            shown_net / denominator
            if shown_net is not None and denominator and denominator > 0
            else None
        )
        if mode == "UNEXECUTED":
            # No hypothetical portfolio return without a reliable dollar budget.
            if not known_hypo_usd:
                shown_net = None
                roi = None
        points = {start, end}
        points.update(
            (datetime.fromisoformat(p["at"]) + timedelta(microseconds=1)).isoformat()
            for r in actual
            for leg_item in r["legs"]
            for p in leg_item["timeline"]
            if start <= p["at"] < end
        )
        points.update(h["exit_at"] for h in hypothesis_events)
        points.update(
            (datetime.fromisoformat(c["at"]) + timedelta(microseconds=1)).isoformat()
            for c in charges
        )
        # Saved user observations, not fabricated bars. Cap rendered points by time buckets.
        points.update(
            q["observed_for"]
            for q in marks
            if q.get("observed_for") and start <= q["observed_for"] <= end
        )
        ordered = sorted(points)
        if len(ordered) > 180:
            ordered = [ordered[round(i * (len(ordered) - 1) / 179)] for i in range(180)]
        # Fold realized cash events once; old closed groups need no repeated
        # cost/quote calculation at each point on a long-range curve.
        events = [
            (p["at"], dec(p["delta"]) if leg["basis_known"] and p["realized_known"] else None)
            for r in actual
            for leg in r["legs"]
            for p in leg["timeline"]
            if p["kind"] in {"EXIT", "FIFO_OFFSET"} and start <= p["at"] < end
        ]
        events.extend((c["at"], dec(c["amount"])) for c in charges)
        events.sort(key=lambda item: item[0])
        opening_upl = total(self.upl(r, start, frame) for r in actual)
        intervals = [
            (
                leg,
                leg["timeline"][0]["at"],
                leg["timeline"][-1]["at"] if dec(leg["qty"]) == 0 else None,
            )
            for r in actual
            for leg in r["legs"]
            if leg["timeline"]
        ]
        unknown = any(not r["legs"] for r in actual)
        hypothetical = sorted(
            [(h["exit_at"], dec(h["amount"])) for h in hypothesis_events if h["amount"] is not None]
        )
        event_index, hypo_index = 0, 0
        realized_total, hypo_total = ZERO, ZERO
        curve = []
        for at in ordered:
            while event_index < len(events) and events[event_index][0] < at:
                value = events[event_index][1]
                unknown |= value is None
                realized_total += value or ZERO
                event_index += 1
            while hypo_index < len(hypothetical) and hypothetical[hypo_index][0] <= at:
                hypo_total += hypothetical[hypo_index][1]
                hypo_index += 1
            ar = None if unknown else realized_total
            closing_upl = total(
                unrealized(leg, at, self.mark(leg["asset"], at, frame))
                for leg, first, last in intervals
                if first < at and (last is None or at <= last)
            )
            an = (
                ar + closing_upl - opening_upl
                if ar is not None and closing_upl is not None and opening_upl is not None
                else None
            )
            hn = hypo_total
            value = (
                an
                if mode == "ACTUAL"
                else hn
                if mode == "UNEXECUTED"
                else an + hn
                if an is not None
                else None
            )
            if mode == "UNEXECUTED" and not known_hypo_usd:
                value = None
            curve.append(dict(at=at, net=number(value), realized=number(ar)))
        known = [v for v in results if v is not None]
        pending_hypotheses.sort(key=lambda r: (r["intent_at"], r["id"]), reverse=True)
        return dict(
            as_of=frame["as_of"],
            since=frame["since"],
            start_at=start,
            end_at=end,
            environment=environment,
            mode=mode,
            hypothesis_total=hypothesis_total,
            hypothesis_pending_count=len(pending_hypotheses),
            hypothesis_pending_ids=[r["id"] for r in pending_hypotheses[:12]],
            kpis=dict(
                strategy_value=number(strategy_value),
                net=number(shown_net),
                roi=number(roi),
                realized=number(shown_net if mode == "UNEXECUTED" else realized),
                upl=number(upl),
                fees=number(fees),
                actual_count=sum(r["kind"] == "ACTUAL" for r in period_records),
                intent_count=len(period_records),
                completed_count=len(complete),
                win_rate=number(dec(sum(v > 0 for v in known)) / dec(len(known)))
                if known and len(known) == len(results)
                else None,
                hypothetical_count=len(hypothesis_events),
                hypothetical_usd_count=sum(h["amount"] is not None for h in hypothesis_events),
            ),
            curve=curve,
            holdings=holdings,
            items=rows,
            total=len(selected),
            offset=offset,
            limit=limit,
        )

    def detail(self, owner, frame_id, environment, identity):
        frame = self.frame(owner, frame_id)
        record = next((r for r in self.records(frame, environment) if r["id"] == identity), None)
        if not record:
            raise ValueError("REVENUE_RECORD_NOT_FOUND")
        start, end = self.window("ALL", frame)
        result = self.row(record, start, end, frame, True)
        orders = self.objects("order", record["ticker"], frame["seq"], identity)
        attempts = {
            a["id"]: a
            for a in self.objects("native:te_attempts", record["ticker"], frame["seq"], identity)
        }
        for order in orders:
            attempt = attempts.get(order["order_attempt_id"], {})
            rejection = attempt.get("rejection") or {}
            order["reason"] = (
                attempt.get("cancel_reason")
                or (rejection.get("error") or {}).get("message")
                or attempt.get("error")
            )
        fills = self.objects("fill", record["ticker"], frame["seq"], identity)
        result.update(orders=orders, fills=fills)
        return result
