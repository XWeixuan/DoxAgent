"""One explicit outbound notification to the configured owner's WeChat account."""

from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .client import ReadError

TOOL_NAME = "send_wechat_notification"
SEND_URL = "https://www.pushplus.plus/send"
DESCRIPTION = (
    "Send a WeChat notification to the fixed DoxAgent owner after an inspection, "
    "on an anomaly, or when reporting. Provide a plain-text title, content and "
    "INFO/WARNING/CRITICAL level. Recipient and wechat channel cannot be changed. "
    "This sends a real message; repeated calls can duplicate it. Accepted means "
    "PushPlus queued the request, not that WeChat delivery is confirmed."
)


class NotificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=100, description="Short notification title.")
    content: str = Field(
        min_length=1, max_length=10000, description="Plain text with line breaks for clarity."
    )
    level: Literal["INFO", "WARNING", "CRITICAL"] = Field(description="Notification severity.")


class WeChatNotifications:
    def __init__(self, token, *, transport=None):
        self._token = token.strip()
        if not self._token:
            raise ValueError("A configured PushPlus token is required")
        self._http = httpx.AsyncClient(
            timeout=10,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            transport=transport,
        )

    async def send(self, arguments):
        message = NotificationInput.model_validate(arguments)
        try:
            response = await self._http.post(
                SEND_URL,
                json={
                    "token": self._token,
                    "title": f"[{message.level}] {message.title}",
                    "content": message.content,
                    "channel": "wechat",
                    "template": "txt",
                },
            )
            response.raise_for_status()
            wire = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # A timeout or broken response may occur after acceptance. Never retry
            # automatically or include provider/request text containing credentials.
            raise ReadError(
                "NOTIFICATION_DELIVERY_UNKNOWN",
                "PushPlus delivery is unconfirmed. Check before resending; no retry was made.",
            ) from exc
        if not isinstance(wire, dict) or type(wire.get("code")) is not int:
            raise ReadError(
                "NOTIFICATION_DELIVERY_UNKNOWN",
                "Invalid PushPlus response; check before resending.",
            )
        if wire["code"] != 200:
            raise ReadError(
                "PUSHPLUS_REJECTED", f"PushPlus rejected the notification (code {wire['code']})."
            )
        short_code = wire.get("data")
        if not isinstance(short_code, str) or not short_code or len(short_code) > 128:
            raise ReadError(
                "NOTIFICATION_DELIVERY_UNKNOWN",
                "PushPlus returned no message receipt; check before resending.",
            )
        return {
            "data": {
                "status": "accepted",
                "delivery": "unconfirmed",
                "channel": "wechat",
                "level": message.level,
                "provider_code": wire["code"],
                "short_code": short_code,
            },
            "meta": {"submitted_at": datetime.now(UTC).isoformat()},
        }

    async def close(self):
        await self._http.aclose()
