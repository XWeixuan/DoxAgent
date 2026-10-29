"""Trade-first W3 execution using the existing effects and output transaction."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from doxagent.codex_worker.schema import ThreadForkRequest
from doxagent.w3_timing import observed
from doxagent.workflows.codex_document3.schema import PolicyDecision

from . import policy_consumption
from .fencing import assert_write_lease
from .journal import digest, encode
from .message_pin import apply_pin_actions
from .policy_watch import apply_v4_actions
from .schema import (
    ArchiveRecord,
    ArchiveResultBasis,
    BadcaseRecord,
    RuntimeEffect,
    RuntimePrimaryRoute,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    TradeDecisionOrigin,
    TradeRecord,
    W3CoverageGapRecord,
)
from .trade_output import TradeOutputService
from .w3 import W3Error
from .w3_assets import instruction_assets, research_assets
from .w3_context import (
    build_case_payload,
    case_labels,
    event_matches,
    freeze_history,
    global_snapshot,
    history_payload,
    policy_associations,
    resolve_event_subjects,
    sync_event_links,
)
from .w3_contracts import (
    OPERATIONS_PHASE,
    TRADE_PHASE,
    W3_LEGACY_CONTRACT,
    contract_models,
    is_unified_contract,
)
from .w3_models import execution_profile
from .w3_research_store import ENTRY
from .w3_threads import W3ThreadRegistry


class W3Execution:
    def __init__(self, service):
        if service.journal is None:
            raise ValueError("W3 v4 requires the durable Runtime journal")
        self.service = service
        self.journal = service.journal
        self.registry = W3ThreadRegistry(self.journal)

    @property
    def agent(self):
        return self.service._w3_agent

    def prepare_public(self, ticker):
        """Publication/startup preparation, never a business Case or trade turn."""
        from types import SimpleNamespace

        from .schema import RuntimeVersionPin

        active = self.journal.get("w3_entry", "pending") or self.journal.get("execution", "active")
        if not active or not self.service.input_snapshot_loader:
            return
        bundle = self.service.execution_bundles.get(active)
        assets = instruction_assets(bundle["assets"])
        if "w3/AGENTS.md" not in assets:
            return
        snapshot = self.service.input_snapshot_loader(ticker)
        pin = RuntimeVersionPin(
            event_library_root=snapshot.event_library_root,
            document1_run_id=snapshot.document1_run_id,
            document2_run_id=snapshot.document2_run_id,
            event_library_version=snapshot.index.version,
            policy_set_version=snapshot.projection.policy_set_version,
            runtime_projection_version=snapshot.projection.policy_set_version,
            provisional_snapshot_version=0,
        )
        identity = digest([active, pin.model_dump(mode="json"), "instructions-only-v1"])
        new_public = bundle.get("contracts", {}).get("w3_entry_version") == ENTRY
        business_contract = (
            bundle.get("contracts", {}).get("w3_contract_version") or W3_LEGACY_CONTRACT
        )
        if self.journal.get("w3_public_preparation", ticker) == identity and not new_public:
            return
        asset_case = SimpleNamespace(
            ticker=ticker,
            w3_contract_version=business_contract,
            version_pin=pin,
            frozen_inputs=dict(w3_assets=assets, w3_contracts=bundle.get("contracts", {})),
        )
        from .w3 import research_pin_key

        if not new_public or not self.journal.get("w3_research_v2", research_pin_key(asset_case)):
            self.agent.prepare_public_assets(asset_case)
        initializing = False
        for model, effort in (("gpt-6-luna", "max"), ("gpt-6.1-sol", "medium")):
            profile = dict(
                model=model, effort=effort, provider=getattr(self.agent, "_model_provider", None)
            )
            case = SimpleNamespace(
                ticker=ticker,
                w3_contract_version=business_contract,
                case_id="public-" + ticker + "-" + digest(profile)[:16],
                version_pin=pin,
                source=SimpleNamespace(message_bus_event_time=self.journal.clock()),
                frozen_inputs=dict(
                    w3_assets=assets,
                    w3_contracts=bundle.get("contracts", {}),
                    w3_execution_profile=profile,
                ),
            )
            environment = self.agent.environment
            capabilities = (
                [environment.web_search_mode, environment.compatibility()]
                if environment.mode != "PRODUCTION"
                else [
                    "live",
                    {"read_only": True, "data_mcp_enabled": True, "allow_subagents": False},
                ]
            )
            layout = ENTRY if new_public else "instructions-only-v1"
            compatibility = digest(
                [
                    assets,
                    profile,
                    *capabilities,
                    business_contract,
                    *(
                        [
                            bundle["contracts"].get("w3_trade_schema_hash"),
                            bundle["contracts"].get("w3_operations_schema_hash"),
                        ]
                        if is_unified_contract(business_contract)
                        else []
                    ),
                    layout,
                ]
            )
            key = digest([None if new_public else ticker, compatibility, layout])
            template, acquired = self.registry.template(ticker, key, compatibility)
            if template["status"] != "READY":
                if not acquired:
                    initializing = True
                    continue
                if self.service.coordinator is not None:
                    self.service.coordinator._background.submit(
                        self._initialize_template, case, template
                    )
                    initializing = True
                else:
                    self._initialize_template(case, template)
                    if new_public and hasattr(self.agent, "configure_ready"):
                        prepared = self.agent.configure_ready(case, template)
                        if prepared and not prepared.get("ready"):
                            initializing = True
            elif new_public and hasattr(self.agent, "configure_ready"):
                prepared = self.agent.configure_ready(case, template)
                if prepared and not prepared.get("ready"):
                    initializing = True
        sync_event_links(self.journal, ticker, pin.event_library_root, pin.event_library_version)
        if not initializing:
            self.journal.set("w3_public_preparation", ticker, identity)
            if new_public:
                self.journal.set("w3_entry_active", ticker, active)

    @observed("case.events")
    def _events(self, case):
        from .service import _w1_canonical_event_business_payload, _w1_provisional_business_payload

        frozen = self.journal.get("round_inputs", f"{case.case_id}:W1:R2", {})
        from .w1_r2_context import frozen_event_details

        details = frozen_event_details(frozen)
        events = details.get("canonical_events", []) + details.get("provisional_events", [])
        if not events and case.w1_final.reference_ids:
            loaded = self.service._known_for_case(case).details(
                case.ticker, case.version_pin.event_library_version, case.w1_final.reference_ids
            )
            events = (
                [_w1_canonical_event_business_payload(e) for e in loaded.events] if loaded else []
            )
            events.extend(
                _w1_provisional_business_payload(p) for p in self.service._visible_provisional(case)
            )
        selected = []
        for eid in case.w1_final.reference_ids:
            row = next(
                (e for e in events if e.get("event_id", e.get("provisional_event_id")) == eid), None
            )
            if row is None:
                raise W3Error("w3_event_evidence_missing", f"Referenced Event {eid} is unavailable")
            selected.append(row)
        return selected

    def _initialize_template(self, case, template):
        try:
            _, job = self.agent.run_phase(
                case=case,
                record=template,
                phase="w3_template",
                payload=dict(
                    template_format="instructions-only-v1",
                )
                if not getattr(self.agent, "entry_enabled", lambda c: False)(case)
                else dict(template_format=ENTRY),
                context=None,
                research=None,
            )
            self.agent.register_template(job.job_id)
            self.registry.ready_template(
                template["record_id"], job.thread_id, template["lease_token"]
            )
            self._ack(job.job_id)
            template["thread_id"] = job.thread_id
            if hasattr(self.agent, "configure_ready"):
                self.agent.configure_ready(
                    case, template, active_public=case.case_id.startswith("public-")
                )
        except BaseException as exc:
            exc.w3_step = "TEMPLATE_INIT"
            with self.journal.transaction() as db:
                db.execute(
                    "UPDATE runtime_v2_w3_threads SET status='FAILED',lease_until=NULL "
                    "WHERE record_id=? AND lease_token=?",
                    (template["record_id"], template["lease_token"]),
                )
            raise
        return template

    @observed("case.thread_prepare")
    def _thread(self, case, context, research, event_keys=None):
        owned = self.registry.owned(case.case_id)
        stage = self.registry.stage(case.case_id, TRADE_PHASE)
        record_id = (stage.get("input_refs") or {}).get("record_id")
        if owned is None and record_id:
            with self.journal.transaction() as db:
                row = db.execute(
                    "SELECT * FROM runtime_v2_w3_threads WHERE record_id=?", (record_id,)
                ).fetchone()
                if row is None or row["owner_case_id"] not in {None, case.case_id}:
                    raise W3Error(
                        "w3_prepare_identity_conflict", "Original Case record unavailable"
                    )
                db.execute(
                    "UPDATE runtime_v2_w3_threads SET owner_case_id=? WHERE record_id=?",
                    (case.case_id, record_id),
                )
                owned = dict(row)
                owned["owner_case_id"] = case.case_id
        accepted_at = datetime.fromisoformat(
            self.registry.stage(case.case_id, TRADE_PHASE)["accepted_at"]
        )
        profile = execution_profile(
            case,
            model=getattr(self.agent, "_model", "gpt-6-luna"),
            effort=getattr(self.agent, "_effort", "max"),
            provider=getattr(self.agent, "_model_provider", None),
            override=getattr(self.agent, "_execution_model_override", None),
        )
        new_entry = getattr(self.agent, "entry_enabled", lambda c: False)(case)
        layout = ENTRY if new_entry else "instructions-only-v1"
        environment = getattr(self.agent, "environment", None)
        capabilities = (
            [environment.web_search_mode, environment.compatibility()]
            if environment and environment.mode != "PRODUCTION"
            else ["live", {"read_only": True, "data_mcp_enabled": True, "allow_subagents": False}]
        )
        compatibility = digest(
            [
                instruction_assets(case.frozen_inputs["w3_assets"]),
                profile,
                *capabilities,
                case.w3_contract_version or W3_LEGACY_CONTRACT,
                *(
                    [
                        case.frozen_inputs.get("w3_contracts", {}).get("w3_trade_schema_hash"),
                        case.frozen_inputs.get("w3_contracts", {}).get("w3_operations_schema_hash"),
                    ]
                    if is_unified_contract(case.w3_contract_version)
                    else []
                ),
                layout,
            ]
        )
        if owned is None:
            owned = self.registry.acquire(
                ticker=case.ticker,
                case_id=case.case_id,
                compatibility=compatibility,
                policy_ids=policy_associations(case.w2_final),
                event_keys=event_keys
                if event_keys is not None
                else resolve_event_subjects(
                    self.journal, case, event_matches(case.model_dump(mode="json"))
                ),
                accepted_at=accepted_at,
            )
        if owned is None:
            template_key = digest([None if new_entry else case.ticker, compatibility, layout])
            template, acquired = self.registry.template(case.ticker, template_key, compatibility)
            if template["status"] != "READY":
                if not acquired:
                    raise W3Error(
                        "w3_template_initializing", "W3 template initialization is active"
                    )
                coordinator = self.service.coordinator
                if coordinator is not None and getattr(self.agent, "supports_prepare", False):
                    coordinator._background.submit(self._initialize_template, case, template)
                    raise W3Error("w3_template_initializing", "Public Template is being prepared")
                self._initialize_template(case, template)
            owned = self.registry.create_case(
                ticker=case.ticker,
                case_id=case.case_id,
                compatibility=compatibility,
                parent_id=template["thread_id"],
                accepted_at=accepted_at,
            )
        refs = self.registry.stage(case.case_id, TRADE_PHASE).get("input_refs") or {}
        refs.update(record_id=owned["record_id"], operation_key="w3-fork:" + owned["record_id"])
        with self.journal.transaction() as db:
            db.execute(
                "UPDATE runtime_v2_w3_stages SET input_refs=? WHERE case_id=? AND phase=?",
                (encode(refs), case.case_id, TRADE_PHASE),
            )
        if not owned["thread_id"] and not getattr(self.agent, "supports_prepare", False):
            child = self.agent.fork_thread(
                ThreadForkRequest(
                    parent_thread_id=owned["parent_id"],
                    run_id=owned["workspace"],
                    idempotency_key="w3-fork:" + owned["record_id"],
                    model=profile["model"],
                    model_provider=profile["provider"],
                )
            )
            owned["thread_id"] = child["thread_id"]
            self.registry.set_thread(owned["record_id"], owned["thread_id"])
        self.registry.link(owned, "POLICY", policy_associations(case.w2_final), case.case_id)
        if new_entry and not owned["thread_id"]:
            self.agent.claim_ready(case, owned, profile)
        return owned

    @observed("case.trade")
    def trade(self, case):
        stage = self.registry.begin_stage(case.case_id, TRADE_PHASE)
        if stage["status"] == "COMMITTED":
            self._restore_trade(case, stage)
            self._sync_route_case(case, {"thread_id": stage["thread_id"]})
            return
        if stage["result_json"] is None:
            if hasattr(self.agent, "queue_market"):
                self.agent.queue_market(case)
            elif hasattr(self.agent, "start_market"):
                self.agent.start_market(case)
            replay = case.frozen_inputs.get("w3_replay")
            membership = freeze_history(
                self.journal, case, datetime.fromisoformat(stage["accepted_at"])
            )
            saved_state = self.journal.get("w3_global_input", case.case_id)
            if saved_state is None:
                with self.journal.transaction(
                    write=False, operation="case.global_input.read"
                ) as db:
                    saved_state = (replay or {}).get("global_state") or global_snapshot(
                        db, case.ticker, self.journal.clock()
                    )
                self.journal.set("w3_global_input", case.case_id, saved_state)
            context = self.agent.prepare_case(case)
            new_entry = getattr(self.agent, "entry_enabled", lambda c: False)(case)
            research = None if new_entry else research_assets(context, self.journal)
            event_keys = resolve_event_subjects(
                self.journal, case, event_matches(case.model_dump(mode="json"))
            )
            record = self._thread(case, context, research, event_keys)
            history, matches = history_payload(
                self.journal, self.service.repository, case, membership
            )
            if replay is not None:
                history = replay.get("recent_trade_intents", history)
                matches = replay.get("history_matches", matches)
            labels = case_labels(
                case,
                history_matches=matches,
                event_keys=event_keys
                if event_keys is not None
                else resolve_event_subjects(
                    self.journal, case, event_matches(case.model_dump(mode="json"))
                ),
                watch_followup=bool(case.frozen_inputs.get("policy_watch_followup")),
            )
            payload = build_case_payload(
                case,
                context,
                events=self._events(case),
                history=history,
                labels=labels,
                state=saved_state,
                journal=self.journal,
            )
            if research:
                payload["research_path"] = "context/research/" + research[0]
                self.journal.set("w3_case_research", case.case_id, payload["research_path"])
            decision, job = self.agent.run_phase(
                case=case,
                record=record,
                phase=TRADE_PHASE,
                payload=payload,
                context=context,
                research=research,
            )
            self.registry.save_result(
                case.case_id,
                TRADE_PHASE,
                decision,
                thread_id=record["thread_id"],
                worker_job_id=job.job_id,
            )
            self._ack(job.job_id)
        else:
            decision = contract_models(case.w3_contract_version)[0].model_validate(
                stage["result_json"]
            )
            record = self.registry.owned(case.case_id) or {"thread_id": stage["thread_id"]}
        case.w3_trade_decision = decision.model_dump(mode="json")
        trade, override = self._trade_record(case, decision)
        operations = RuntimeEffect(
            case_id=case.case_id,
            effect_type=RuntimeSideEffect.W3_POST_DECISION,
            idempotency_key=f"{case.case_id}:W3:{OPERATIONS_PHASE}",
        )
        # The output transaction never waits for the operations model or delivery network.
        prepared_case = self._prepare_case_payload(case)
        with self.journal.transaction() as db:
            assert_write_lease(db)
            outcome = (
                TradeOutputService(self.journal).record_in_transaction(
                    db, case, trade, disposition_override=override
                )
                if trade
                else "NO_TRADE"
            )
            if outcome == "POLICY_AUTHORIZATION_NOT_ADMITTED":
                raise W3Error(
                    "policy_authorization_pending", "Retry the saved decision after admission"
                )
            self.registry.commit_stage(db, case.case_id, TRADE_PHASE, outcome)
            self._restore_trade(
                case,
                dict(
                    result_json=case.w3_trade_decision,
                    committed_at=self.journal.clock().isoformat(),
                    disposition=outcome,
                ),
                persist=False,
            )
            self._save_case_in_transaction(db, case, prepared_case)
            self._enqueue_in_transaction(db, operations)
        if not hasattr(self.service.repository, "path"):
            self.service.repository.save_case(case)
        self._sync_route_case(case, record)
        self.service._wake_effects()
        if getattr(self.agent, "entry_enabled", lambda c: False)(case):
            # A failed stop notification is recovered by the prepared deadline.
            try:
                self.agent.stop_market(case.case_id)
            except Exception as exc:
                self.journal.gap("w3-market-stop", case.ticker, type(exc).__name__, str(exc))

    def _trade_record(self, case, decision):
        if decision.decision == "NO_TRADE":
            return None, None
        common = dict(
            case_id=case.case_id,
            ticker=case.ticker,
            trading_date=case.trading_date,
            source=case.source,
            policy_set_version=case.version_pin.policy_set_version,
            w1_result=case.w1_final,
            w2_result=case.w2_final,
            w3_trade_decision=decision.model_dump(mode="json"),
        )
        if is_unified_contract(case.w3_contract_version) or decision.decision == "EXPERT_TRADE":
            return TradeRecord(
                **common,
                decision_origin=TradeDecisionOrigin.W3,
                w3_case_id="w3:" + case.case_id,
                decision=PolicyDecision(
                    decision.decision
                    if is_unified_contract(case.w3_contract_version)
                    else decision.expert_trade.direction
                ),
            ), None
        selected = decision.policy_trade
        policy = next(
            (
                p
                for p in case.frozen_inputs["projection"]["policies"]
                if p["policy_id"] == selected.policy_id
            ),
            None,
        )
        condition = (
            next(
                (
                    c
                    for c in policy["activation_conditions"]
                    if c["condition_id"] == selected.condition_id
                ),
                None,
            )
            if policy
            else None
        )
        if not condition:
            raise W3Error(
                "selected_condition_invalid", "Selected Condition is outside frozen PolicySet"
            )
        state = next(
            d
            for p in case.frozen_inputs["policy_usage"]
            if p["policy_id"] == selected.policy_id
            for d in p["directions"]
            if d["direction"] == condition["decision"]
        )
        trade = TradeRecord(
            **common,
            decision_origin=TradeDecisionOrigin.POLICY,
            executed_policy_id=selected.policy_id,
            executed_condition_id=selected.condition_id,
            executed_condition_revision=condition["condition_revision"],
            usage_generation=state["usage_generation"],
            matched_condition_ids=[selected.condition_id],
            candidate_policy_ids=case.w2_final.policy_ids,
            decision=PolicyDecision(condition["decision"]),
        )
        override = None
        if self.service.input_snapshot_loader and not policy_consumption.prepare_output_guard(
            case, trade, self.service.input_snapshot_loader(case.ticker)
        ):
            override = "SELECTED_CONDITION_INVALID"
        return trade, override

    @observed("case.operations")
    def operations(self, case):
        trade_stage = self.registry.stage(case.case_id, TRADE_PHASE)
        if not trade_stage or trade_stage["status"] != "COMMITTED":
            raise W3Error("w3_trade_not_committed", "Operations require a committed trade decision")
        stage = self.registry.begin_stage(
            case.case_id, OPERATIONS_PHASE, thread_id=trade_stage["thread_id"]
        )
        if stage["status"] == "COMMITTED":
            case.w3_operations_result = stage["result_json"]
            case.w3_operations_status = "COMMITTED"
            self.service.repository.save_case(case)
            self._sync_route_case(case, {"thread_id": stage["thread_id"]})
            self.registry.release(case.case_id)
            return
        record = self.registry.owned(case.case_id)
        if not record or record["thread_id"] != trade_stage["thread_id"]:
            raise W3Error("w3_case_thread_missing", "Cannot replace the Case thread for operations")
        if stage["result_json"] is None and self.journal.clock() >= datetime.fromisoformat(
            record["expires_at"]
        ):
            raise W3Error("w3_operations_expired", "Operations retry window has expired")
        state = self.journal.get("w3_operations_input", case.case_id)
        if state is None:
            with self.journal.transaction() as db:
                state = case.frozen_inputs.get("w3_replay", {}).get(
                    "global_state"
                ) or global_snapshot(db, case.ticker, self.journal.clock())
                policy_consumption.put_value(db, "w3_operations_input", case.case_id, state)
        if stage["result_json"] is None:
            context = self.agent.prepare_case(case)
            candidate = self.journal.get("candidates", "candidate:" + case.case_id)
            result, job = self.agent.run_phase(
                case=case,
                record=record,
                phase=OPERATIONS_PHASE,
                payload=dict(
                    research_path=self.journal.get("w3_case_research", case.case_id) or "",
                    submitted_decision=trade_stage["result_json"],
                    output_disposition=trade_stage["disposition"],
                    candidate_eligibility={
                        "origin_trade_eligible": candidate["origin_trade_eligible"],
                        "trade_disposition": candidate["trade_disposition"],
                    }
                    if candidate
                    else None,
                    watch=state["watch"],
                    pins=state["pins"],
                ),
                context=context,
            )
            self.registry.save_result(
                case.case_id,
                OPERATIONS_PHASE,
                result,
                thread_id=record["thread_id"],
                worker_job_id=job.job_id,
            )
            self._ack(job.job_id)
        else:
            result = contract_models(case.w3_contract_version)[1].model_validate(
                stage["result_json"]
            )
        if result.delta_candidates:
            maximum = self.service._known_for_case(case).max_event_numeric_id(
                case.ticker, case.version_pin.event_library_version
            )
            if maximum is None:
                raise W3Error(
                    "event_library_max_id_unavailable", "Published Event maximum is unavailable"
                )
            keys = []
            for index, candidate in enumerate(result.delta_candidates):
                self.service.repository.allocate_provisional(
                    case_id=case.case_id,
                    originating_node="W3",
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source_message_id=case.source.source_message_id,
                    candidate_index=index,
                    candidate=candidate,
                    published_max_event_numeric_id=maximum,
                )
                keys.append(
                    encode(
                        [
                            case.version_pin.event_library_root,
                            "atomic",
                            case.source.source_message_id,
                            index,
                        ]
                    )
                )
            self.registry.link(record, "EVENT", keys, case.case_id)
        unified = is_unified_contract(case.w3_contract_version)
        hit_ids = (
            (case.w2_final.policy_ids if case.w2_final.policy_hit else [])
            if unified
            else result.policy.policy_ids
        )
        policy_reason = case.w2_final.reason if unified else result.policy.reason
        if result.novelty.result.value == "NEW" and not hit_ids:
            self.service.repository.save_w3_coverage_gap(
                W3CoverageGapRecord(
                    case_id=case.case_id,
                    w3_case_id="w3:" + case.case_id,
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source=case.source,
                    policy_set_version=case.version_pin.policy_set_version,
                    result={
                        **result.model_dump(mode="json"),
                        "trade_decision": trade_stage["result_json"],
                        **(
                            dict(
                                w2_assessment=case.w2_final.model_dump(mode="json"),
                                result_contract_version=case.w3_contract_version,
                            )
                            if unified
                            else {}
                        ),
                    },
                )
            )
        if result.novelty.result.value == "OLD" and hit_ids:
            self.service.repository.save_badcase(
                BadcaseRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source=case.source,
                    matched_known_event_ids=result.novelty.reference_ids,
                    hit_policy_ids=hit_ids,
                    event_library_version=case.version_pin.event_library_version,
                    policy_set_version=case.version_pin.policy_set_version,
                    w1_reason=result.novelty.reason,
                    w2_reason=policy_reason,
                )
            )
        case.w3_operations_result = result.model_dump(mode="json")
        prepared_case = self._prepare_case_payload(case)
        archive = self._fallback_archive(case, result, trade_stage)
        archive_payload = (
            self.service.repository.content.encode(archive.model_dump(mode="json"))
            if archive and hasattr(self.service.repository, "content")
            else None
        )
        event_subjects = event_matches(
            case.model_dump(mode="json") | {"w3_operations_status": "COMMITTED"}
        )
        with self.journal.transaction() as db:
            assert_write_lease(db)
            if policy_consumption.get_value(db, "w3_operations_applied", case.case_id) is None:
                watch = apply_v4_actions(
                    db, case=case, result=result, frozen=state, now=self.journal.clock()
                )
                pins = apply_pin_actions(
                    db,
                    case=case,
                    actions=result.pin_actions,
                    frozen=state["pins"],
                    now=self.journal.clock(),
                )
                policy_consumption.put_value(
                    db,
                    "w3_operations_applied",
                    case.case_id,
                    dict(
                        ticker=case.ticker,
                        case_id=case.case_id,
                        watch=watch,
                        pins=pins,
                        validation_issues=policy_consumption.get_value(
                            db, "w3_action_parse_issues", case.case_id
                        )
                        or [],
                    ),
                )
            if archive is not None and hasattr(self.service.repository, "path"):
                self.service.repository.save_archive_in_transaction(db, archive, archive_payload)
            self.registry.commit_stage(db, case.case_id, OPERATIONS_PHASE)
            db.executemany(
                "INSERT OR IGNORE INTO runtime_v2_w3_thread_links VALUES(?,?,?,?,?)",
                [
                    (record["record_id"], case.ticker, "EVENT", key, case.case_id)
                    for key in event_subjects
                ],
            )
            case.w3_operations_status = "COMMITTED"
            case.w3_operations_error = None
            self._save_case_in_transaction(db, case, prepared_case)
        if archive is not None and not hasattr(self.service.repository, "path"):
            self.service.repository.save_archive(archive)
        case.w3_operations_status = "COMMITTED"
        case.w3_operations_error = None
        self.service.repository.save_case(case)
        self._sync_route_case(case, record)
        self.registry.release(case.case_id)

    def _fallback_archive(self, case, result, trade_stage):
        if not is_unified_contract(case.w3_contract_version):
            return None
        if (
            trade_stage["result_json"]["decision"] != "NO_TRADE"
            or result.novelty.result.value != "OLD"
            or case.w2_final.policy_hit
        ):
            return None
        with self.journal.transaction(write=False) as db:
            for namespace, key in (
                ("trade_intents", "trade:" + case.case_id),
                ("analysis_trade_decisions", "trade:" + case.case_id),
                ("candidates", "candidate:" + case.case_id),
            ):
                if policy_consumption.get_value(db, namespace, key) is not None:
                    return None
            if hasattr(self.service.repository, "path"):
                for table in (
                    "runtime_v2_archives",
                    "runtime_v2_badcases",
                    "runtime_v2_trade_records",
                    "runtime_v2_w3_coverage_gaps",
                ):
                    if db.execute(
                        "SELECT 1 FROM " + table + " WHERE case_id=?", (case.case_id,)
                    ).fetchone():
                        return None
                if db.execute(
                    "SELECT 1 FROM runtime_v2_candidates WHERE source_message_id=? LIMIT 1",
                    (case.source.source_message_id,),
                ).fetchone():
                    return None
        return ArchiveRecord(
            case_id=case.case_id,
            ticker=case.ticker,
            source_message_id=case.source.source_message_id,
            reason=trade_stage["result_json"]["reason"] + "\n" + result.novelty.reason,
            result_basis=ArchiveResultBasis(
                novelty=result.novelty.result,
                novelty_source="W3_OPERATIONS",
                policy_hit=case.w2_final.policy_hit,
                policy_related=case.w2_final.policy_related,
                w3_decision="NO_TRADE",
            ),
        )

    def _sync_route_case(self, case, record):
        from .schema import W3CaseStatus, W3ThreadKind

        route_case = self.service.repository.get_w3_case(case.case_id)
        if route_case:
            self.service.repository.save_w3_case(
                route_case.model_copy(
                    update=dict(
                        status=W3CaseStatus.RESOLVED,
                        thread_id=record["thread_id"],
                        thread_kind=W3ThreadKind.CASE,
                        trade_decision=case.w3_trade_decision,
                        operations_result=case.w3_operations_result,
                        operations_status=case.w3_operations_status,
                        resolved_route=case.resolved_route,
                    )
                )
            )

    def _restore_trade(self, case, stage, *, persist=True):
        case.w3_trade_decision = stage["result_json"]
        case.decision_completed_at = datetime.fromisoformat(stage["committed_at"])
        case.error_code = None
        case.error_message = None
        case.w3_operations_status = case.w3_operations_status or "PENDING"
        trading = stage["result_json"]["decision"] != "NO_TRADE"
        case.resolved_route = RuntimeRouteDecision(
            primary_route=RuntimePrimaryRoute.TRADE if trading else RuntimePrimaryRoute.ARCHIVE,
            side_effects=[],
            reason=f"w3_decision={stage['result_json']['decision']};output={stage['disposition']}",
        )
        if persist:
            self.service.repository.save_case(case)

    def _prepare_case_payload(self, case):
        if hasattr(self.service.repository, "content"):
            return json.loads(self.service.repository.content.encode(case.model_dump(mode="json")))
        return None

    def _save_case_in_transaction(self, db, case, prepared=None):
        if hasattr(self.service.repository, "path"):
            # Only small fields decided by the commit guard are patched under the lock.
            # Preserve the legacy recovery helper signature; hot paths pre-encode.
            payload = dict(prepared) if prepared is not None else self._prepare_case_payload(case)
            payload.update(
                case.model_dump(
                    mode="json",
                    include={
                        "status",
                        "technical_status",
                        "resolved_route",
                        "decision_completed_at",
                        "error_code",
                        "error_message",
                        "w3_operations_status",
                        "w3_operations_error",
                        "updated_at",
                    },
                )
            )
            db.execute(
                "UPDATE runtime_v2_cases SET payload_json=?,status=?,technical_status=?,"
                "updated_at=? WHERE case_id=?",
                (
                    encode(payload),
                    case.status.value,
                    case.technical_status.value,
                    self.journal.clock().isoformat(),
                    case.case_id,
                ),
            )

    def _enqueue_in_transaction(self, db, effect):
        if hasattr(self.service.repository, "path"):
            db.execute(
                "INSERT OR IGNORE INTO runtime_v2_effects(effect_id,case_id,effect_type,"
                "idempotency_key,status,attempt_count,available_at,payload_json,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    effect.effect_id,
                    effect.case_id,
                    effect.effect_type.value,
                    effect.idempotency_key,
                    effect.status.value,
                    0,
                    effect.available_at.isoformat(),
                    effect.model_dump_json(),
                    effect.updated_at.isoformat(),
                ),
            )
        else:
            self.service.repository.enqueue_effect(effect)

    def _ack(self, job_id):
        # An ACK failure is recoverable housekeeping, never a reason to repeat a valid decision.
        try:
            self.agent.acknowledge_job(job_id)
            # Exact keys only. Decode/compact outside the global writer, including NativeContent.
            with self.journal.transaction(write=False, expand=False) as db:
                invocation_row = db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace='worker_invocations' AND key=?",
                    (job_id,),
                ).fetchone()
            invocation = (
                json.loads(self.journal.content.text(invocation_row[0])) if invocation_row else None
            )
            receipt_key = invocation.get("receipt_key") if invocation else None
            if not receipt_key and invocation and invocation.get("case_id"):
                # Existing W3 case receipts have a deterministic identity; no history scan.
                case_id = invocation["case_id"]
                phase = invocation.get("task_phase")
                receipt_key = (
                    f"runtime-worker:{case_id}:{invocation['run_id']}:{invocation['node']}:"
                    f"{phase or 'case'}" + (f":{case_id}" if phase is not None else "")
                )
            keys = []
            if receipt_key:
                keys.append(("worker_receipts", receipt_key))
            with self.journal.transaction(write=False, expand=False) as db:
                rows = [
                    (ns, key, row[0])
                    for ns, key in keys
                    if (
                        row := db.execute(
                            "SELECT payload FROM runtime_values WHERE namespace=? AND key=?",
                            (ns, key),
                        ).fetchone()
                    )
                ]
            if invocation_row:
                rows.insert(0, ("worker_invocations", job_id, invocation_row[0]))
            updates = []
            receipt_matches = False
            for ns, key, raw in rows:
                small = (
                    invocation
                    if ns == "worker_invocations"
                    else json.loads(self.journal.content.text(raw))
                )
                job = small["job"] if ns == "worker_invocations" else small
                if job.get("job_id") != job_id:
                    continue  # A newer generation owns this receipt and request.
                job["final_response"] = None
                if job.get("telemetry"):
                    job["telemetry"]["events"] = []
                updates.append((self.journal.content.encode(small), ns, key, raw))
                receipt_matches |= ns == "worker_receipts"
            with self.journal.transaction(expand=False, operation="w3.ack.commit") as db:
                for payload, ns, key, raw in updates:
                    changed = db.execute(
                        "UPDATE runtime_values SET payload=? WHERE namespace=? AND key=? AND payload=?",
                        (payload, ns, key, raw),
                    ).rowcount
                    if not changed:
                        raise RuntimeError("Worker receipt changed during ACK; retry housekeeping")
                if receipt_matches:
                    db.execute(
                        "DELETE FROM runtime_values WHERE namespace='worker_requests' AND key=?",
                        (receipt_key,),
                    )
                db.execute(
                    "DELETE FROM runtime_values WHERE namespace='w3_ack_pending' AND key=?",
                    (job_id,),
                )
        except Exception as exc:
            self.journal.set("w3_ack_pending", job_id, str(exc)[:500])

    def _maintain_legacy(self, now):
        """Bound the input scan first; registered threads never read Worker receipts."""
        from datetime import timedelta

        with self.journal.transaction(write=False) as db:
            markers = {
                r[0]: json.loads(r[1])
                for r in db.execute(
                    "SELECT key,payload FROM runtime_values WHERE namespace='w3_housekeeping'"
                )
            }
            cursor = markers.get("legacy_case_cursor", "")
            receipt_cursor = markers.get("legacy_receipt_cursor", {})
            failed = markers.get("legacy_cycle_failed", False)
            batch = [
                dict(r)
                for r in db.execute(
                    "WITH batch AS MATERIALIZED (SELECT case_id,ticker,"
                    "json_extract(payload_json,'$.thread_id') AS thread_id "
                    "FROM runtime_v2_w3_cases WHERE case_id>? ORDER BY case_id LIMIT 64) "
                    "SELECT b.*,EXISTS(SELECT 1 FROM runtime_v2_w3_threads t "
                    "WHERE t.thread_id=b.thread_id) AS registered FROM batch b ORDER BY case_id",
                    (cursor,),
                )
            ]
        report = dict(scanned=len(batch), lookups=0, attempts=0, more=len(batch) == 64)
        seen = set()
        for case in batch:
            thread_id = case["thread_id"]
            if not thread_id or case["registered"] or thread_id in seen:
                cursor, receipt_cursor = case["case_id"], {}
                continue
            if report["attempts"] == 20:
                report["more"] = True
                break
            resume = receipt_cursor if receipt_cursor.get("case_id") == case["case_id"] else {}
            with self.journal.transaction(write=False) as db:
                preferred = [
                    r[0]
                    for r in db.execute(
                        "SELECT worker_job_id FROM runtime_v2_w3_stages "
                        "WHERE case_id=? AND worker_job_id IS NOT NULL",
                        (case["case_id"],),
                    )
                ]
                receipts = [
                    dict(r)
                    for r in db.execute(
                        "SELECT key,json_extract(payload,'$.job.job_id') AS job_id,"
                        "json_extract(payload,'$.run_id') AS workspace,"
                        "CASE WHEN key IN (SELECT value FROM json_each(?)) "
                        "THEN 0 ELSE 1 END AS priority "
                        "FROM runtime_values WHERE namespace='worker_invocations' "
                        "AND json_extract(payload,'$.node')='persistent_runtime_w3' "
                        "AND json_extract(payload,'$.job.thread_id')=? "
                        "AND (priority>? OR (priority=? AND key>?)) "
                        "ORDER BY priority,key LIMIT ?",
                        (
                            json.dumps(preferred),
                            thread_id,
                            resume.get("priority", -1),
                            resume.get("priority", -1),
                            resume.get("key", ""),
                            21 - report["attempts"],
                        ),
                    )
                ]
            report["lookups"] += 1
            registered = False
            consumed = 0
            for receipt in receipts:
                if report["attempts"] == 20:
                    break
                report["attempts"] += 1
                consumed += 1
                receipt_cursor = dict(
                    case_id=case["case_id"], priority=receipt["priority"], key=receipt["key"]
                )
                try:
                    self.agent.register_legacy_thread(receipt["job_id"])
                    with self.journal.transaction() as db:
                        db.execute(
                            "INSERT OR IGNORE INTO runtime_v2_w3_threads"
                            "(record_id,thread_id,ticker,kind,compatibility_key,workspace,status,"
                            "last_received_at,expires_at) "
                            "VALUES(?,?,?,'LEGACY_CASE','legacy',?,'ACTIVE',?,?)",
                            (
                                digest(["legacy", thread_id]),
                                thread_id,
                                case["ticker"],
                                receipt["workspace"],
                                now.isoformat(),
                                (now + timedelta(hours=48)).isoformat(),
                            ),
                        )
                        db.execute(
                            "DELETE FROM runtime_values WHERE "
                            "namespace='w3_legacy_registration_errors' AND key=?",
                            (thread_id,),
                        )
                    registered = True
                    seen.add(thread_id)
                    break
                except Exception as exc:
                    failed = True
                    self.journal.set("w3_legacy_registration_errors", thread_id, str(exc)[:500])
            if not registered and consumed < len(receipts):
                report["more"] = True
                break  # Retain this Case and its last receipt coordinate.
            if not registered:
                failed = True  # Missing proof is revisited, never fabricated.
            seen.add(thread_id)
            cursor, receipt_cursor = case["case_id"], {}
        with self.journal.transaction() as db:
            for key, value in {
                "legacy_case_cursor": cursor if report["more"] else "",
                "legacy_receipt_cursor": receipt_cursor if report["more"] else {},
                "legacy_cycle_failed": failed if report["more"] else False,
            }.items():
                policy_consumption.put_value(db, "w3_housekeeping", key, value)
        report["failed"] = failed
        return report

    def maintain(self):
        """Daily bounded housekeeping; SDK errors leave durable retryable records."""
        now = self.journal.clock()
        with self.journal.transaction(write=False) as db:
            markers = {
                r[0]: json.loads(r[1])
                for r in db.execute(
                    "SELECT key,payload FROM runtime_values WHERE namespace='w3_housekeeping'"
                )
            }
        marker = markers.get("next_at")
        if marker and marker > now.isoformat():
            return
        from datetime import timedelta

        started = time.monotonic()
        cpu_started = time.thread_time()
        failed = markers.get("cycle_failed", False)
        cursor = markers.get("cursor", "")
        try:
            legacy = (
                dict(scanned=0, lookups=0, attempts=0, more=False, failed=False)
                if markers.get("legacy_phase_done")
                else self._maintain_legacy(now)
            )
            failed |= legacy["failed"]
        except Exception as exc:
            failed = True
            legacy = dict(scanned=0, lookups=0, attempts=0, more=False)
            self.journal.set("w3_legacy_registration_errors", "scan", str(exc)[:500])
        with self.journal.transaction(write=False) as db:
            rows = (
                []
                if markers.get("thread_phase_done")
                else [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM runtime_v2_w3_threads WHERE kind IN ('CASE','LEGACY_CASE') "
                        "AND status IN ('ACTIVE','DELETING') "
                        "AND (expires_at<=? OR owner_case_id IS NOT NULL) "
                        "AND record_id>? ORDER BY record_id LIMIT 20",
                        (now.isoformat(), cursor),
                    )
                ]
            )
            acknowledgements = [
                r[0]
                for r in db.execute(
                    "SELECT key FROM runtime_values WHERE namespace='w3_ack_pending' LIMIT 20"
                )
            ]
        for job_id in acknowledgements:
            self._ack(job_id)
        for record in rows:
            try:
                if not record["thread_id"]:
                    continue
                state = self.agent.thread_status(record["thread_id"])
                if state["active"]:
                    continue
                owner = record["owner_case_id"]
                expired = record["expires_at"] <= now.isoformat()
                if owner:
                    effects = self.service.repository.list_effects(case_id=owner)
                    if not expired and any(
                        e.status.value in {"PENDING", "PENDING_RETRY", "RUNNING"} for e in effects
                    ):
                        continue
                    if expired:
                        # A pending retry must terminate on its original thread, never fork anew.
                        case = self.service.repository.get_case(owner)
                        if case and case.w3_operations_status != "COMMITTED":
                            case.w3_operations_status = "FAILED"
                            case.w3_operations_error = "w3_operations_expired"
                            self.service.repository.save_case(case)
                    self.registry.release(owner)
                if expired:
                    with self.journal.transaction() as db:
                        changed = db.execute(
                            "UPDATE runtime_v2_w3_threads SET status='DELETING' "
                            "WHERE record_id=? AND owner_case_id IS NULL AND expires_at<=?",
                            (record["record_id"], now.isoformat()),
                        ).rowcount
                    if not changed:
                        continue
                    self.agent.delete_thread(record["thread_id"])
                    with self.journal.transaction() as db:
                        db.execute(
                            "UPDATE runtime_v2_w3_threads SET status='DELETED',error=NULL "
                            "WHERE record_id=? AND owner_case_id IS NULL",
                            (record["record_id"],),
                        )
                        db.execute(
                            "DELETE FROM runtime_v2_w3_thread_links WHERE record_id=?",
                            (record["record_id"],),
                        )
            except Exception as exc:
                failed = True
                with self.journal.transaction() as db:
                    db.execute(
                        "UPDATE runtime_v2_w3_threads SET error=? WHERE record_id=?",
                        (str(exc)[:1000], record["record_id"]),
                    )
        thread_done = bool(markers.get("thread_phase_done")) or len(rows) < 20
        legacy_done = not legacy["more"]
        more = not thread_done or not legacy_done or len(acknowledgements) == 20
        delay = timedelta(seconds=5) if more else timedelta(hours=1 if failed else 24)
        with self.journal.transaction() as db:
            for key, value in {
                "cursor": rows[-1]["record_id"] if len(rows) == 20 else "",
                "legacy_phase_done": legacy_done if more else False,
                "thread_phase_done": thread_done if more else False,
                "cycle_failed": failed if more else False,
                "next_at": (now + delay).isoformat(),
            }.items():
                policy_consumption.put_value(db, "w3_housekeeping", key, value)
        logging.getLogger(__name__).info(
            "w3_housekeeping scanned=%s lookups=%s registration_attempts=%s more=%s "
            "failed=%s elapsed_ms=%.1f cpu_ms=%.1f",
            legacy["scanned"],
            legacy["lookups"],
            legacy["attempts"],
            more,
            failed,
            (time.monotonic() - started) * 1000,
            (time.thread_time() - cpu_started) * 1000,
        )

    def record_failure(self, case_id, phase, exc, *, retryable):
        case = self.service.repository.get_case(case_id)
        code = getattr(exc, "code", type(exc).__name__)
        step = getattr(exc, "w3_step", None) or "DISPATCH"
        message = f"{phase}/{step}: {str(exc) or type(exc).__name__}"[:1000]
        with self.journal.transaction() as db:
            db.execute(
                "UPDATE runtime_v2_w3_stages SET status=CASE "
                "WHEN ?=0 THEN 'FAILED' WHEN result_json IS NULL THEN 'PENDING' "
                "ELSE 'RESULT_SAVED' END,error=? WHERE case_id=? AND phase=?",
                (int(retryable), message, case_id, phase),
            )
        if case:
            case.updated_at = self.journal.clock()
        if case and phase == TRADE_PHASE:
            case.error_code, case.error_message = code, message
            case.frozen_inputs["w3_execution_state"] = dict(
                status="FAILED_RETRYABLE" if retryable else "FAILED",
                phase=phase,
                step=step,
                operation=(self.registry.stage(case_id, phase) or {}).get("input_refs"),
            )
            self.service.repository.save_case(case)
            self.service._project_terminal(case)
        if phase == OPERATIONS_PHASE and case:
            case.w3_operations_status = "PENDING_RETRY" if retryable else "FAILED"
            case.w3_operations_error = message
            self.service.repository.save_case(case)
            self.service._project_terminal(case)
            owned = self.registry.owned(case_id)
            if owned:
                self._sync_route_case(case, owned)
        if not retryable:
            if phase == TRADE_PHASE and hasattr(self.agent, "stop_market"):
                try:
                    self.agent.stop_market(case_id)
                except Exception as stop_error:
                    self.journal.gap(
                        "w3-market-stop",
                        case.ticker if case else "",
                        type(stop_error).__name__,
                        str(stop_error),
                    )
            owned = self.registry.owned(case_id)
            if owned:
                try:
                    active = bool(
                        owned["thread_id"]
                        and self.agent.thread_status(owned["thread_id"])["active"]
                    )
                except Exception:
                    active = True
                if not active:
                    self.registry.release(case_id)
