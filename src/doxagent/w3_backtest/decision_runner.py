"""Decision-only replay using one initialized mother and isolated Case forks."""

from __future__ import annotations

import json
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

from doxagent.codex_worker.schema import W3PrepareRequest
from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest
from doxagent.persistent_runtime_v2.schema import RuntimeCase
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner, W3ExecutionEnvironment
from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
from doxagent.persistent_runtime_v2.w3_v6 import ENTRY, scoped_manifest

from .context import (
    business_payload,
    coverage,
    execution_profile,
    read_context,
    validate_market,
    visible_input,
)
from .runner import _read, _write, local_worker, native_tool_audit, workspace_path


def agent_payload(raw, context=None):
    context = context or {}
    case = runtime_case(raw, Path("preview"), {}, execution_profile(raw, context))
    return business_payload(raw, case, context)


def runtime_case(raw, root, assets, profile):
    at = datetime.fromisoformat(raw["as_of"])
    if at.tzinfo is None:
        raise ValueError("as_of requires an explicit timezone")
    key = raw.get("case_key", "preview")
    message = raw["message"]
    return RuntimeCase.model_validate(
        dict(
            case_id="replay_" + digest([str(root), key])[:32],
            trading_date=at.date().isoformat(),
            source=dict(
                source_message_id=key,
                source_id="frozen-replay",
                binding_id=key,
                stream_item_id=key,
                member_count=1,
                url=message.get("url") or "replay://" + key,
                published_at=message.get("published_at") or raw["as_of"],
                publication_time_basis=message.get("publication_time_basis") or "EXACT",
                collected_at=raw["as_of"],
                message_bus_event_time=raw["as_of"],
                snapshot=dict(
                    ticker=raw["ticker"],
                    title=message["title"],
                    body=message.get("body"),
                    source_name=message.get("source_name"),
                ),
            ),
            version_pin=dict(
                event_library_version=1,
                provisional_snapshot_version=0,
                policy_set_version=1,
                runtime_projection_version=1,
            ),
            w3_contract_version="persistent-runtime.w3.v6",
            orchestration_revision=3,
            frozen_inputs=dict(
                w3_assets=assets,
                w3_contracts=dict(w3_entry_version=ENTRY),
                w3_execution_profile=profile,
                w3_replay=dict(cutoff_at=raw["as_of"], partial_input=True),
            ),
        )
    )


