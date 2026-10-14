"""One real W1/W2 preparation pass for the user-selected missing NASA case."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

from doxagent.event_library.provider import EventDetailSnapshot, KnownEventIndexSnapshot
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus, SourceMessageEnvelope
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import BailianRuntimeResponsesClient
from doxagent.workflows.codex_document3.consumer_contracts import (
    parse_policy_set,
    parse_runtime_projection,
    policy_details,
)

from .dataset import read_json, sanitized_case, write_json


class _Events:
    def __init__(self, index, details):
        self.index = KnownEventIndexSnapshot.model_validate(index)
        if "canonical_events" in details:
            details = dict(
                ticker=self.index.ticker,
                version=self.index.version,
                requested_event_ids=details.get("requested_event_ids", []),
                # The saved W1 business projection omits these three storage
                # fields. They are excluded again by W1's payload projector.
                events=[
                    dict(
                        ticker=self.index.ticker,
                        is_important=False,
                        include_in_reference_view=False,
                        **event,
                    )
                    for event in details["canonical_events"]
                ],
                missing_event_ids=details.get("missing_event_ids", []),
            )
        self.events = EventDetailSnapshot.model_validate(details)

    def current_index(self, ticker):
        return self.index

    def details(self, ticker, version, event_ids):
        known = {e.event_id: e for e in self.events.events}
        return EventDetailSnapshot(
            ticker=ticker,
            version=version,
            requested_event_ids=event_ids,
            events=[known[key] for key in event_ids if key in known],
            missing_event_ids=[key for key in event_ids if key not in known],
        )

    def max_event_numeric_id(self, *_):
        return max([int(e.event_id[1:]) for e in self.events.events] or [0])


class _Policies:
    def __init__(self, policy, projection):
        self.policy = parse_policy_set(policy)
        self.projection = parse_runtime_projection(projection)

    def current_projection(self, ticker):
        return self.projection

    def details(self, ticker, version, policy_ids):
        return policy_details(self.policy, ticker=ticker, version=version, policy_ids=policy_ids)

    def activation(self, ticker, version, policy_id):
        return next((p for p in self.projection.policies if p.policy_id == policy_id), None)

    def decision(self, ticker, version, policy_id):
        return next((p.decision for p in self.policy.policies if p.policy_id == policy_id), None)


def prepare_nasa(dataset: Path, *, responses=None) -> dict:
    """The current hot-plane owns reasoning; no model outputs are fabricated here."""
    dataset = Path(dataset)
    path = dataset / "inputs" / "EXTRA-RKLB-NASA" / "case.json"
    item = read_json(path)
    if item.get("original_case") and item.get("w1_final") and item.get("w2_final"):
        return dict(case_key=item["case_key"], status="ALREADY_PREPARED")
    base = read_json(dataset / "inputs" / item["research_basis_case"] / "case.json")
    original = base["original_case"]
    folder = dataset / "research" / item["research_key"]
    source_path = path.parent / "evidence" / "sources" / "S1.md"
    collected = source_path.read_text(encoding="utf-8")
    body = collected.split("## Publicly visible body, paraphrased", 1)[-1].split("\n## ", 1)[0]
    body = body.strip() + (
        "\n\n[Source availability: PARTIAL. Only the publicly visible opening is provided; "
        "later paywalled passages are unavailable and must not be inferred.]"
    )
    t0 = datetime.fromisoformat(item["evidence_cutoff_at"])
    published = t0 - timedelta(minutes=5)
    source = SourceMessageEnvelope(
        source_message_id="backtest-nasa-20261006",
        source_id="bloomberg-news",
        binding_id="backtest-rklb",
        stream_item_id="backtest-nasa-20261006",
        member_count=1,
        member_message_ids=["backtest-nasa-20261006"],
        url="https://news.bloomberglaw.com/federal-contracting/"
        "nasa-eyes-bulk-rocket-buy-for-moon-base-amid-spacex-phaseout",
        published_at=published,
        collected_at=t0,
        message_bus_event_time=t0,
        snapshot=dict(
            ticker="RKLB",
            title="NASA Eyes Bulk Rocket Buy for Moon Base Amid SpaceX Phaseout",
            body=body,
            source_name="bloomberg",
        ),
    )
    prepared = dataset / "preparation" / "nasa_w1_w2"
    journal = RuntimeJournal(prepared / "runtime.sqlite3", clock=lambda: t0)
    repo = SQLitePersistentRuntimeV2Repository(journal.path)
    owned = responses is None
    if responses is None:
        from doxagent.settings import DoxAgentSettings

        settings = DoxAgentSettings()
        responses = BailianRuntimeResponsesClient(
            api_key=settings.require_dashscope_api_key(),
            base_url=settings.dashscope_chat_base_url,
            model=settings.persistent_runtime_v2_model,
            reasoning_effort=settings.persistent_runtime_v2_reasoning_effort,
            timeout_seconds=settings.persistent_runtime_v2_timeout_seconds,
            session_cache=settings.persistent_runtime_v2_session_cache_enabled,
        )
    state_path = path.parent / "historical_state.json"
    state = read_json(state_path) if state_path.exists() else {}
    details = state.get("event_details") or read_json(folder / "events.json")
    events = _Events(original["frozen_inputs"]["index"], details)
    policies = _Policies(
        read_json(folder / "policy_set.original.json"), original["frozen_inputs"]["projection"]
    )
    service = PersistentRuntimeV2Service(
        repository=repo,
        journal=journal,
        responses=responses,
        known_events=events,
        policies=policies,
        dispatch_effects=False,
        retry_delays_seconds=(0, 0),
    )
    try:
        # Resume the saved upstream turns after a preparation/provider failure.
        existing = repo.get_case_by_source(source.source_message_id)
        if existing and existing.status in {
            RuntimeCaseStatus.FAILED,
            RuntimeCaseStatus.UNAVAILABLE,
        }:
            existing.status = RuntimeCaseStatus.RUNNING
            existing.error_code = None
            existing.error_message = None
            repo.save_case(existing)
        result = service.execute_message(source, admitted_at=t0)
        turns = [turn.model_dump(mode="json") for turn in repo.list_turns(result.case_id)]
        write_json(prepared / "turns.json", turns)
        write_json(prepared / "case.json", result.model_dump(mode="json"))
        if result.w1_final is None or result.w2_final is None:
            raise RuntimeError(f"NASA W1/W2 failed: {result.error_code}: {result.error_message}")
        safe = sanitized_case(result.model_dump(mode="json"))
        safe["version_pin"] = item["version_pin"]
        item.update(
            original_case=safe,
            source=source.model_dump(mode="json"),
            w1_final=result.w1_final.model_dump(mode="json"),
            w2_final=result.w2_final.model_dump(mode="json"),
            w2_alignment=result.w2_alignment.model_dump(mode="json")
            if result.w2_alignment
            else None,
            preparation_status="PREPARED",
            source_body_availability="PARTIAL",
            source_fixture_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        )
        write_json(path, item)
        state["event_details"] = details
        state.setdefault("recent_trade_intents", [])
        state["recent_trade_intents"] = [
            h
            for h in state["recent_trade_intents"]
            if t0 - timedelta(hours=72)
            <= datetime.fromisoformat(h["initiated_at"].replace("Z", "+00:00"))
            < t0
        ]
        state["history_matches"] = []
        state["reconstruction_notes"] = [
            "Synthetic NASA case uses nearest prepared pre-cutoff RKLB research; "
            "source is partial.",
        ]
        write_json(state_path, state)
        return dict(
            case_key=item["case_key"],
            status="PREPARED",
            model=responses.model,
            turns=len(turns),
            w1=item["w1_final"],
            w2=item["w2_final"],
        )
    finally:
        service.close()
        if owned and hasattr(responses, "close"):
            responses.close()
