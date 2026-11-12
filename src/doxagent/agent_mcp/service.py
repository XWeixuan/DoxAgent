"""Four small read operations, preserving page/view boundaries and display fields."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from urllib.parse import quote

from . import projection as p
from .client import MAX_RESULT_BYTES, ReadError, StateStore, data, encoded
from .schemas import TOOLS

ROWS = {
    "case": p.case_row,
    "operations": p.operations_row,
    "source": p.source_row,
    "attempts": p.attempts,
    "messages": p.message,
    "candidates": p.candidate,
    "executions": p.execution,
}


def path_id(value):
    return quote(value, safe="")


def expiration(view):
    try:
        return float(view.rsplit(".", 1)[1])
    except (ValueError, IndexError):
        return time.time() + 60


def fingerprint(name, args):
    return encoded(
        [name, {k: v for k, v in args.items() if k not in {"view_id", "cursor"}}]
    ).decode()


def metadata(wire):
    return p.pick(
        wire.get("meta", {}),
        "view_id",
        "as_of",
        "freshness",
        "refresh_error",
        "request_id",
        "read_at",
    )


class AgentReads:
    def __init__(self, api, *, state=None):
        self.api, self.state = api, state or StateStore()
        self.capacity = asyncio.Semaphore(1)
        self.waiting = 0
        self.contexts = OrderedDict()

    async def call(self, name, arguments):
        if name not in TOOLS:
            raise ReadError("UNKNOWN_TOOL", "Only the four DoxAgent read tools are available.")
        args = TOOLS[name][0].model_validate(arguments).model_dump(exclude_none=True)
        if self.capacity.locked() and self.waiting >= 2:
            raise ReadError("SERVICE_BUSY", "Read capacity is busy; retry later.", retryable=True)
        self.waiting += 1
        admitted = False
        try:
            async with asyncio.timeout(15):
                async with self.capacity:
                    self.waiting -= 1
                    admitted = True
                    result = await self._dispatch(name, args)
                    view = result.get("meta", {}).get("view_id")
                    if view in self.contexts and "projection" in self.contexts[view]:
                        result["meta"]["projection"] = self.contexts[view]["projection"]
                    return result
        except TimeoutError as exc:
            raise ReadError(
                "QUERY_TIMEOUT", "Read deadline reached; retry a bounded query.", retryable=True
            ) from exc
        finally:
            if not admitted:
                self.waiting -= 1

    async def _context(self, page, ticker=None, period=None, view_id=None):
        if view_id:
            known = self.contexts.get(view_id)
            if known:
                if (
                    known["page"] != page
                    or known["ticker"] != ticker
                    or (period and known.get("period", {}).get("selected") != period)
                ):
                    raise ReadError(
                        "SCOPE_MISMATCH", "View does not match the selected page, ticker or period."
                    )
                if expiration(view_id) <= time.time():
                    raise ReadError("VIEW_EXPIRED", "View expired; start a fresh query.")
                return known
            # The API validates externally supplied view ownership and scope.
            return {"view_id": view_id, "period": None}
        requested = period or (
            "PREVIOUS_TRADING_DAY" if page in {"OVERVIEW", "COST"} else "CURRENT_TRADING_DAY"
        )
        wire = await self.api.get(
            "read-context", {"page": page, "ticker": ticker, "period": requested, "refresh": "OPEN"}
        )
        ctx = data(wire)
        if (
            page == "OVERVIEW"
            and period is None
            and any(
                option["period"] == "CURRENT_TRADING_DAY" and option["selectable"]
                for option in ctx["period_options"]
            )
        ):
            wire = await self.api.get(
                "read-context", {"page": page, "period": "CURRENT_TRADING_DAY", "refresh": "OPEN"}
            )
            ctx = data(wire)
        self.contexts[ctx["view_id"]] = p.pick(ctx, "view_id", "page", "ticker", "period")
        self.contexts[ctx["view_id"]]["projection"] = p.projection_clock(ctx.get("projection", []))
        while len(self.contexts) > 64:
            self.contexts.popitem(last=False)
        return self.contexts[ctx["view_id"]]

    async def _dispatch(self, name, args):
        if args.get("cursor") and not (name.endswith("case_detail") and args["part"] == "content"):
            state = self.state.get(args["cursor"], "page")
            if state["fingerprint"] != fingerprint(name, args) or (
                args.get("view_id") and args["view_id"] != state["view"]
            ):
                raise ReadError(
                    "INVALID_CURSOR", "Keep the same filters, ticker, view and page size."
                )
            if state["items"]:
                return self._page(state)
            return await self._fetch_page(state)
        if name.endswith("get_overview"):
            return await self._overview(args, name)
        if name.endswith(("list_cases", "search_cases")):
            ctx = await self._context(
                "RUNTIME",
                args["ticker"],
                args.get("period") or "CURRENT_TRADING_DAY",
                args.get("view_id"),
            )
            params = {
                "view_id": ctx["view_id"],
                **p.pick(args, "limit", "result", "source_id", "q"),
            }
            return await self._fetch_page(
                {
                    "path": f"tickers/{path_id(args['ticker'])}/runtime/cases",
                    "params": params,
                    "projector": "case",
                    "view": ctx["view_id"],
                    "period": ctx["period"],
                    "extra": {},
                    "fingerprint": fingerprint(name, args),
                    "items": [],
                    "case": None,
                }
            )
        return await self._detail(args, name)

    async def _optional(self, path, params, projector):
        try:
            wire = await self.api.get(path, params)
            return {**p.resource(wire["data"], projector), "meta": metadata(wire)}
        except ReadError as exc:
            return {"state": "ERROR", "data": None, "reason": "READ_FAILED", **exc.wire()}

    async def _overview(self, args, name):
        section = args["section"]
        if section == "sources":
            wire = await self.api.get("message-bus/source-catalog")
            catalog = data(wire)
            sectors = [p.pick(s, "id", "name") for s in catalog["sectors"]]
            groups = ["GLOBAL", *[s["id"] for s in sectors], "UNKNOWN", "UNUSED", "TEST"]

            def order(row):
                c = row.get("classification")
                group = (
                    "TEST" if row["delivery_mode"] == "test" else
                    "UNUSED" if not row["enabled"] or not any(
                        s["enabled"] for s in row["subscriptions"]) else
                    "UNKNOWN" if not c else "GLOBAL" if c["applicability"] == "GLOBAL" else
                    next(iter(c["sector_ids"]), "UNKNOWN")
                )
                return (groups.index(group) if group in groups else groups.index("UNKNOWN"),
                        row["name"], row["source_id"])

            snapshot = "catalog." + str(int(time.time()) + 900)
            return self._page({
                "items": [p.catalog_row(r) for r in sorted(catalog["sources"], key=order)],
                "params": {"limit": args["limit"]}, "more": False, "view": snapshot,
                "period": None, "snapshot_id": catalog["snapshot_at"], "meta": metadata(wire),
                "extra": {"section": section, "snapshot_at": catalog["snapshot_at"],
                          "semantic_day": catalog["semantic_day"], "sectors": sectors},
                "fingerprint": fingerprint(name, args),
            })
        page = {
            "summary": "OVERVIEW",
            "sources": "MESSAGE_BUS",
            "cost": "COST",
            "policy": "POLICIES",
        }[section]
        ctx = await self._context(
            page,
            args.get("ticker") if section in {"sources", "policy"} else None,
            args.get("period"),
            args.get("view_id"),
        )
        view = ctx["view_id"]
        if section == "cost":
            wire = await self.api.get(
                "audit/cost/report",
                {
                    "view_id": view,
                    "scope": args["scope"],
                    "ticker_selector": args["ticker_selector"],
                    "dimension": "NODE",
                    "limit": 1,
                },
            )
            return {
                "data": {
                    "section": section,
                    "scope": args["scope"],
                    "ticker_selector": args["ticker_selector"],
                    "period": ctx["period"],
                    **p.cost_kpis(data(wire)),
                },
                "meta": metadata(wire),
            }
        if section == "policy":
            wire = await self.api.get(
                f"tickers/{path_id(args['ticker'])}/policies/metrics",
                {"view_id": view, "shell_id": args["shell_id"]},
            )
            fields = p.pick(
                data(wire),
                "active",
                "long_ratio",
                "short_ratio",
                "added",
                "hit",
                "modified",
                "retired",
                "executed",
            )
            return {
                "data": {
                    "section": section,
                    "ticker": args["ticker"],
                    "shell_id": args["shell_id"],
                    "period": ctx["period"],
                    "metrics": p.metrics(fields),
                },
                "meta": metadata(wire),
            }
        extra = {"section": section, "projection": ctx.get("projection", [])}
        if section == "summary":
            extra["status"] = await self._optional(
                "overview/status",
                {"view_id": view},
                lambda v: {"clock": p.pick(v["clock"], "session")},
            )
            extra["metrics"] = await self._optional(
                "overview/metrics", {"view_id": view}, p.metrics
            )
            extra["gateway"] = await self._optional(
                "overview/gateway-status",
                {"view_id": view},
                lambda v: p.pick(v, "ib_gateway_live_status", "ib_gateway_paper_status"),
            )
            extra["openrouter_balance"] = await self._optional(
                "overview/openrouter-balance",
                {},
                lambda v: p.pick(v, "status", "balance_usd", "checked_at"),
            )
        return await self._fetch_page(
            {
                "path": "overview/operations",
                "params": {
                    "view_id": view,
                    "section": section,
                    "ticker": args.get("ticker"),
                    "limit": args["limit"],
                },
                "projector": "operations" if section == "summary" else "source",
                "view": view,
                "period": ctx["period"],
                "extra": extra,
                "fingerprint": fingerprint(name, args),
                "items": [],
                "case": None,
            }
        )

    async def _fetch_page(self, state):
        wire = await self.api.get(state["path"], state["params"])
        raw = data(wire)
        items = [ROWS[state["projector"]](row) for row in raw["items"]]
        if state.get("case"):
            items = self._defer(items, state["case"], state["view"])
        state = {
            **state,
            "items": items,
            "params": {**state["params"], "cursor": raw["next_cursor"]},
            "more": raw["has_more"],
            "meta": metadata(wire),
            "snapshot_id": raw["snapshot_id"],
        }
        return self._page(state)

    def _page(self, state):
        result = {
            "data": {
                **state["extra"],
                "period": state["period"],
                "items": [],
                "limit": state["params"]["limit"],
                "snapshot_id": state["snapshot_id"],
                "has_more": False,
                "next_cursor": None,
            },
            "meta": state["meta"],
        }
        context = self.contexts.get(state["view"], {})
        if "projection" in context:
            result["meta"] = {**result["meta"], "projection": context["projection"]}
        remaining = list(state["items"])
        while remaining and len(result["data"]["items"]) < state["params"]["limit"]:
            result["data"]["items"].append(remaining[0])
            if len(encoded(result)) > MAX_RESULT_BYTES - 1024:
                result["data"]["items"].pop()
                if not result["data"]["items"]:
                    raise ReadError(
                        "RESOURCE_TOO_LARGE", "One display row exceeds the response budget."
                    )
                break
            remaining.pop(0)
        if remaining or state["more"]:
            result["data"]["has_more"] = True
            result["data"]["next_cursor"] = self.state.put(
                "page", {**state, "items": remaining}, expiration(state["view"])
            )
        return result

    def _defer(self, value, case, view):
        expires = expiration(view)
        if isinstance(value, dict):
            if {"content_id", "sha256", "content_type"} <= value.keys():
                handle = self.state.put(
                    "content", {"case": case, "view": view, "ref": value}, expires
                )
                return {"content_handle": handle}
            result = {}
            for key, raw in value.items():
                if (key == "body" and isinstance(raw, str)) or (
                    key == "receipt" and raw is not None
                ):
                    text = raw if isinstance(raw, str) else encoded(raw).decode()
                    handle = self.state.put(
                        "content", {"case": case, "view": view, "text": text}, expires
                    )
                    result[key] = {"content_handle": handle}
                    if not isinstance(raw, str):
                        result[key]["format"] = "json"
                else:
                    result[key] = self._defer(raw, case, view)
            return result
        if isinstance(value, str) and len(value.encode("utf-8")) > 4000:
            handle = self.state.put("content", {"case": case, "view": view, "text": value}, expires)
            return {"content_handle": handle}
        if isinstance(value, list):
            projected = [self._defer(raw, case, view) for raw in value]
            if len(value) > 20:
                handle = self.state.put(
                    "content",
                    {"case": case, "view": view, "text": encoded(projected).decode()},
                    expires,
                )
                return {"content_handle": handle, "format": "json"}
            return projected
        return value

    async def _detail(self, args, name):
        case = {"ticker": args["ticker"], "case_id": args["case_id"]}
        part = args["part"]
        if part == "content":
            return await self._content(args, case)
        ctx = await self._context("RUNTIME", args["ticker"], view_id=args.get("view_id"))
        view = ctx["view_id"]
        root = f"tickers/{path_id(args['ticker'])}/runtime/cases/{path_id(args['case_id'])}"
        if part in {"attempts", "messages", "candidates", "executions"}:
            return await self._fetch_page(
                {
                    "path": root + "/" + part,
                    "params": {
                        "view_id": view,
                        **p.pick(args, "node", "originating_node", "limit"),
                    },
                    "projector": part,
                    "view": view,
                    "period": ctx["period"],
                    "extra": {**case, "part": part},
                    "case": case,
                    "fingerprint": fingerprint(name, args),
                    "items": [],
                }
            )
        wire = await self.api.get(root, {"view_id": view})
        projected = p.detail(data(wire), part)
        # Convert the frontend's embedded first-page cursors into the same scoped
        # MCP continuation used by explicit subpage calls.
        for node in ("w1", "w2", "w3"):
            node_data = (projected.get(node) or {}).get("data") or {}
            if node_data.get("attempts"):
                self._bind_nested(
                    node_data["attempts"], "attempts", case, view, ctx, name, wire, node.upper()
                )
        if projected.get("messages"):
            self._bind_nested(projected["messages"], "messages", case, view, ctx, name, wire)
        projected = self._defer(projected, case, view)
        result = {"data": {**case, "part": part, **projected}, "meta": metadata(wire)}
        if "projection" in ctx:
            result["meta"]["projection"] = ctx["projection"]
        if len(encoded(result)) > MAX_RESULT_BYTES:
            handle = self.state.put(
                "content",
                {"case": case, "view": view, "text": encoded(projected).decode()},
                expiration(view),
            )
            result["data"] = {**case, "part": part, "content_handle": handle, "format": "json"}
        return result

    def _bind_nested(self, page, part, case, view, ctx, name, wire, node=None):
        if not page.get("has_more"):
            return
        args = (
            TOOLS[name][0]
            .model_validate({**case, "part": part, **({"node": node} if node else {})})
            .model_dump(exclude_none=True)
        )
        state = {
            "path": (
                f"tickers/{path_id(case['ticker'])}/runtime/cases/{path_id(case['case_id'])}/{part}"
            ),
            "params": {
                "view_id": view,
                "limit": 20,
                "cursor": page["next_cursor"],
                **({"node": node} if node else {}),
            },
            "projector": part,
            "view": view,
            "period": ctx["period"],
            "extra": {**case, "part": part},
            "case": case,
            "fingerprint": fingerprint(name, args),
            "items": [],
            "more": True,
            "meta": metadata(wire),
            "snapshot_id": page.get("snapshot_id", view),
        }
        page["next_cursor"] = self.state.put("page", state, expiration(view))

    async def _content(self, args, case):
        value = self.state.get(args["content_handle"], "content")
        if value["case"] != case or (args.get("view_id") and args["view_id"] != value["view"]):
            raise ReadError("SCOPE_MISMATCH", "Content belongs to another Case or view.")
        if "ref" in value:
            wire = await self.api.get(
                f"tickers/{path_id(case['ticker'])}/contents/{path_id(value['ref']['content_id'])}",
                {
                    "view_id": value["view"],
                    "case_id": case["case_id"],
                    "cursor": args.get("cursor"),
                },
            )
            raw = data(wire)
            if p.pick(raw["content"], "content_id", "sha256") != p.pick(
                value["ref"], "content_id", "sha256"
            ):
                raise ReadError("CONTENT_MISMATCH", "Content identity changed; reopen the Case.")
            return {
                "data": {
                    **case,
                    "content_handle": args["content_handle"],
                    **p.pick(raw, "text", "chunk_index", "next_cursor", "complete"),
                },
                "meta": {**metadata(wire), "view_id": value["view"]},
            }
        offset = 0
        if args.get("cursor"):
            cursor = self.state.get(args["cursor"], "text")
            if cursor["handle"] != args["content_handle"]:
                raise ReadError("INVALID_CURSOR", "Content cursor does not match this field.")
            offset = cursor["offset"]
        raw = value["text"].encode("utf-8")
        text = raw[offset : offset + 16000].decode("utf-8", errors="ignore")
        end = offset + len(text.encode("utf-8"))
        complete = end >= len(raw)
        cursor = (
            None
            if complete
            else self.state.put(
                "text", {"handle": args["content_handle"], "offset": end}, expiration(value["view"])
            )
        )
        return {
            "data": {
                **case,
                "content_handle": args["content_handle"],
                "text": text,
                "next_cursor": cursor,
                "complete": complete,
            },
            "meta": {"view_id": value["view"]},
        }
