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

W3_LEGACY_CONTRACT = "persistent-runtime.w3.v4"
W3_CONTRACT = "persistent-runtime.w3.v5"
TRADE_PHASE = "trade_decision"
OPERATIONS_PHASE = "post_decision_operations"


def is_two_phase_contract(version):
    return version in {W3_LEGACY_CONTRACT, W3_CONTRACT}


def is_unified_contract(version):
    return version == W3_CONTRACT


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


class W3DirectionDecision(RuntimeV2Model):
    decision: Literal["LONG", "SHORT", "NO_TRADE"]
    reason: str = Field(min_length=1, max_length=4000)
    prior_expectation: str | None = Field(min_length=1, max_length=4000)
    expectation_delta: str | None = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def direction_analysis(self):
        if not self.reason.strip():
            raise ValueError("reason cannot be blank")
        for name in ("prior_expectation", "expectation_delta"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} cannot be blank")
            if self.decision != "NO_TRADE" and value is None:
                raise ValueError(f"{name} is required for LONG/SHORT")
        return self


class W3OperationsResultV5(RuntimeV2Model):
    novelty: W3NoveltyResult
    delta_candidates: list[RuntimeFactCandidate] = Field(max_length=12)
    watch_actions: list[PolicyWatchAction]
    pin_actions: list[MessagePinAction]

    @model_validator(mode="after")
    def fact_shape(self):
        if not self.novelty.reason.strip():
            raise ValueError("novelty.reason cannot be blank")
        if (self.novelty.result.value == "NEW") != bool(self.delta_candidates):
            raise ValueError("NEW requires facts; OLD forbids facts (operations only)")
        return self


def contract_models(version):
    if is_unified_contract(version):
        return W3DirectionDecision, W3OperationsResultV5
    if version == W3_LEGACY_CONTRACT:
        return W3TradeDecision, W3OperationsResult
    raise ValueError(f"unsupported W3 execution contract: {version}")


def asset_contract_version(assets):
    """An unmarked existing prompt pack remains v4 until its owner publishes v5."""
    marker = assets.get("w3/contract.json")
    version = json.loads(marker)["w3_contract_version"] if marker else W3_LEGACY_CONTRACT
    contract_models(version)
    return version


def parse_w3_trade_decision(value: str | dict, *, version=W3_LEGACY_CONTRACT):
    model, _ = contract_models(version)
    return model.model_validate(json.loads(value) if isinstance(value, str) else value)


def parse_w3_operations(value: str | dict, *, version=W3_LEGACY_CONTRACT):
    payload = json.loads(value) if isinstance(value, str) else dict(value)
    if is_unified_contract(version) and any(
        key not in payload for key in ("watch_actions", "pin_actions")
    ):
        raise ValueError("v5 requires watch_actions and pin_actions, including empty lists")
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
    _, model = contract_models(version)
    return model.model_validate(payload), issues
