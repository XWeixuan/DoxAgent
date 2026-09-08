"""Bounded graph snapshots and resumable replacement deltas share one read watermark."""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from doxagent.semantic_clock import semantic_day
from doxagent.v2_read.graph import NODES
from doxagent.v2_read.repository import encode, instant

from .dto import available, missing, validate
from .errors import ApiFailure


class Graphs:
    def __init__(self, store, views):
        self.store, self.views = store, views

    def scope(self, ticker, view_id, limit):
        return hashlib.sha256(encode(["graph", ticker, view_id, limit]).encode()).hexdigest()

    def cursor(self, owner, token, ticker, view_id):
        try:
            state = self.store.token(token, owner)
        except TimeoutError:
            raise ApiFailure("CURSOR_EXPIRED", 410) from None
        except ValueError:
            raise ApiFailure("INVALID_CURSOR", 400) from None
        if (
            state.get("kind") != "graph"
            or state.get("ticker") != ticker
            or state.get("view_id") != view_id
        ):
            raise ApiFailure("SCOPE_MISMATCH", 400)
        return state

    @staticmethod
    def days(view):
        period = view["wire"]["period"]
        return None if period["selected"] == "ALL" else period["current"]["trading_days"]

    def counts(self, ticker, seq, days):
        where = [
            "ticker=?",
            "metric LIKE 'graph_%'",
            "valid_from<=?",
            "(valid_to IS NULL OR valid_to>?)",
        ]
        params = [ticker, seq, seq]
        if days is None:
            where.append("day='*'")
        else:
            where.append("day IN (" + ",".join("?" for _ in days) + ")" if days else "0")
            params.extend(days)
        totals = {}
        with self.store.connect() as db:
            for row in db.execute(
                "SELECT metric,dimensions,value FROM metric_buckets WHERE " + " AND ".join(where),
                params,
            ):
                key = (row[0], row[1])
                totals[key] = totals.get(key, Decimal(0)) + Decimal(row[2])

        def value(metric, dimensions):
            return totals.get((metric, encode(dimensions)), Decimal(0))

        nodes = []
        for node in NODES:
            dimensions = {"node": node}
            samples = value("graph_samples", dimensions)
            latest = self.store.page(
                "graph_observation", ticker, seq, parent=node, days=days, limit=1
            )
            at = latest[0]["data"]["latest_at"] if latest else None
            nodes.append(
                validate(
                    "NodeCounts",
                    {
                        "node_id": node,
                        "case_count": int(value("graph_cases", dimensions)),
                        "failed_case_count": int(value("graph_failed", dimensions)),
                        "low_confidence_case_count": int(value("graph_low", dimensions))
                        if node in {"W1", "W2"}
                        else None,
                        "latest_processed_at": available(at) if at else missing(),
                        "average_seconds": available(
                            float(value("graph_seconds", dimensions) / samples)
                        )
                        if samples
                        else missing(),
                        "result_counts": [
                            {
                                "result": result,
                                "case_count": int(
                                    value("graph_results", {"node": node, "result": result})
                                ),
                            }
                            for result in NODES[4:]
                        ]
                        if node == "W3"
                        else [],
                    },
                )
            )
        edges = [
            {
                "edge_id": source + ":" + target,
                "from": source,
                "to": target,
                "case_count": int(count),
            }
            for source in NODES
            for target in NODES
            if (count := value("graph_edges", {"from": source, "to": target}))
        ]
        return nodes, edges

    def paths(self, ticker, seq, days, node):
        clauses = ["m.kind='graph_member'", "m.ticker=?", "m.parent=?",
                   "m.valid_from<=?", "(m.valid_to IS NULL OR m.valid_to>?)",
                   "g.kind='graph_case'", "g.valid_from<=?", "(g.valid_to IS NULL OR g.valid_to>?)"]
        params = [ticker, node, seq, seq, seq, seq]
        if days is not None:
            clauses.append("m.day IN (" + ",".join("?" for _ in days) + ")" if days else "0")
            params.extend(days)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT json_extract(e.value,'$[0]') AS origin, json_extract(e.value,'$[1]') AS target, count(DISTINCT m.id) "
                "FROM objects m JOIN objects g ON g.ticker=m.ticker AND g.id=json_extract(m.payload,'$.case_id') "
                "JOIN json_each(g.payload,'$.edges') e WHERE " + " AND ".join(clauses) + " GROUP BY origin,target ORDER BY origin,target", params,
            ).fetchall()
        return [{"edge_id":a+":"+b,"from":a,"to":b,"case_count":count} for a,b,count in rows]

    def baseline(self, owner, ticker, view_id, limit=20):
        view = self.views.get(owner, view_id, ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        days = self.days(view)
        cases = self.views.page(
            owner, "case", ticker, view=view, view_id=view_id, days=days, limit=limit
        )
        nodes, edges = self.counts(ticker, view["seq"], days)
        state = {
            "kind": "graph",
            "ticker": ticker,
            "view_id": view_id,
            "limit": limit,
            "seq": view["seq"],
            "head": [[c["case_id"], c] for c in cases["items"]],
        }
        token = self.store.save_token(owner, self.scope(ticker, view_id, limit), state)
        return validate(
            "GraphBaseline",
            {
                "graph_revision": view["seq"],
                "stream_cursor": token,
                "nodes": nodes,
                "edges": edges,
                "cases": cases,
            },
        )

    def next(self, owner, state, view):
        changed = self.store.changes(
            state["ticker"], ["case", "graph_member"], state["seq"], limit=1
        )
        if not changed:
            return None
        seq = changed[0]["seq"]
        rows = self.store.page(
            "case", state["ticker"], seq, days=self.days(view), limit=state["limit"]
        )
        old = dict(state["head"])
        current = {row["id"]: row["data"] for row in rows}
        nodes, edges = self.counts(state["ticker"], seq, self.days(view))
        payload = validate(
            "GraphDelta",
            {
                "previous_graph_revision": state["seq"],
                "graph_revision": seq,
                "upsert_cases": [row["data"] for row in rows if old.get(row["id"]) != row["data"]],
                "remove_case_ids": [identity for identity in old if identity not in current],
                "replace_nodes": nodes,
                "replace_edges": edges,
            },
        )
        updated = {**state, "seq": seq, "head": list(current.items())}
        scope = self.scope(state["ticker"], state["view_id"], state["limit"])
        token = self.store.save_token(owner, scope, updated)
        envelope = {
            "event_id": token,
            "stream_cursor": token,
            "view_id": state["view_id"],
            "scope_key": scope,
            "sequence": str(seq),
            "emitted_at": instant(datetime.now(UTC)),
            "payload": payload,
        }
        wire = "id: " + token + "\nevent: graph.delta\ndata: " + encode(envelope) + "\n\n"
        if len(wire.encode()) > 32768:
            raise ApiFailure("BASELINE_UNAVAILABLE", 410)
        return wire, updated


def install(app: FastAPI):
    graphs = Graphs(app.state.store, app.state.views)
    app.state.graphs = graphs
    prefix = "/api/doxagent/v2/tickers/{ticker}/runtime"

    @app.get(prefix + "/graph")
    async def baseline(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "limit"})
        data = graphs.baseline(
            request.state.principal.user_id,
            ticker,
            args.get("view_id", ""),
            int(args.get("limit", 20)),
        )
        return app.state.respond(request, "GraphBaseline", data, view_id=args["view_id"])

    def node_page(ticker, node_id, request, args):
        if node_id not in NODES:
            raise ApiFailure("VALIDATION_FAILED", 422)
        owner = request.state.principal.user_id
        view = graphs.views.get(owner, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        page = graphs.views.page(
            owner,
            "graph_member",
            ticker,
            view=view,
            view_id=args["view_id"],
            parent=node_id,
            days=graphs.days(view),
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return view, page

    @app.get(prefix + "/nodes/{node_id}/cases")
    async def cases(ticker: str, node_id: str, request: Request):
        args = app.state.query(request, {"view_id", "limit", "cursor"})
        _, page = node_page(ticker, node_id, request, args)
        return app.state.respond(request, "CaseSummaryPage", page, view_id=args["view_id"])

    @app.get(prefix + "/nodes/{node_id}")
    async def node(ticker: str, node_id: str, request: Request):
        args = app.state.query(request, {"view_id", "limit"})
        view, page = node_page(ticker, node_id, request, args)
        nodes, _ = graphs.counts(ticker, view["seq"], graphs.days(view))
        value = {"summary": next(n for n in nodes if n["node_id"] == node_id), "recent_cases": page,
                 "path_edges": graphs.paths(ticker, view["seq"], graphs.days(view), node_id)}
        return app.state.respond(request, "NodeDetail", value, view_id=args["view_id"])

    @app.get(prefix + "/graph/events")
    async def events(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "cursor"})
        owner, view_id = request.state.principal.user_id, args.get("view_id", "")
        view = graphs.views.get(owner, view_id, ticker)
        header = request.headers.get("last-event-id")
        if header and args.get("cursor") and header != args["cursor"]:
            raise ApiFailure("INVALID_CURSOR", 400)
        cursor = header or args.get("cursor")
        if not cursor:
            raise ApiFailure("CURSOR_REQUIRED", 400)
        state = graphs.cursor(owner, cursor, ticker, view_id)

        async def generate():
            current = state
            checked, heartbeat = time.monotonic(), time.monotonic()
            while not await request.is_disconnected():
                if time.time() >= request.state.principal.expires_at:
                    return
                if time.monotonic() - checked >= 30:
                    try:
                        await app.state.auth.authenticate(request.headers["authorization"][7:])
                    except ApiFailure:
                        return
                    checked = time.monotonic()
                reason = None
                if instant(datetime.now(UTC)) >= view["wire"]["expires_at"]:
                    reason = "VIEW_EXPIRED"
                elif str(semantic_day(datetime.now(UTC))) != view["wire"]["clock"]["semantic_day"]:
                    reason = "SCOPE_MISMATCH"
                try:
                    result = graphs.next(owner, current, view) if reason is None else None
                except ApiFailure as exc:
                    reason, result = exc.code, None
                if reason:
                    payload = {"reason": reason, "replacement_required": True}
                    envelope = {
                        "event_id": cursor,
                        "stream_cursor": cursor,
                        "view_id": view_id,
                        "scope_key": graphs.scope(ticker, view_id, current["limit"]),
                        "sequence": str(current["seq"]),
                        "emitted_at": instant(datetime.now(UTC)),
                        "payload": payload,
                    }
                    yield (
                        (
                            "event: scope.rolled\ndata: "
                            if reason == "SCOPE_MISMATCH"
                            else "event: reset\ndata: "
                        )
                        + encode(envelope)
                        + "\n\n"
                    )
                    return
                if result:
                    wire, current = result
                    yield wire
                elif time.monotonic() - heartbeat >= 15:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0 if result else 1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
