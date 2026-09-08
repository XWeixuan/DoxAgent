"""Durable, bounded message streams with resumable per-row delivery positions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from doxagent.semantic_clock import semantic_day
from doxagent.v2_read.repository import encode, instant

from .dto import validate
from .errors import ApiFailure

PREFIX = "/api/doxagent/v2"


class MessageStreams:
    def __init__(self, store: Any, views: Any) -> None:
        self.store, self.views = store, views

    def filters(self, args: dict[str, str], view: dict[str, Any]) -> dict[str, Any]:
        filters = {}
        if args.get("source_kind"):
            if args["source_kind"] not in {"api", "crawler"}:
                raise ApiFailure("VALIDATION_FAILED", 422)
            filters["source_kind"] = args["source_kind"]
        if args.get("source_id"):
            filters["source_id"] = args["source_id"]
        if args.get("route") and args["route"] != "ALL":
            if args["route"] not in {
                "ARCHIVE",
                "TRADE",
                "ADD_TO_DELTA",
                "W3_PENDING",
                "FAILED",
                "NOT_PROCESSED",
            }:
                raise ApiFailure("VALIDATION_FAILED", 422)
            filters["route"] = args["route"]
        if args.get("q", "").strip():
            if len(args["q"].strip()) > 200:
                raise ApiFailure("VALIDATION_FAILED", 422)
            filters["q"] = args["q"].strip()
        if filters.get("source_id"):
            source = self.store.get(
                "native:source_definitions", "", filters["source_id"], view["seq"]
            )
            binding = self.store.get(
                "native:ticker_source_bindings",
                view["wire"]["ticker"],
                view["wire"]["ticker"] + ":" + filters["source_id"],
                view["seq"],
            )
            if (
                not source
                or not binding
                or (filters.get("source_kind") and source["kind"] != filters["source_kind"])
            ):
                raise ApiFailure("VALIDATION_FAILED", 422)
        window = view["wire"]["period"]["current"]
        if window["membership"] == "LISTED_TRADING_DAYS":
            filters["days"] = window["trading_days"]
        return filters

    @staticmethod
    def scope(ticker: str, view_id: str, filters: dict[str, Any], limit: int) -> str:
        return hashlib.sha256(
            encode(["messages", ticker, view_id, filters, limit]).encode()
        ).hexdigest()

    def baseline(self, owner: str, ticker: str, args: dict[str, str]) -> dict[str, Any]:
        identity = args.get("view_id", "")
        view = self.views.get(owner, identity, ticker)
        filters = self.filters(args, view)
        limit = int(args.get("limit", 20))
        page = self.views.page(
            owner,
            "message",
            ticker,
            view=view,
            view_id=identity,
            limit=limit,
            cursor=args.get("cursor"),
            **filters,
        )
        scope = self.scope(ticker, identity, filters, limit)
        token = self.store.save_token(
            owner,
            scope,
            {
                "seq": view["seq"],
                "view_id": identity,
                "ticker": ticker,
                "limit": limit,
                "filters": filters,
                "head": [
                    [item["standard_message_id"], item["row_revision"]] for item in page["items"]
                ],
                "pending": [],
                "ordinal": 0,
                "delta_version": 2,
            },
        )
        return {"messages": page, "stream_cursor": token}

    def cursor(
        self,
        owner: str,
        token: str,
        *,
        ticker: str,
        view_id: str,
        filters: dict[str, Any],
        limit: int | None = None,
    ) -> dict[str, Any]:
        try:
            state = self.store.token(token, owner)
        except TimeoutError:
            raise ApiFailure("CURSOR_EXPIRED", 410) from None
        except ValueError:
            raise ApiFailure("INVALID_CURSOR", 400) from None
        if state.get("delta_version") != 2:
            raise ApiFailure("CURSOR_EXPIRED", 410)
        if (
            state.get("ticker") != ticker
            or state.get("view_id") != view_id
            or state.get("filters") != filters
        ):
            raise ApiFailure("SCOPE_MISMATCH", 400)
        if limit is not None and state["limit"] != limit:
            raise ApiFailure("SCOPE_MISMATCH", 400)
        return state

    def next(self, owner: str, state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        ticker, seq = state["ticker"], state["seq"]
        if not state["pending"]:
            changes = self.store.changes(ticker, ["message"], seq, limit=1)
            if not changes:
                return None
            target = changes[0]["seq"]
            pending = []
            # Follow changed identities across the entire retained scope, including loaded
            # later pages. Falling out of a bounded head is never a scope removal.
            for identity in dict.fromkeys(change["id"] for change in changes):
                new = self.store.page("message", ticker, target, identity=identity,
                                      limit=1, **state["filters"])
                old = self.store.page("message", ticker, target - 1, identity=identity,
                                      limit=1, **state["filters"])
                if new:
                    pending.append([identity, "UPSERT"])
                elif old:
                    pending.append([identity, "REMOVE"])
            state = {
                **state,
                "seq": target,
                "head": [],
                "pending": pending,
                "ordinal": 0,
            }
            if not pending:
                # No visible head change; advance internally without fabricating a row event.
                return "", state
        identity, action = state["pending"][0]
        row = self.store.get(
            "message", ticker, identity, state["seq"] if action == "UPSERT" else state["seq"] - 1
        )
        if row is None:
            raise ApiFailure("BASELINE_UNAVAILABLE", 410)
        payload = {
            "standard_message_id": identity,
            "revision": row["revision"],
            "row_revision": state["seq"],
            "matches_scope": action == "UPSERT",
            "action": action,
            "row": None,
        }
        if action == "UPSERT":
            payload.update(row_revision=row["row_revision"], row=row)
        validate("MessageDelta", payload)
        remainder = {**state, "pending": state["pending"][1:], "ordinal": state["ordinal"] + 1}
        scope = self.scope(ticker, state["view_id"], state["filters"], state["limit"])
        token = self.store.save_token(owner, scope, remainder)
        envelope = {
            "event_id": token,
            "stream_cursor": token,
            "view_id": state["view_id"],
            "scope_key": scope,
            "sequence": str(state["seq"] * 100000 + remainder["ordinal"]),
            "emitted_at": instant(datetime.now(UTC)),
            "payload": payload,
        }
        if len(encode(envelope).encode()) > 32000:
            payload["row"] = None
        return "id: " + token + "\nevent: message.delta\ndata: " + encode(
            envelope
        ) + "\n\n", remainder


def install(app: FastAPI) -> None:
    streams = MessageStreams(app.state.store, app.state.views)
    app.state.message_streams = streams
    query, respond = app.state.query, app.state.respond

    @app.get(PREFIX + "/tickers/{ticker}/messages")
    async def messages(ticker: str, request: Request) -> Any:
        args = query(
            request, {"view_id", "source_kind", "source_id", "route", "q", "limit", "cursor"}
        )
        return respond(
            request,
            "MessageBaseline",
            streams.baseline(request.state.principal.user_id, ticker, args),
            view_id=args.get("view_id"),
        )

    @app.get(PREFIX + "/tickers/{ticker}/messages/events")
    async def events(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id", "source_kind", "source_id", "route", "q", "cursor"})
        view_id, owner = args.get("view_id", ""), request.state.principal.user_id
        view = streams.views.get(owner, view_id, ticker)
        filters = streams.filters(args, view)
        header = request.headers.get("last-event-id")
        if header and args.get("cursor") and header != args["cursor"]:
            raise ApiFailure("INVALID_CURSOR", 400)
        cursor = header or args.get("cursor")
        if not cursor:
            raise ApiFailure("CURSOR_REQUIRED", 400)
        state = streams.cursor(owner, cursor, ticker=ticker, view_id=view_id, filters=filters)
        token = request.headers["authorization"][7:]

        async def generate() -> Any:
            current = state
            last_auth, last_heartbeat = time.monotonic(), time.monotonic()
            while not await request.is_disconnected():
                if time.time() >= request.state.principal.expires_at:
                    return
                if time.monotonic() - last_auth >= 30:
                    try:
                        await app.state.auth.authenticate(token)
                    except ApiFailure:
                        return
                    last_auth = time.monotonic()
                expired = instant(datetime.now(UTC)) >= view["wire"]["expires_at"]
                if (
                    expired
                    or str(semantic_day(datetime.now(UTC))) != view["wire"]["clock"]["semantic_day"]
                ):
                    payload = {
                        "reason": "VIEW_EXPIRED" if expired else "SCOPE_MISMATCH",
                        "replacement_required": True,
                    }
                    envelope = {
                        "event_id": cursor,
                        "stream_cursor": cursor,
                        "view_id": view_id,
                        "scope_key": streams.scope(ticker, view_id, filters, current["limit"]),
                        "sequence": str(current["seq"] * 100000 + current["ordinal"]),
                        "emitted_at": instant(datetime.now(UTC)),
                        "payload": payload,
                    }
                    yield (
                        ("event: reset\ndata: " if expired else "event: scope.rolled\ndata: ")
                        + encode(envelope)
                        + "\n\n"
                    )
                    return
                result = streams.next(owner, current)
                if result:
                    wire, current = result
                    if wire:
                        yield wire
                    await asyncio.sleep(0)
                    continue
                if time.monotonic() - last_heartbeat >= 15:
                    yield ": keepalive\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(1)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get(PREFIX + "/tickers/{ticker}/messages/{standard_message_id}/revisions/{revision}")
    async def message(
        ticker: str, standard_message_id: str, revision: int, request: Request
    ) -> Any:
        args = query(request, {"view_id", "stream_cursor"})
        owner, view_id = request.state.principal.user_id, args.get("view_id", "")
        view = streams.views.get(owner, view_id, ticker)
        seq = view["seq"]
        if args.get("stream_cursor"):
            try:
                token = streams.store.token(args["stream_cursor"], owner)
                if "filters" not in token:
                    raise ValueError("not a stream cursor")
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
            token = streams.cursor(
                owner,
                args["stream_cursor"],
                ticker=ticker,
                view_id=view_id,
                filters=token["filters"],
            )
            seq = token["seq"]
        row = streams.store.get("message", ticker, standard_message_id, seq)
        if not row or row["revision"] != revision:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return respond(request, "MessageSummary", row, view_id=view_id, read_seq=seq)

    @app.get(PREFIX + "/tickers/{ticker}/messages/{standard_message_id}/revisions/{revision}/body")
    async def body(ticker: str, standard_message_id: str, revision: int, request: Request) -> Any:
        args = query(request, {"cursor"})
        with streams.store.connect() as db:
            found = db.execute(
                "SELECT payload FROM objects WHERE kind='message' AND ticker=? AND id=? "
                "AND json_extract(payload,'$.revision')=? ORDER BY valid_from DESC LIMIT 1",
                (ticker, standard_message_id, revision),
            ).fetchone()
        message = json.loads(found[0]) if found else None
        if not message or message["revision"] != revision:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return app.state.content_chunk(
            request, ticker, message["body"]["content_id"], args.get("cursor")
        )
