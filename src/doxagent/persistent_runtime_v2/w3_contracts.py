"""W3 business contracts; no model instructions or orchestration identifiers."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from .schema import (
    PolicyWatchAction,
    RuntimeFactCandidate,
    RuntimeV2Model,
    W3NoveltyResult,
    W3PolicyResult,
    W3SelectedCondition,
)

W3_CONTRACT = "persistent-runtime.w3.v4"
TRADE_PHASE = "trade_decision"
OPERATIONS_PHASE = "post_decision_operations"


class W3ExpertDecision(RuntimeV2Model):
    direction: Literal["LONG", "SHORT"]
    prior_expectation: str = Field(min_length=1, max_length=4000)
    expectation_delta: str = Field(min_length=1, max_length=4000)


class W3TradeDecision(RuntimeV2Model):
    decision: Literal["NO_TRADE", "POLICY_TRADE", "EXPERT_TRADE"]
    reason: str = Field(min_length=1, max_length=4000)
    policy_trade: W3SelectedCondition | None
    expert_trade: W3ExpertDecision | None

    @model_validator(mode="after")
    def mutually_exclusive(self):
        expected = {
            "NO_TRADE": (False, False),
            "POLICY_TRADE": (True, False),
            "EXPERT_TRADE": (False, True),
        }[self.decision]
        if (self.policy_trade is not None, self.expert_trade is not None) != expected:
            raise ValueError("decision requires exactly its selected trade branch")
        return self


class MessagePinAction(RuntimeV2Model):
    action: Literal["SET", "CANCEL"]
    pin_id: str | None
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def binds_current_message(self):
        if (self.action == "SET" and self.pin_id is not None) or (
            self.action == "CANCEL" and not self.pin_id
        ):
            raise ValueError("SET binds current message; CANCEL requires an existing pin_id")
        return self


class W3OperationsResult(RuntimeV2Model):
    novelty: W3NoveltyResult
    policy: W3PolicyResult
    delta_candidates: list[RuntimeFactCandidate] = Field(max_length=12)
    watch_actions: list[PolicyWatchAction]
    pin_actions: list[MessagePinAction]

    @model_validator(mode="after")
    def fact_shape(self):
        if (self.novelty.result.value == "NEW") != bool(self.delta_candidates):
            raise ValueError("NEW requires facts; OLD forbids facts (operations only)")
        return self


def parse_w3_trade_decision(value: str | dict) -> W3TradeDecision:
    return W3TradeDecision.model_validate(json.loads(value) if isinstance(value, str) else value)


def parse_w3_operations(value: str | dict) -> tuple[W3OperationsResult, list[dict]]:
    payload = json.loads(value) if isinstance(value, str) else dict(value)
    issues = []
    for key, model in (("watch_actions", PolicyWatchAction), ("pin_actions", MessagePinAction)):
        accepted = []
        actions = payload.get(key, [])
        if not isinstance(actions, list):
            issues.append({"field": key, "index": None, "error": "must be a list"})
            actions = []
        for index, raw in enumerate(actions):
            try:
                accepted.append(model.model_validate(raw).model_dump(mode="json"))
            except ValueError as exc:
                issues.append({"field": key, "index": index, "error": str(exc)[:1000]})
        payload[key] = accepted
    return W3OperationsResult.model_validate(payload), issues
