"""Permissive O2 wire DTOs used before Canonical persistence normalization.

These models intentionally do not inherit the strict Published contracts.  O2
output is decoded here first so one malformed field cannot quarantine an entire
otherwise usable Event.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class O2WireModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class O2FactRevisionWire(O2WireModel):
    fact_id: Any = None
    proposition: Any = None
    assertion_state: Any = None
    subject_time: Any = None
    fact_occurred_at: Any = None
    fact_occurrence_time_precision: Any = None
    consumes_delta_ids: Any = Field(default_factory=list)


class O2EventRevisionWire(O2WireModel):
    event_id: Any = None
    ticker: Any = None
    title: Any = None
    event_type: Any = None
    occurred_at: Any = None
    occurrence_time_precision: Any = None
    canonical_summary: Any = None
    known_event_summary: Any = None
    is_important: Any = None
    include_in_reference_view: Any = None
    facts: Any = Field(default_factory=list)
    price_analysis: Any = None
    related_event_ids: Any = Field(default_factory=list)
    supersedes_event_id: Any = None
    derived_from_event_ids: Any = Field(default_factory=list)
    status: Any = None


class O2ResidualResolutionWire(O2WireModel):
    delta_id: Any = None
    resolution: Any = None
    disposition: Any = None
    target_event_id: Any = None
    target_fact_id: Any = None


class O2DateLedgerWire(O2WireModel):
    delta_id: Any = None
    runtime_atomic_id: Any = None
    runtime_package_id: Any = None
    source_message_id: Any = None
    candidates: Any = Field(default_factory=list)
    selected_date: Any = None
    selected_precision: Any = None
    semantic_role: Any = None
    status: Any = None
    event_id: Any = None
    fact_id: Any = None
    subject_time: Any = None
    note: Any = None


class O2ReferenceDecisionWire(O2WireModel):
    event_id: Any = None
    reviewed_at: Any = None
    review_mode: Any = None
    candidate_reason: Any = None
    changed: Any = None
    include_in_reference_view: Any = None
    is_important: Any = None
    reference_view_basis: Any = None
    next_review_at: Any = None
    note: Any = None


class O2RevisionBundleWire(O2WireModel):
    contract_version: Any = None
    run_id: Any = None
    ticker: Any = None
    base_library_version: Any = None
    delta_batch_ids: Any = Field(default_factory=list)
    event_revisions: Any = Field(default_factory=list)
    event_retirements: Any = Field(default_factory=list)
    residual_delta_resolutions: Any = Field(default_factory=list)
    reference_review_decisions: Any = Field(default_factory=list)
    date_resolution_ledger: Any = Field(default_factory=list)
    reference_view_decision_ledger: Any = Field(default_factory=list)
