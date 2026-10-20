"""Isolated local execution of the production W3 Template/Fork/two-stage chain."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import traceback
from datetime import UTC, datetime
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest, encode
from doxagent.persistent_runtime_v2.policy_watch import freeze_followup
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.router import route_runtime_case
from doxagent.persistent_runtime_v2.schema import RuntimeCase, W3ContextVersionPin
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.w3 import (
    CodexW3AgentRunner,
    W3ExecutionEnvironment,
    W3PreparedContext,
)
from doxagent.persistent_runtime_v2.w3_contracts import asset_contract_version, contract_models
from doxagent.persistent_runtime_v2.w3_execution import W3Execution
from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile
from doxagent.semantic_clock import semantic_day
from doxagent.workflows.codex_document3.consumer_contracts import parse_document2, parse_policy_set

from .legacy import LegacyReplayAdapter


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def workspace_path(path):
    """W3's immutable research paths exceed legacy Windows MAX_PATH."""
    resolved = str(Path(path).resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        resolved = "\\\\?\\" + resolved
    return Path(resolved)


def private_home(worker_root):
    state_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state")
    return state_root / "DoxAgent" / "w3_backtest" / digest(str(Path(worker_root).resolve()))[:24]


def native_tool_audit(worker_root, output):
    """Keep real tool receipts; code-mode commands are absent from SDK counters."""
    rows = []
    seen = set()
    for path in sorted(private_home(worker_root).glob("sessions/**/*.jsonl")):
        turn_id = None
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            payload = row.get("payload", {})
            if row.get("type") == "turn_context":
                turn_id = payload.get("turn_id")
            if payload.get("type") in {
                "function_call",
                "function_call_output",
                "custom_tool_call",
                "custom_tool_call_output",
            }:
                identity = (payload["type"], payload.get("call_id") or row.get("timestamp"))
                if identity not in seen:
                    seen.add(identity)
                    rows.append(dict(turn_id=turn_id, timestamp=row.get("timestamp"), **payload))
    rows.sort(key=lambda row: row.get("timestamp") or "")
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return dict(
        trace_path=output.name,
        tool_calls=sum(r["type"] in {"function_call", "custom_tool_call"} for r in rows),
        policy_blocks=sum(
            any(
                marker in str(r.get("output", ""))
                for marker in ("blocked by policy", "code-mode host is disabled")
            )
            for r in rows
            if r["type"].endswith("output")
        ),
    )


class FrozenContextProvider:
    def __init__(self, context):
        self.context = context

    async def load(self, case):
        return self.context.model_copy(deep=True)


class _NoUpstreamCalls:
    def close(self):
        """No transport is constructed in replay; service shutdown is a no-op."""

    def __getattr__(self, name):
        raise RuntimeError(f"Replay must not invoke upstream provider: {name}")


class _FrozenEvents:
    def __init__(self, reference):
        self.maximum = max([int(x) for x in re.findall(r"\bE([1-9][0-9]*)\b", reference)] or [0])

    def max_event_numeric_id(self, *_):
        return self.maximum


class _LocalWorker:
    """Close manager and SDK on the same event loop used for its jobs."""

    def __init__(self, manager):
        from doxagent.codex_worker.local_client import EmbeddedCodexWorkerClient

        self.client = EmbeddedCodexWorkerClient(manager)
        self.manager = manager
        self.research = None
        self.server = self.serving = self.mcp_context = None
        self.ready_configs = {}

    async def _ensure_research(self):
        if self.research is not None:
            return
        import asyncio
        import socket

        import uvicorn

        from doxagent.codex_worker.w3_market import W3MarketPreparer
        from doxagent.mcp.w3_research_server import ResearchMcp
        from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore
        from doxagent.settings import DoxAgentSettings

        self.research = W3ResearchStore(self.manager.store.root)
        await asyncio.to_thread(self.research.initialize)
        settings = DoxAgentSettings(_env_file=None, ibkr_tws_enabled=False)
        market = W3MarketPreparer(self.research, settings)
        mcp = ResearchMcp(self.research, "w3-local-replay-only-no-mcp-capabilities", market)
        self.mcp_context = mcp.manager.run()
        await self.mcp_context.__aenter__()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(mcp, host="127.0.0.1", port=port, lifespan="off", log_level="error")
        )
        self.serving = asyncio.create_task(self.server.serve(sockets=[sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        self.manager._runtime.runtime._settings.w3_research_mcp_url = f"http://127.0.0.1:{port}/"

    async def prepare_w3_research(self, ticker, components):
        import asyncio

        await self._ensure_research()
        return await asyncio.to_thread(self.research.install, components, ticker)

    async def bind_w3_context(self, case_id, phase, manifest):
        import asyncio

        await self._ensure_research()
        return await asyncio.to_thread(self.research.bind, case_id, phase, manifest)

    async def start_w3_market(self, manifest):
        if manifest.get("mode") != "OFFLINE_REPLAY":
            raise ValueError("local replay cannot start live market preparation")

    async def stop_w3_market(self, case_id):
        pass

    async def configure_w3_ready(self, config):
        self.ready_configs[config["parent_thread_id"]] = config
        return {"ready": True, "mode": "OFFLINE_REPLAY_SERIAL"}

    async def claim_w3_ready(self, request):
        from doxagent.codex_worker.schema import W3PrepareRequest
        from doxagent.persistent_runtime_v2.w3_research_store import ENTRY

        config = self.ready_configs[request["parent_thread_id"]]
        run_id = "w3-ready-replay-" + request["record_id"]
        self.manager._workspaces.materialize_case(run_id, {}, config["instructions"])
        value = await self.client.prepare_thread(
            W3PrepareRequest(
                operation_key="w3-fork:" + request["record_id"],
                record_id=request["record_id"],
                source_case_id=request["case_id"],
                parent_thread_id=request["parent_thread_id"],
                run_id=run_id,
                attempt_id="prepared",
                research_pack_id="",
                reference_pack_id="",
                case_overlay_sha=digest(config["instructions"]),
                cutoff_at=datetime.now(UTC),
                capability_contract_version=ENTRY,
                profile={
                    **request["profile"],
                    "ticker": "REPLAY",
                    "data_mcp_enabled": "false",
                    "research_assets_mcp_enabled": "true",
                    "w3_entry_version": ENTRY,
                    "web_search_mode": "disabled",
                },
            )
        )
        return {**value, "run_id": run_id}

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def aclose(self):
        try:
            await self.manager.close()
        finally:
            if self.server:
                self.server.should_exit = True
                await self.serving
                await self.mcp_context.__aexit__(None, None, None)


class _ReplayRuntime:
    """Bridge the SDK's synchronous identity hook to the manager's async hook.

    Production uses a capsule protocol for this bridge; local replay embeds the
    SDK directly. Each replay owns its runtime and runs its phases serially.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.receipt_callback = None

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    async def prepare_fork(self, request, cwd, parent_thread_id):
        return await self.runtime.prepare_fork(request, cwd, parent_thread_id)

    async def start(self, request, cwd):
        identities = []
        self.runtime.receipt_callback = lambda thread, turn: identities.append((thread, turn))
        try:
            return await self.runtime.start(request, cwd)
        finally:
            if self.receipt_callback is not None:
                for thread, turn in identities:
                    await self.receipt_callback(request, thread, turn)


def local_worker(worker_root: Path):
    """Only authentication is copied; production config, MCP and env files are excluded."""
    from openai_codex import CodexConfig, Sandbox

    from doxagent.codex_worker.jobs import WorkerJobManager
    from doxagent.codex_worker.local_client import LocalWorkspaceClient
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from doxagent.codex_worker.thread_lifecycle import AppServerThreads
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from doxagent.settings import DoxAgentSettings

    home = private_home(worker_root)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text("[features]\nmulti_agent=false\n", encoding="utf-8")
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    auth = source_home / "auth.json"
    if auth.is_file() and not (home / "auth.json").is_file():
        shutil.copyfile(auth, home / "auth.json")
    # CodexConfig.env overlays (rather than replaces) the parent's environment.
    # Blank inherited app bridges and provider credentials explicitly so the
    # child cannot acquire desktop tools or production service capabilities.
    env = {k: "" for k in os.environ if k.startswith(("DOXAGENT_", "IBKR_", "TWS_", "CODEX_"))}
    env.update(
        CODEX_HOME=str(home.resolve()),
        IBKR_TWS_ENABLED="false",
        CODEX_INTERNAL_ORIGINATOR_OVERRIDE="doxagent-w3-backtest",
    )
    config = CodexConfig(
        env=env,
        config_overrides=(
            'web_search="disabled"',
            "features.multi_agent=false",
            "features.apps=false",
            "features.plugins=false",
            "features.memories=false",
            "features.skip_host_skill_discovery=true",
            "features.browser_use=false",
            "features.computer_use=false",
            "features.image_generation=false",
            'sandbox_mode="read-only"',
        ),
        client_name="doxagent-w3-backtest",
    )
    settings = DoxAgentSettings(_env_file=None, ibkr_tws_enabled=False)
    runtime = OpenAICodexRuntime(
        config=config,
        settings=settings,
        capability_secret="w3-local-replay-only-no-mcp-capabilities",
        container_isolated=False,
        preserve_requested_model=True,
        # Windows' read-only process policy rejects even Get-Content. The
        # isolated local process needs executable file tools; tool capabilities,
        # credentials and case-local writes remain constrained separately.
        sandbox_override=Sandbox.full_access if os.name == "nt" else None,
    )
    store = LocalWorkspaceStore(workspace_path(worker_root / "workspaces"))
    manager = WorkerJobManager(_ReplayRuntime(runtime), store, launch_interval_seconds=0.05)
    manager.thread_lifecycle.transport = AppServerThreads(config)
    return _LocalWorker(manager), LocalWorkspaceClient(store)


class ReplayRunner:
    def __init__(self, *, prompt_root=None, worker_factory=local_worker, timeout_seconds=1800):
        self.prompt_root = Path(prompt_root or "prompts/persistent_runtime_v2/w3").resolve()
        self.worker_factory = worker_factory
        self.timeout_seconds = timeout_seconds

    def run_case(self, dataset: Path, case_key: str, run_root: Path, **_):
        marker = self.prompt_root / "contract.json"
        replay_contract = asset_contract_version({"w3/contract.json": marker.read_text(encoding="utf-8")} if marker.exists() else {})
        dataset, run_root = Path(dataset).resolve(), Path(run_root).resolve()
        case_dir = dataset / "inputs" / case_key
        raw = _read(case_dir / "case.json")
        historical = _read(case_dir / "historical_state.json")
        research_dir = dataset / "research" / raw["research_key"]
        original = raw.get("original_case") or raw.get("prepared_case")
        if not original:
            raise ValueError("case.json must include original_case without its final outcome")
        original = dict(original)
        cutoff = datetime.fromisoformat(raw.get("evidence_cutoff_at", raw.get("t0")))
        cutoff = cutoff.astimezone(UTC)
        original_id = raw.get("original_case_id", original["case_id"])
        original.update(
            case_id="replay_" + digest([str(run_root), case_key])[:32],
            trading_date=semantic_day(cutoff).isoformat(),
            created_at=cutoff.isoformat(),
            updated_at=cutoff.isoformat(),
            status="PENDING_W3",
            technical_status="OK",
            w3_contract_version=replay_contract,
            orchestration_revision=3,
            trade_expired=False,
            error_code=None,
            error_message=None,
            w3_result=None,
            w3_trade_decision=None,
            w3_operations_result=None,
            w3_operations_status=None,
            w3_operations_error=None,
            decision_completed_at=None,
            completed_at=None,
            resolved_route=None,
            execution_bundle_id=None,
        )
        # Old filesystem roots and production thread/result receipts are never resumed.
        original["version_pin"] = dict(
            original["version_pin"], event_library_root=None, activation_revision_id=None
        )
        prior_frozen = original.get("frozen_inputs", {})
        original["frozen_inputs"] = {
            k: prior_frozen[k]
            for k in ("provisional", "policy_watch_followup")
            if k in prior_frozen
        }
        case = RuntimeCase.model_validate(original)
        production_route = route_runtime_case(
            case.w1_final, case.w2_final, case.w2_alignment, w3_contract_version=replay_contract
        )
        would_enter = production_route.primary_route.value == "W3" or bool(
            case.frozen_inputs.get("policy_watch_followup")
        )
        d1 = (research_dir / "document1.md").read_text(encoding="utf-8-sig")
        d2 = (research_dir / "document2.json").read_text(encoding="utf-8-sig")
        d3 = (research_dir / "policy_set.original.json").read_text(encoding="utf-8-sig")
        reference = (research_dir / "reference_view.md").read_text(encoding="utf-8-sig")
        index_path = research_dir / "known_event_index.md"
        known_index = index_path.read_text(encoding="utf-8-sig") if index_path.exists() else ""
        policies = parse_policy_set(d3)
        context = W3PreparedContext(
            version_pin=W3ContextVersionPin(
                document1_run_id=case.version_pin.document1_run_id or "historical-d1",
                document2_run_id=case.version_pin.document2_run_id or "historical-d2",
                event_library_version=case.version_pin.event_library_version,
                policy_set_version=case.version_pin.policy_set_version,
            ),
            document1=d1,
            document2=parse_document2(d2),
            policy_set=policies,
            reference_view=reference,
            document2_publication_state="COMPLETE",
            original_document2=d2,
            original_policy_set=d3,
        )
        view = LegacyReplayAdapter.policy_execution_view(policies)
        usage = LegacyReplayAdapter.usage_snapshot(view, historical.get("policy_usage"))
        for policy in usage:
            for state in policy["directions"]:
                consumed = next(
                    (
                        item
                        for item in historical.get("policy_consumptions", [])
                        if item.get("policy_id", item.get("executed_policy_id"))
                        == policy["policy_id"]
                        and item.get(
                            "direction",
                            item.get(
                                "decision",
                                state["direction"] if len(policy["directions"]) == 1 else None,
                            ),
                        )
                        == state["direction"]
                        and item.get("consumed_at", item.get("activated_at"))
                        and datetime.fromisoformat(
                            item.get("consumed_at", item.get("activated_at"))
                        )
                        <= cutoff
                    ),
                    None,
                )
                if consumed:
                    state.update(consumed=True, consumption_id=consumed.get("consumption_id"))
            policy["consumed_directions"] = [
                s["direction"] for s in policy["directions"] if s["consumed"]
            ]
            policy["executable_directions"] = [
                s["direction"] for s in policy["directions"] if not s["consumed"]
            ]
        assets = {
            "w3/" + p: (self.prompt_root / p).read_text(encoding="utf-8")
            for p in ("AGENTS.md", "skills/skill.md")
        }
        if marker.exists():
            assets["w3/contract.json"] = marker.read_text(encoding="utf-8")
        evidence = {
            p.relative_to(case_dir / "evidence").as_posix(): p.read_text(encoding="utf-8")
            for p in (case_dir / "evidence").rglob("*")
            if p.is_file()
        }
        window_path = dataset / "research" / "execution_window.json"
        window = _read(window_path) if window_path.exists() else {}
        source_root = Path(__file__).resolve().parents[1]
        code_files = (
            list((source_root / "persistent_runtime_v2").glob("w3*.py"))
            + [
                source_root / "persistent_runtime_v2" / name
                for name in ("service.py", "worker_receipts.py", "trade_output.py")
            ]
            + list((source_root / "codex_worker").glob("*.py"))
            + [Path(__file__)]
        )
        code_identity = {
            str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(code_files)
        }
        fingerprint = digest(
            [
                raw,
                historical,
                d1,
                d2,
                d3,
                reference,
                known_index,
                assets,
                evidence,
                window,
                code_identity,
            ]
        )
        root = run_root / "cases" / case_key
        root.mkdir(parents=True, exist_ok=True)
        marker_path = root / "replay_manifest.json"
        if marker_path.is_file() and _read(marker_path)["input_fingerprint"] != fingerprint:
            raise ValueError(
                "Inputs, prompts or orchestration changed; create a new run instead of resuming"
            )
        _write(
            marker_path,
            dict(
                case_key=case_key,
                w3_contract_version=replay_contract,
                original_case_id=original_id,
                input_fingerprint=fingerprint,
                orchestration_fingerprint=digest(code_identity),
                cutoff_at=cutoff.isoformat(),
                environment=W3ExecutionEnvironment.offline().compatibility(),
            ),
        )
        journal = RuntimeJournal(root / "runtime.sqlite3", clock=lambda: cutoff)
        repository = SQLitePersistentRuntimeV2Repository(journal.path)
        case.frozen_inputs.update(
            w3_assets=assets,
            w3_contracts=dict(w3_entry_version="w3-entry-v5", w3_contract_version=replay_contract,
                w3_trade_schema=contract_models(replay_contract)[0].model_json_schema(),
                w3_operations_schema=contract_models(replay_contract)[1].model_json_schema()),
            projection=view,
            policy_usage=usage,
            w3_replay=dict(
                cutoff_at=cutoff.isoformat(),
                evidence_files=evidence,
                policy_execution_view=view,
                recent_trade_intents=historical.get("recent_trade_intents", []),
                history_matches=historical.get("history_matches", []),
            ),
        )
        if "watch" in historical.get("global_state", {}):
            case.frozen_inputs["w3_replay"]["global_state"] = historical["global_state"]
        bindings = window.get("bindings", [])
        binding = next((b for b in bindings if b["ticker"] == case.ticker), None)
        binding = binding or next((b for b in bindings if b["ticker"] == "*"), None)
        if binding and binding.get("exit_offset_minutes") is not None:
            from doxagent.trade_execution.sessions import Sessions

            offset = binding["exit_offset_minutes"]
            case.frozen_inputs["w3_replay"]["trading_context"] = dict(
                exit_before_regular_close_minutes=offset,
                holding_exit_at=Sessions(journal).exit_at(cutoff, offset).isoformat()
                if case.runtime_mode != "CLOSED"
                else None,
            )
        freeze_case_profile(case)
        saved = repository.get_case(case.case_id)
        if saved:
            case = saved
        else:
            repository.save_case(case)
            with journal.transaction() as db:
                for policy in usage:
                    for state in policy["directions"]:
                        db.execute(
                            "INSERT OR REPLACE INTO runtime_values VALUES(?,?,?)",
                            (
                                "policy_direction_usage",
                                encode([case.ticker, policy["policy_id"], state["direction"]]),
                                encode(state),
                            ),
                        )
                for namespace, rows, identity_field in (
                    ("policy_watch", historical.get("watches", []), "policy_id"),
                    ("message_pins", historical.get("pins", []), "pin_id"),
                ):
                    for row in rows:
                        if row.get("expires_at", "") > cutoff.isoformat():
                            db.execute(
                                "INSERT OR REPLACE INTO runtime_values VALUES(?,?,?)",
                                (
                                    namespace,
                                    encode([case.ticker, row[identity_field]]),
                                    encode(row),
                                ),
                            )
            details = historical.get("event_details") or prior_frozen.get("event_details") or {}
            if not details and (research_dir / "events.json").is_file():
                events = _read(research_dir / "events.json")
                details = events if isinstance(events, dict) else dict(canonical_events=events)
                if "events" in details and "canonical_events" not in details:
                    details = dict(canonical_events=details["events"])
            if not details:
                details = (prior_frozen.get("w1_round2_input") or {}).get("event_details", {})
            journal.set(
                "round_inputs", f"{case.case_id}:W1:R2", {"payload": {"event_details": details}}
            )
            with journal.transaction() as db:
                freeze_followup(db, case, cutoff)
            repository.save_case(case)
        worker, workspace = self.worker_factory(root / "worker")
        agent = CodexW3AgentRunner(
            worker=worker,
            workspace=workspace,
            context_provider=FrozenContextProvider(context),
            prompt_root=self.prompt_root,
            journal=journal,
            environment=W3ExecutionEnvironment.offline(),
            timeout_seconds=self.timeout_seconds,
            workspace_namespace="w3-backtest",
        )
        service = PersistentRuntimeV2Service(
            repository=repository,
            journal=journal,
            responses=_NoUpstreamCalls(),
            known_events=_FrozenEvents(
                reference
                + "\n"
                + known_index
                + "\n"
                + json.dumps(case.frozen_inputs.get("provisional", []))
            ),
            policies=_NoUpstreamCalls(),
            w3_agent=agent,
            dispatch_effects=False,
        )
        started = datetime.now(UTC)
        error = None
        try:
            execution = W3Execution(service)
            execution.trade(case)
            execution.operations(case)
        except Exception as exc:
            error = dict(type=type(exc).__name__, code=getattr(exc, "code", None), message=str(exc))
            (root / "error_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            service.close()
        saved = repository.get_case(case.case_id)
        stages = {}
        for phase in ("trade_decision", "post_decision_operations"):
            stages[phase] = execution.registry.stage(case.case_id, phase)
        audits = []
        for path in workspace_path(root / "worker" / "workspaces").glob(
            "*/attempts/*/audit/turn_summary.json"
        ):
            audit = _read(path)
            selection = path.with_name("model_selection.json")
            audit["model_selection"] = _read(selection) if selection.exists() else None
            audits.append(audit)
        audits.sort(
            key=lambda a: (
                0
                if a["attempt_id"].endswith("w3_template")
                else 1
                if a["attempt_id"].endswith("trade_decision")
                else 2
            )
        )
        from doxagent.persistent_runtime_v2.w3_context import case_labels

        labels = case_labels(
            case,
            history_matches=historical.get("history_matches", []),
            watch_followup=bool(case.frozen_inputs.get("policy_watch_followup")),
        )
        result = dict(
            case_key=case_key,
            original_case_id=original_id,
            replay_case_id=case.case_id,
            ticker=case.ticker,
            input_fingerprint=fingerprint,
            status="SUCCEEDED" if not error else "FAILED",
            error=error,
            started_at=started.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            cutoff_at=cutoff.isoformat(),
            model=case.frozen_inputs["w3_execution_profile"],
            labels=labels,
            phase_audits=audits,
            native_tool_audit=native_tool_audit(root / "worker", root / "native_tool_trace.jsonl"),
            source_title=case.source.snapshot.title,
            evidence_summary=_read(case_dir / "evidence" / "index.json")
            if (case_dir / "evidence" / "index.json").exists()
            else {},
            production_route=production_route.model_dump(mode="json"),
            would_enter_w3=would_enter or bool(case.frozen_inputs.get("policy_watch_followup")),
            direct_replay_only=not (
                would_enter or bool(case.frozen_inputs.get("policy_watch_followup"))
            ),
            decision=saved.w3_trade_decision or (stages["trade_decision"] or {}).get("result_json"),
            operations=saved.w3_operations_result
            or (stages["post_decision_operations"] or {}).get("result_json"),
            host_receipt=stages["trade_decision"],
            stages=stages,
        )
        for filename, value in (
            ("decision.json", result["decision"]),
            ("operations.json", result["operations"]),
            ("host_receipt.json", result["host_receipt"]),
            ("result.json", result),
        ):
            _write(root / filename, value)
        return result


def run_case(dataset: Path, case_key: str, run_root: Path, **kwargs):
    return ReplayRunner(**kwargs).run_case(dataset, case_key, run_root)
