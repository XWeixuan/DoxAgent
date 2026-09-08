"""Read the before/after evidence pinned to one submitted maintenance input."""

from datetime import date

from fastapi import Request

from .errors import ApiFailure


def install(app):
    prefix = "/api/doxagent/v2/tickers/{ticker}/reference-deltas"
    store, views = app.state.store, app.state.views

    def context(request, ticker, args):
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "EVENTS":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        period = view["wire"]["period"]
        return view, None if period["selected"] == "ALL" else period["current"]["trading_days"]

    @app.get(prefix + "/days")
    async def days(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "limit", "cursor"})
        view, members = context(request, ticker, args)
        if members is None:
            data = views.page(
                request.state.principal.user_id,
                "delta_day",
                ticker,
                view=view,
                view_id=args["view_id"],
                limit=int(args.get("limit", 20)),
                cursor=args.get("cursor"),
            )
        else:
            try:
                limit = int(args.get("limit", 20))
                if not 1 <= limit <= 100:
                    raise ValueError()
            except ValueError:
                raise ApiFailure("VALIDATION_FAILED", 422) from None
            owner, scope, after = (
                request.state.principal.user_id,
                f"delta-days:{ticker}:{args['view_id']}:{limit}",
                None,
            )
            if args.get("cursor"):
                try:
                    after = store.token(args["cursor"], owner, scope=scope)["after"]
                except TimeoutError:
                    raise ApiFailure("CURSOR_EXPIRED", 410) from None
                except ValueError:
                    raise ApiFailure("INVALID_CURSOR", 400) from None
            selected = [
                day for day in sorted(members, reverse=True) if after is None or day < after
            ][: limit + 1]
            items = [
                store.get("delta_day", ticker, day, view["seq"])
                or {
                    "semantic_day": day,
                    "state": "UNAVAILABLE",
                    "delta_id": None,
                    "from_library_version": None,
                    "to_library_version": None,
                    "from_library_snapshot_id": None,
                    "to_library_snapshot_id": None,
                    "reason": "NOT_RECORDED",
                }
                for day in selected[:limit]
            ]
            more = len(selected) > limit
            data = {
                "items": items,
                "limit": limit,
                "has_more": more,
                "next_cursor": store.save_token(owner, scope, {"after": selected[limit - 1]})
                if more
                else None,
                "snapshot_id": args["view_id"],
            }
        return app.state.respond(request, "DeltaDayPage", data, view_id=args["view_id"])

    @app.get(prefix + "/days/{semantic_day}")
    async def delta(ticker: str, semantic_day: str, request: Request):
        args = app.state.query(request, {"view_id", "limit", "cursor"})
        view, members = context(request, ticker, args)
        try:
            date.fromisoformat(semantic_day)
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        if members is not None and semantic_day not in members:
            raise ApiFailure("SCOPE_MISMATCH", 400)
        day = store.get("delta_day", ticker, semantic_day, view["seq"])
        if not day or day["state"] not in {"AVAILABLE", "NO_CHANGE"}:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        data = store.get("reference_delta", ticker, day["delta_id"], view["seq"])
        data["changes"] = views.page(
            request.state.principal.user_id,
            "reference_change",
            ticker,
            view=view,
            view_id=args["view_id"],
            parent=day["delta_id"],
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return app.state.respond(request, "ReferenceDelta", data, view_id=args["view_id"])

    @app.get(prefix + "/{delta_id}/changes/{change_id}/event")
    async def event(ticker: str, delta_id: str, change_id: str, request: Request):
        args = app.state.query(request, {"side", "limit", "cursor"})
        if args.get("side") not in {"before", "after"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        sides = store.get("reference_change_sides", ticker, delta_id + ":" + change_id)
        parent = sides.get(args["side"]) if sides else None
        if not parent:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        data = store.get("reference_event", ticker, parent)
        data["facts"] = views.page(
            request.state.principal.user_id,
            "fact",
            ticker,
            view=None,
            parent=parent,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return app.state.respond(request, "EventDetail", data)
