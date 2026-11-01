"""Decision-only replay using one initialized mother and isolated Case forks."""

from __future__ import annotations

import json
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from doxagent.codex_worker.schema import W3PrepareRequest
from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest
from doxagent.persistent_runtime_v2.schema import RuntimeCase
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner, W3ExecutionEnvironment
from doxagent.persistent_runtime_v2.w3_research_store import ENTRY
from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry

from .runner import _read, _write, local_worker, native_tool_audit, workspace_path


def agent_payload(raw):
    at = datetime.fromisoformat(raw["as_of"])
    if at.tzinfo is None:
        raise ValueError("as_of requires an explicit timezone")
    message = dict(raw["message"])
    if message.get("publication_time_basis") == "DATE":
        message["published_at"] = str(message["published_at"])[:10]
    return dict(
        ticker=raw["ticker"],
        as_of=raw["as_of"],
        cutoff_at=raw["as_of"],
        time_utc=at.astimezone(UTC).strftime("%Y-%m-%d %H:%M"),
        time_new_york=at.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M"),
        message=message,
        w1=raw.get("w1", {}),
        w2=raw.get("w2", {}),
    )


def runtime_case(raw, root, assets, profile):
    at = datetime.fromisoformat(raw["as_of"])
    key = raw["case_key"]
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
                published_at=raw["as_of"],
                collected_at=raw["as_of"],
                message_bus_event_time=raw["as_of"],
                snapshot=dict(
                    ticker=raw["ticker"], title=message["title"], body=message.get("body")
                ),
            ),
            version_pin=dict(
                event_library_version=1,
                provisional_snapshot_version=0,
                policy_set_version=1,
                runtime_projection_version=1,
            ),
            w3_contract_version="persistent-runtime.w3.v5",
            orchestration_revision=3,
            frozen_inputs=dict(
                w3_assets=assets,
                w3_contracts=dict(w3_entry_version=ENTRY),
                w3_execution_profile=profile,
                w3_replay=dict(cutoff_at=raw["as_of"]),
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
        timeout_seconds=1800,
        worker_factory=local_worker,
    ):
        self.dataset, self.root = Path(dataset).resolve(), Path(run_root).resolve()
        self.profile = dict(
            model=model or "gpt-6-luna",
            effort=effort or ("medium" if model else "max"),
            provider=None,
        )
        prompts = Path(prompt_root)
        self.assets = {
            "w3/" + name: (prompts / name).read_text(encoding="utf-8-sig")
            for name in ("AGENTS.md", "skills/skill.md", "backtest.md", "contract.json")
        }
        contract = json.loads(self.assets["w3/contract.json"])["w3_contract_version"]
        if contract != "persistent-runtime.w3.v5":
            raise ValueError("decision-only replay requires the current v5 decision contract")
        self.root.mkdir(parents=True, exist_ok=True)
        self.journal = RuntimeJournal(self.root / "runtime.sqlite3")
        self.registry = W3ThreadRegistry(self.journal)
        self.worker, workspace = worker_factory(self.root / "worker")
        self.agent = CodexW3AgentRunner(
            worker=self.worker,
            workspace=workspace,
            context_provider=None,
            prompt_root=prompts,
            journal=self.journal,
            environment=W3ExecutionEnvironment.offline(),
            timeout_seconds=timeout_seconds,
            workspace_namespace="w3-backtest-decision",
        )
        self.template = None
        self._mother_lock = threading.Lock()

    def initialize(self, raw):
        with self._mother_lock:
            if self.template:
                return
            case = runtime_case(raw, self.root, self.assets, self.profile)
            identity = digest([self.assets, self.profile, "w3-decision-only-v2"])
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
            self.template = template

    def run_case(self, dataset, case_key, run_root, **_):
        raw = _read(self.dataset / "inputs" / case_key / "case.json")
        folder = self.dataset / "inputs" / case_key / "evidence"
        # Missing construction data is an engineering error, never a NO_TRADE.
        payload = agent_payload(raw)
        market = _read(folder / "market_view.json")
        for name in ("daily", "hourly", "fine"):
            series = market.get("series", {}).get(name, {})
            if not series.get("rows") and series.get("session_state") != "MARKET_CLOSED":
                raise ValueError("DATA_ERROR: missing real " + name + " market series")
        if market.get("observed_at") != raw["as_of"]:
            raise ValueError("DATA_ERROR: price snapshot and Case clock differ")
        evidence = {
            p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(folder.rglob("*"))
            if p.is_file()
            and p.suffix in {".json", ".jsonl", ".md"}
            and p.name != "market_view.json"
        }
        self.initialize(raw)
        case = runtime_case(raw, self.root, self.assets, self.profile)
        started = datetime.now(UTC)
        result = None
        error = None
        job = None
        record = None
        out = self.root / "cases" / case_key
        out.mkdir(parents=True, exist_ok=True)
        try:
            research = self.dataset / "research" / raw["research_key"]
            components = self.agent._wait_v4(
                self.worker.prepare_w3_research(
                    case.ticker,
                    dict(
                        d1=(research / "document1.md").read_text(encoding="utf-8-sig"),
                        d2=(research / "document2.json").read_text(encoding="utf-8-sig"),
                    ),
                )
            )
            binding = self.agent._wait_v4(
                self.worker.bind_w3_context(
                    case.case_id,
                    "trade_decision",
                    dict(
                        case_id=case.case_id,
                        phase="trade_decision",
                        ticker=case.ticker,
                        mode="OFFLINE_REPLAY",
                        cutoff_at=raw["as_of"],
                        w3_contract_version=case.w3_contract_version,
                        components=components,
                        evidence=evidence,
                        market_view=market,
                    ),
                )
            )
            self.journal.set("w3_context_v2", case.case_id + ":trade_decision", binding)
            case.frozen_inputs["w3_replay"] = dict(evidence_files=evidence, cutoff_at=raw["as_of"])
            record = self.registry.create_case(
                ticker=case.ticker,
                case_id=case.case_id,
                compatibility=self.template["compatibility_key"],
                parent_id=self.template["thread_id"],
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
                        **self.profile,
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
            result, job = self.agent.run_phase(
                case=case, record=record, phase="trade_decision", payload=payload
            )
            self.registry.save_result(
                case.case_id,
                "trade_decision",
                result,
                thread_id=job.thread_id,
                worker_job_id=job.job_id,
            )
            with self.journal.transaction() as db:
                self.registry.commit_stage(
                    db, case.case_id, "trade_decision", disposition="RECORDED_ONLY"
                )
            self.agent.acknowledge_job(job.job_id)
        except Exception as exc:
            error = dict(type=type(exc).__name__, code=getattr(exc, "code", None), message=str(exc))
            (out / "error_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            self.registry.release(case.case_id)
        audits = []
        if record:
            for path in workspace_path(self.root / "worker/workspaces" / record["workspace"]).glob(
                "attempts/*/audit/turn_summary.json"
            ):
                audit = _read(path)
                selection = path.with_name("model_selection.json")
                audit["model_selection"] = _read(selection) if selection.exists() else None
                audits.append(audit)
        decision = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        receipt = dict(
            case_key=case_key,
            ticker=case.ticker,
            original_case_id=raw.get("original_case_id"),
            replay_case_id=case.case_id,
            status="FAILED" if error else "SUCCEEDED",
            error=error,
            started_at=started.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            cutoff_at=raw["as_of"],
            input_fingerprint=digest([raw, evidence, market]),
            source_title=raw["message"]["title"],
            evaluation_scope="w3_decision_only",
            model=self.profile,
            decision=decision,
            operations=None,
            operations_status="NOT_REQUESTED",
            host_receipt=None,
            phase_audits=audits,
            mother_thread_id=self.template["thread_id"],
            thread_id=record.get("thread_id") if record else None,
            stages=dict(trade_decision=self.registry.stage(case.case_id, "trade_decision")),
        )
        _write(out / "decision.json", decision)
        _write(out / "result.json", receipt)
        return receipt

    def close(self):
        self.agent.close()
        native_tool_audit(self.root / "worker", self.root / "native_tool_trace.jsonl")
