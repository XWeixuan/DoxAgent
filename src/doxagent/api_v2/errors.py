from __future__ import annotations

from typing import Any


class ApiFailure(Exception):
    def __init__(
        self,
        code: str,
        status: int = 400,
        *,
        retryable: bool = False,
        content_id: str | None = None,
    ) -> None:
        self.code, self.status, self.retryable = code, status, retryable
        self.content_id = content_id

    def payload(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.code,
                "retryable": self.retryable,
                "request_id": request_id,
                "fields": [],
                "content_id": self.content_id,
            }
        }