class DecisionReplayRunner:
    def __init__(
        self,
        dataset,
        run_root,
        *,
        prompt_root,
        model=None,
        effort=None,
        trade_mode="single",
        timeout_seconds=1800,
        worker_factory=local_worker,
        time_awareness_enabled=False,
        service_tier=None,
    ):
        self.dataset, self.root = Path(dataset).resolve(), Path(run_root).resolve()
        self.time_awareness_enabled = time_awareness_enabled
        self.model, self.effort = model, effort
        if trade_mode not in {'single','dual_consensus'}:
            raise ValueError('invalid Trade mode')
        self.trade_mode=trade_mode
        self.service_tier = service_tier
        prompts = Path(prompt_root)
        prompt_files = ["AGENTS.md", "backtest.md", "contract.json"]
        if (prompts / "skills/skill.md").is_file():
            prompt_files.append("skills/skill.md")
        self.assets = {
            "w3/" + name: (prompts / name).read_text(encoding="utf-8-sig")
            for name in prompt_files
        }
        contract = json.loads(self.assets["w3/contract.json"])["w3_contract_version"]
        if contract != "persistent-runtime.w3.v6":
            raise ValueError("decision-only replay supports only the v6 decision contract")
        self.root.mkdir(parents=True, exist_ok=True)
        self.journal = RuntimeJournal(self.root / "runtime.sqlite3")
        self.registry = W3ThreadRegistry(self.journal)
        self.worker, workspace = (
            local_worker(
                self.root / "worker",
                time_awareness_enabled=time_awareness_enabled,
                service_tier=service_tier,
            )
            if worker_factory is local_worker
            else worker_factory(self.root / "worker")
        )
        self.agent = CodexW3AgentRunner(
            worker=self.worker,
            workspace=workspace,
            context_provider=None,
            prompt_root=prompts,
            journal=self.journal,
            environment=W3ExecutionEnvironment.offline(),
            timeout_seconds=timeout_seconds,
            workspace_namespace="w3-backtest-decision",
            time_awareness_enabled=time_awareness_enabled,
        )
        self.templates = {}
        self._mother_lock = threading.Lock()

    def initialize(self, raw, profile):
        with self._mother_lock:
            identity = digest([self.assets, profile, "w3-decision-only-v6"])
            if identity in self.templates:
                return self.templates[identity]
            case = runtime_case(raw, self.root, self.assets, profile)
            template, acquired = self.registry.template("REPLAY", identity, identity)
            if acquired:
                _, job = self.agent.run_phase(
                    case=case,
                    record=template,
                    phase="w3_template",
                    payload=dict(template_format=ENTRY),
                )
                self.agent.register_template(job.job_id)
                self.registry.ready_template(
                    template["record_id"], job.thread_id, template["lease_token"]
                )
                self.agent.acknowledge_job(job.job_id)
                template["thread_id"] = job.thread_id
            if not template.get("thread_id"):
                raise RuntimeError("replay mother initialization is already in progress")
            self.templates[identity] = template
            return template

    def _pair(self, case, record, payload):
        from doxagent.persistent_runtime_v2.w3_consensus import aggregate
        from doxagent.persistent_runtime_v2.w3_contracts import W3DirectionDecision
        from doxagent.codex_worker.schema import WorkerJob
        from doxagent.persistent_runtime_v2.journal import encode

        self.registry.begin_stage(
            case.case_id, "trade_decision:A", input_refs=dict(record_id=record["record_id"])
        )
        self.registry.begin_stage(case.case_id, "trade_decision:B")
        records = {}
        for label in ("A", "B"):
            key = "trade_decision:" + label
            if self.registry.stage(case.case_id, key)["result_json"] is None:
                child = (
                    record
                    if label == "A"
                    else self.registry.create_case(
                        ticker=case.ticker,
                        case_id=case.case_id,
                        compatibility=record["compatibility_key"],
                        parent_id=record["parent_id"],
                        stage_key=key,
                    )
                )
                child["stage_key"] = key
                records[label] = child

        def save(label, result, job):
            key = "trade_decision:" + label
            self.registry.save_result(
                case.case_id, key, result, thread_id=job.thread_id, worker_job_id=job.job_id
            )
            refs = self.registry.stage(case.case_id, key)["input_refs"]
            refs.update(
                raw_output=job.final_response,
                duration_ms=(job.finished_at - job.started_at).total_seconds() * 1000
                if job.started_at and job.finished_at
                else None,
            )
            with self.journal.transaction() as db:
                db.execute(
                    "UPDATE runtime_v2_w3_stages SET input_refs=? WHERE case_id=? AND phase=?",
                    (encode(refs), case.case_id, key),
                )

        if records:
            self.agent.run_trade_pair(
                case=case, records=records, payload=payload, context=None, research=None, save=save
            )
        stages = {k: self.registry.stage(case.case_id, "trade_decision:" + k) for k in ("A", "B")}
        result, summary = aggregate(
            {
                k: dict(result=v["result_json"], duration_ms=v["input_refs"].get("duration_ms"))
                for k, v in stages.items()
            }
        )
        chosen = stages[summary["selected_branch"]]
        selected = self.registry.bound(case.case_id, "trade_decision:" + summary["selected_branch"])
        summary["record_id"] = selected["record_id"]
        with self.journal.transaction() as db:
            db.execute(
                "UPDATE runtime_v2_w3_stages SET input_refs=? WHERE case_id=? AND phase=?",
                (encode(summary), case.case_id, "trade_decision"),
            )
        for stage in stages.values():
            self.agent.acknowledge_job(stage["worker_job_id"])
        return (
            W3DirectionDecision.model_validate(result),
            WorkerJob(
                job_id=chosen["worker_job_id"],
                run_id=selected["workspace"],
                attempt_id="trade_decision",
                status="succeeded",
                thread_id=chosen["thread_id"],
            ),
            selected,
        )

    def run_case(self, dataset, case_key, run_root, **_):
        raw = _read(self.dataset / "inputs" / case_key / "case.json")
        folder = self.dataset / "inputs" / case_key / "evidence"
        extra = read_context(self.dataset, case_key)
        research = self.dataset / "research" / raw.get("research_key", "absent")
        extra.setdefault(
            "research_cutoffs",
            {
                name: None
                for name, path in (
                    ("D1", research / "document1.md"),
                    ("D2", research / "document2.json"),
                )
                if path.exists()
            },
        )
        profile = execution_profile(raw, extra, self.model, self.effort)
        market_path = folder / "market_view.json"
        market = _read(market_path) if market_path.exists() else None
        validate_market(market, raw)
        evidence = {
            p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(folder.rglob("*"))
            if p.is_file()
            and p.suffix in {".json", ".jsonl", ".md"}
            and p.name != "market_view.json"
        }
        template = self.initialize(raw, profile)
        case = runtime_case(raw, self.root, self.assets, profile)
        payload = business_payload(raw, case, extra)
        projected = visible_input(raw, case, extra)
        started = datetime.now(UTC)
        result = None
        error = None
        job = None
        record = None
        timing = None
        research_files = {}
        out = self.root / "cases" / case_key
        out.mkdir(parents=True, exist_ok=True)
        try:
            if self.time_awareness_enabled:
                timing = self.agent.begin_trade_timing(case.case_id)
            research = self.dataset / "research" / raw.get("research_key", "absent")
            research_files = {
                name: p.read_text(encoding="utf-8-sig")
                for name, p in (
                    ("d1", research / "document1.md"),
                    ("d2", research / "document2.json"),
                )
                if p.is_file()
            }
            components = self.agent._wait_v4(
                self.worker.prepare_w3_research(
                    case.ticker,
                    research_files,
                )
            )
            binding = self.agent._wait_v4(
                self.worker.bind_w3_context(
                    case.case_id,
                    "trade_decision",
                    scoped_manifest(
                        dict(
                            case_id=case.case_id,
                            phase="trade_decision",
                            ticker=case.ticker,
                            mode="OFFLINE_REPLAY",
                            cutoff_at=raw["as_of"],
                            w3_contract_version=case.w3_contract_version,
                            components=components,
                            evidence=evidence,
                            market_view=market
                            or dict(
                                status="UNAVAILABLE",
                                ticker=case.ticker,
                                observed_at=raw["as_of"],
                                series={},
                            ),
                        )
                    ),
                )
            )
            self.journal.set("w3_context_v2", case.case_id + ":trade_decision", binding)
            case.frozen_inputs["w3_replay"].update(
                evidence_files=evidence, cutoff_at=raw["as_of"], partial_input=True
            )
            self.registry.begin_stage(case.case_id, "trade_decision")
            record = self.registry.create_case(
                ticker=case.ticker,
                case_id=case.case_id,
                compatibility=template["compatibility_key"],
                parent_id=template["thread_id"],
            )
            child = self.agent.prepare_thread(
                W3PrepareRequest(
                    operation_key="w3-fork:" + record["record_id"],
                    record_id=record["record_id"],
                    source_case_id=case.case_id,
                    parent_thread_id=record["parent_id"],
                    run_id=record["workspace"],
                    attempt_id="prepared",
                    research_pack_id="",
                    reference_pack_id="",
                    case_overlay_sha=digest(self.assets),
                    cutoff_at=datetime.fromisoformat(raw["as_of"]),
                    capability_contract_version=ENTRY,
                    profile={
                        **profile,
                        "ticker": case.ticker,
                        "data_mcp_enabled": "false",
                        "research_assets_mcp_enabled": "true",
                        "w3_entry_version": ENTRY,
                        "web_search_mode": "disabled",
                    },
                )
            )
            record["thread_id"] = child["thread_id"]
            self.registry.set_thread(record["record_id"], record["thread_id"])
            self.registry.begin_stage(case.case_id, "trade_decision", thread_id=record["thread_id"])
            if self.trade_mode == 'dual_consensus':
                result, job, record = self._pair(case,record,payload)
            else:
                result, job = self.agent.run_phase(
                    case=case, record=record, phase="trade_decision", payload=payload)

            self.registry.save_result(
                case.case_id,
                "trade_decision",
                result,
                thread_id=job.thread_id,
                worker_job_id=job.job_id if (self.registry.stage(case.case_id,'trade_decision').get('input_refs') or {}).get('result_source') != 'CONSENSUS_ROUTER' else None,
            )
            with self.journal.transaction() as db:
                self.registry.commit_stage(
                    db, case.case_id, "trade_decision", disposition="RECORDED_ONLY"
                )
            if self.trade_mode == 'single':
                self.agent.acknowledge_job(job.job_id)
        except Exception as exc:
            error = dict(type=type(exc).__name__, code=getattr(exc, "code", None), message=str(exc))
            (out / "error_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            self.registry.release(case.case_id)
        audits = []
        if record:
            for path in workspace_path(self.root / "worker/workspaces" / record["workspace"]).rglob(
                "turn_summary.json"
            ):
                audit = _read(path)
                if audit.get("thread_id") != record.get("thread_id"):
                    continue
                selection = path.with_name("model_selection.json")
                audit["model_selection"] = _read(selection) if selection.exists() else None
                audits.append(audit)
        decision = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        receipt = dict(
            case_key=case_key,
            case_id=raw.get('case_id', raw.get('original_case_id')),
            ticker=case.ticker,
            original_case_id=raw.get("original_case_id"),
            replay_case_id=case.case_id,
            status="FAILED" if error else "SUCCEEDED",
            error=error,
            started_at=started.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            cutoff_at=raw["as_of"],
            business_input_fingerprint=digest([raw, extra, evidence, market, research_files]),
            input_fingerprint=digest(
                [raw, extra, evidence, market, research_files, profile, self.service_tier, self.trade_mode]
            ),
            input_coverage=coverage(raw, extra, research_files, evidence, market),
            visible_input_sha=digest(projected),
            source_title=raw["message"]["title"],
            evaluation_scope="w3_decision_only",
            consensus=(self.registry.stage(case.case_id,'trade_decision') or {}).get('input_refs'),
            branches={k:self.registry.stage(case.case_id,'trade_decision:'+k) for k in ('A','B')} if self.trade_mode=='dual_consensus' else {},
            model={
                **profile,
                "service_tier": self.service_tier
                or ("fast" if profile["model"] == "gpt-6-luna" else "default"),
            },
            decision=decision,
            model_selection_basis=(
                "explicit_override"
                if self.model
                else "W2"
                if "policy_hit" in extra.get("w2_assessment", raw.get("w2", {}))
                else "missing_W2_fallback"
            ),
            operations=None,
            operations_status="NOT_REQUESTED",
            host_receipt=None,
            phase_audits=audits,
            mother_thread_id=template["thread_id"],
            thread_id=record.get("thread_id") if record else None,
            stages=dict(trade_decision=self.registry.stage(case.case_id, "trade_decision")),
        )
        if self.time_awareness_enabled:
            receipt["time_awareness"] = timing.model_dump(mode="json") if timing else None
        _write(out / "visible_input.json", projected)
        _write(out / "input_coverage.json", receipt["input_coverage"])
        _write(out / "decision.json", decision)
        _write(out / "result.json", receipt)
        return receipt

    def close(self):
        self.agent.close()
        native_tool_audit(self.root / "worker", self.root / "native_tool_trace.jsonl")
