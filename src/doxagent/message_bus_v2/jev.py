"""Bailian System One API: one article, keyed independent ticker questions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .monitoring_terms import TickerMonitoringTerms
from .schema import RawMessageInput


class BailianDecisionClient:
    MODEL = "decision-model-preview"
    URL = "https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone"

    def __init__(
        self, key: str, *, url: str = URL, timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.key = key
        self.url = url
        self.timeout = timeout
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def classify(
        self, message: RawMessageInput, terms: Mapping[str, TickerMonitoringTerms]
    ) -> dict[str, float]:
        if len(terms) > 16:
            raise ValueError("Decision model batch cannot exceed 16 tickers")
        if not terms:
            return {}
        questions = {
            ticker: {
                "type": "noul",
                "instructions": f"Is this news relevant to {ticker}?",
                "criteria": {
                    "true": definition.definition.relevant,
                    "false": definition.definition.irrelevant,
                },
            }
            for ticker, definition in terms.items()
        }
        state = {
            "title": message.title or "",
            "summary": message.summary or "",
            "body": message.body or "",
            "source": message.publisher_name or message.source or "",
            "language": message.metadata.get("content_language") or "",
        }
        response = await self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.key}"},
            json={"model": self.MODEL, "state": state, "questions": questions},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload: Any = response.json()
        answers = payload.get("answers", {}) if isinstance(payload, dict) else {}
        if not isinstance(answers, dict):
            return {}
        values = {}
        for ticker in terms:
            answer = answers.get(ticker)
            if not isinstance(answer, dict) or answer.get("type") != "noul":
                continue
            number = answer.get("noul")
            if type(number) in (int, float) and 0 <= number <= 1:
                values[ticker] = float(number)
        return values


# Compatibility for existing worker annotations and external imports. No OpenRouter calls.
JevClient = BailianDecisionClient
