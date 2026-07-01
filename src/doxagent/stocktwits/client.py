"""HTTP client for Stocktwits symbol stream polling."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urlparse

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.schema import StocktwitsPage, normalize_symbol


class StocktwitsClientError(RuntimeError):
    """Structured external-client failure for run observability."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        rate_limited: bool = False,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.rate_limited = rate_limited
        self.retryable = retryable


class StocktwitsPageClient(Protocol):
    def fetch_symbol_page(
        self,
        *,
        symbol: str,
        max_message_id: str | None = None,
        page_size: int = 30,
    ) -> StocktwitsPage:
        ...


class StocktwitsHTTPTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        ...


class RequestRateLimiter:
    """Simple process-local request spacing guard."""

    def __init__(
        self,
        *,
        min_interval_seconds: float,
        monotonic: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.monotonic = monotonic
        self.sleep = sleep
        self._last_request_at: float | None = None

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            self._last_request_at = float(self.monotonic())
            return
        now = float(self.monotonic())
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                self.sleep(remaining)
                now = float(self.monotonic())
        self._last_request_at = now


class StocktwitsHTTPClient:
    """Low-frequency REST client for the public Stocktwits symbol stream API."""

    def __init__(
        self,
        settings: DoxAgentSettings | None = None,
        *,
        client: StocktwitsHTTPTransport | None = None,
        rate_limiter: RequestRateLimiter | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.settings = settings or DoxAgentSettings()
        self.client = client or UrllibStocktwitsTransport(
            timeout=self.settings.stocktwits_request_timeout_seconds
        )
        self.rate_limiter = rate_limiter or RequestRateLimiter(
            min_interval_seconds=self.settings.stocktwits_min_request_interval_seconds
        )
        self.sleep = sleep

    def fetch_symbol_page(
        self,
        *,
        symbol: str,
        max_message_id: str | None = None,
        page_size: int = 30,
    ) -> StocktwitsPage:
        normalized_symbol = normalize_symbol(symbol)
        url = self._symbol_url(normalized_symbol)
        params: dict[str, str | int] = {"limit": page_size}
        if max_message_id is not None:
            params["max"] = max_message_id
        headers = self._headers()
        max_attempts = max(1, self.settings.stocktwits_max_retries)
        last_error: StocktwitsClientError | None = None
        for attempt in range(max_attempts):
            self.rate_limiter.wait()
            try:
                response = self.client.get(url, params=params, headers=headers)
                if response.status_code == 429:
                    raise StocktwitsClientError(
                        "Stocktwits rate limit response.",
                        code="rate_limited",
                        rate_limited=True,
                    )
                if response.status_code >= 500:
                    raise StocktwitsClientError(
                        f"Stocktwits upstream HTTP {response.status_code}.",
                        code="upstream_http_error",
                    )
                if response.status_code >= 400:
                    body = response.text.replace("\n", " ")[:500]
                    raise StocktwitsClientError(
                        f"Stocktwits HTTP {response.status_code}: {body}",
                        code=_http_error_code(response.status_code, body),
                        retryable=False,
                    )
                return _page_from_response(response)
            except httpx.TimeoutException as exc:
                last_error = StocktwitsClientError(
                    f"Stocktwits request timed out: {exc}",
                    code="timeout",
                )
            except httpx.RequestError as exc:
                last_error = StocktwitsClientError(
                    f"Stocktwits network error: {exc}",
                    code="network_error",
                )
            except StocktwitsClientError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            if attempt >= max_attempts - 1:
                break
            self.sleep(_retry_delay(self.settings.stocktwits_retry_base_delay_seconds, attempt))
        if last_error is not None:
            raise last_error
        raise StocktwitsClientError("Stocktwits request failed.", code="unknown_error")

    def _symbol_url(self, symbol: str) -> str:
        base_url = self.settings.stocktwits_public_base_url.rstrip("/")
        path = self.settings.stocktwits_public_path_template.format(symbol=symbol)
        return base_url + path

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": self.settings.stocktwits_accept_language,
            "Origin": "https://stocktwits.com",
            "Referer": "https://stocktwits.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": self.settings.stocktwits_user_agent,
        }


class UrllibStocktwitsTransport:
    """urllib transport used by default because Cloudflare blocks httpx here."""

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request = httpx.Request("GET", url, params=params, headers=headers)
        request_url = _url_with_params(url, params)
        urllib_req = urllib_request.Request(request_url, headers=headers or {}, method="GET")
        try:
            with urllib_request.urlopen(urllib_req, timeout=self.timeout) as response:
                return httpx.Response(
                    response.status,
                    content=response.read(),
                    headers=dict(response.headers.items()),
                    request=request,
                )
        except urllib_error.HTTPError as exc:
            return httpx.Response(
                exc.code,
                content=exc.read(),
                headers=dict(exc.headers.items()),
                request=request,
            )
        except urllib_error.URLError as exc:
            raise httpx.RequestError(str(exc.reason), request=request) from exc


def _page_from_response(response: httpx.Response) -> StocktwitsPage:
    try:
        payload = response.json()
    except ValueError as exc:
        raise StocktwitsClientError(
            f"Stocktwits returned invalid JSON from {urlparse(str(response.url)).path}.",
            code="invalid_json",
            retryable=False,
        ) from exc
    if not isinstance(payload, dict):
        raise StocktwitsClientError(
            "Stocktwits response schema error: top-level payload is not an object.",
            code="schema_error",
            retryable=False,
        )
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise StocktwitsClientError(
            "Stocktwits response schema error: missing messages array.",
            code="schema_error",
            retryable=False,
        )
    cursor = payload.get("cursor")
    cursor_more: bool | None = None
    next_max_id: str | None = None
    if isinstance(cursor, dict):
        raw_more = cursor.get("more")
        cursor_more = bool(raw_more) if raw_more is not None else None
        next_max_id = _str_or_none(cursor.get("max"))
    return StocktwitsPage(
        messages=[dict(item) for item in messages if isinstance(item, dict)],
        cursor_more=cursor_more,
        next_max_id=next_max_id,
        raw_response=dict(payload),
    )


def parse_stocktwits_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _retry_delay(base_delay_seconds: float, attempt: int) -> float:
    return max(0.0, base_delay_seconds) * float(2**attempt)


def _url_with_params(url: str, params: dict[str, str | int] | None) -> str:
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib_parse.urlencode(params)}"


def _http_error_code(status_code: int, body: str) -> str:
    lowered = body.lower()
    if status_code == 403 and ("cloudflare" in lowered or "attention required" in lowered):
        return "cloudflare_blocked"
    return "http_error"


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "RequestRateLimiter",
    "StocktwitsClientError",
    "StocktwitsHTTPClient",
    "StocktwitsHTTPTransport",
    "StocktwitsPageClient",
    "UrllibStocktwitsTransport",
    "parse_stocktwits_datetime",
]
