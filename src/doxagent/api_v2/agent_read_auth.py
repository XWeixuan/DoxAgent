"""One optional internal credential, restricted to the existing presentation reads."""

from __future__ import annotations

import hmac
import os
import re
import time
from pathlib import Path

from .auth import Principal
from .errors import ApiFailure

PAGES = {"OVERVIEW", "RUNTIME", "MESSAGE_BUS", "COST", "POLICIES"}
GLOBAL = {
    "/auth/me",
    "/read-context",
    "/overview/status",
    "/overview/gateway-status",
    "/overview/openrouter-balance",
    "/overview/metrics",
    "/overview/operations",
    "/audit/cost/report",
}
TICKER_READ = re.compile(
    r"/tickers/[^/]+/(?:policies/metrics|runtime/cases(?:/[^/]+(?:/(?:attempts|messages|candidates|executions))?)?|contents/[^/]+)"
)


class AgentReadAuth:
    def __init__(self, base, token: str):
        if len(token) < 32:
            raise ValueError("Agent read token must have at least 32 characters")
        self.base, self._token = base, token

    async def authenticate(self, token):
        if hmac.compare_digest(token.encode(), self._token.encode()):
            return Principal("agent-read", "AGENT_READ", time.time() + 3600, read_only=True)
        return await self.base.authenticate(token)

    def configuration(self):
        return self.base.configuration()

    async def close(self):
        await self.base.close()


def configured_auth(base):
    path = os.getenv("DOXAGENT_AGENT_READ_TOKEN_FILE")
    return AgentReadAuth(base, Path(path).read_text(encoding="utf-8").strip()) if path else base


def require_read_scope(request):
    if not getattr(getattr(request.state, "principal", None), "read_only", False):
        return
    path = request.url.path.removeprefix("/api/doxagent/v2")
    args = request.query_params
    if request.method != "GET" or not (path in GLOBAL or TICKER_READ.fullmatch(path)):
        raise ApiFailure("FORBIDDEN", 403)
    if path == "/read-context" and (
        args.get("page") not in PAGES or args.get("refresh", "OPEN") != "OPEN"
    ):
        raise ApiFailure("FORBIDDEN", 403)
    if any(key in args for key in ("stream_cursor", "diagnostics", "today_counts")):
        raise ApiFailure("FORBIDDEN", 403)
    if args.get("result") == "BADCASE":
        raise ApiFailure("FORBIDDEN", 403)
    if "limit" in args:
        try:
            if not 1 <= int(args["limit"]) <= (1 if path == "/audit/cost/report" else 20):
                raise ValueError()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None


def require_case_content(store, views, request, ticker, content_id, args):
    """Check only Case-owned reasoning refs / message refs, never arbitrary research content."""
    owner = request.state.principal.user_id
    case_id, view_id = args.get("case_id"), args.get("view_id", "")
    view = views.get(owner, view_id, ticker)
    if view["wire"]["page"] != "RUNTIME" or not case_id:
        raise ApiFailure("FORBIDDEN", 403)
    if store.get("case", ticker, case_id, view["seq"]) is None:
        raise ApiFailure("RESOURCE_NOT_FOUND", 404)
    reasons = store.get("case_reasoning", ticker, case_id, view["seq"]) or {}
    keys = ("w1", "w2", "w3_novelty", "w3_policy", "w3_trade")
    refs = [reasons.get(key) for key in keys]
    refs.append(reasons.get("w3_trade") or reasons.get("w3_expert_trade"))
    if any(isinstance(ref, dict) and ref.get("content_id") == content_id for ref in refs):
        return
    from .case_evidence import CaseEvidence

    evidence = CaseEvidence(store, ticker, case_id, view["seq"])
    with store.connect() as db:
        found = db.execute(
            "SELECT 1 FROM " + evidence.members + " WHERE kind='message' AND ticker=? "
            "AND parent=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
            "AND json_extract(payload,'$.body.content_id')=? LIMIT 1",
            (ticker, case_id, view["seq"], view["seq"], content_id),
        ).fetchone()
    if not found:
        raise ApiFailure("FORBIDDEN", 403)
