"""Version-dispatched consumer contracts. Canonical research is never downcast."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from doxagent.workflows.codex_document2.schema import Document2Document, Document2DocumentV21

from .schema import PolicyDetailSnapshot, PolicySet
from .schema_v21 import ConditionV3, PolicySetV3, PolicyV3


class ConsumerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectedConditionV5(ConditionV3):
    condition_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimePolicyRecordV5(PolicyV3):
    activation_conditions: list[ProjectedConditionV5] = Field(min_length=1)

    @property
    def condition_ids(self):
        return [c.condition_id for c in self.activation_conditions]


class RuntimePolicyProjectionV5(ConsumerModel):
    schema_version: Literal["document3.runtime_projection.v5"] = "document3.runtime_projection.v5"
    consumer_contract: Literal["persistent-runtime.v2.condition-policy.v1"] = (
        "persistent-runtime.v2.condition-policy.v1"
    )
    ticker: str
    policy_set_version: int = Field(ge=1)
    as_of: datetime
    published_at: datetime
    publication_state: Literal["COMPLETE", "PARTIAL"]
    policies: list[RuntimePolicyRecordV5]


class PolicyDetailSnapshotV3(ConsumerModel):
    ticker: str
    policy_set_version: int = Field(ge=1)
    requested_policy_ids: list[str]
    policies: list[PolicyV3] = Field(default_factory=list)
    missing_policy_ids: list[str] = Field(default_factory=list)


def _payload(value):
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def parse_document2(value):
    value = _payload(value)
    models = {"document2.v2": Document2Document, "document2.v2.1": Document2DocumentV21}
    version = value.get("schema_version")
    if version not in models:
        raise ValueError(f"unsupported Document2 schema: {version!r}")
    return models[version].model_validate(value)


def parse_policy_set(value):
    value = _payload(value)
    if value.get("schema_version") == "document3.v3":
        return PolicySetV3.model_validate(value)
    if value.get("schema_version") in {"document3.v2", "document3.v2.1", "document3.v2.2"}:
        return PolicySet.model_validate(value)
    raise ValueError(f"unsupported Document3 schema: {value.get('schema_version')!r}")


def parse_runtime_projection(value):
    value = _payload(value)
    if value.get("schema_version") == "document3.runtime_projection.v5":
        return RuntimePolicyProjectionV5.model_validate(value)
    from .runtime_projection import upgrade_runtime_projection

    return upgrade_runtime_projection(value)


def policy_details(policy_set, *, ticker, version, policy_ids):
    requested = list(dict.fromkeys(p.strip() for p in policy_ids if p.strip()))
    rows = {p.policy_id: p for p in policy_set.policies} if policy_set else {}
    model = PolicyDetailSnapshotV3 if isinstance(policy_set, PolicySetV3) else PolicyDetailSnapshot
    return model(
        ticker=ticker.upper(),
        policy_set_version=version,
        requested_policy_ids=requested,
        policies=[rows[p] for p in requested if p in rows],
        missing_policy_ids=[p for p in requested if p not in rows],
    )
