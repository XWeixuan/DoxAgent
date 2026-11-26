"""Local, decision-only evaluation of recent production Cases with live tools.

This module never constructs a production runtime service or an effect dispatcher.
SSH is used only by the host acquisition and fixed market-history adapter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .decision_runner import DecisionReplayRunner
from .enrich import acquire_contexts, context_from_case
from .runner import _LocalWorker, local_worker


def remote_python(script, host="doxagent-sg"):
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", host,
         "sudo -n docker exec -i doxagent-v2-v2-scheduler-1 python -"],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-1800:])
    return json.loads(result.stdout)


def acquire(case_id, destination, host="doxagent-sg"):
    """Read the exact Case and its pinned documents using query-only SQLite."""
    if not case_id.startswith("case_") or not case_id[5:].isalnum():
        raise ValueError("invalid production Case ID")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "native.json").exists():
        raise ValueError("source snapshot already exists")
    script = """
import json,sqlite3
from pathlib import Path
from doxagent.v2_read.native_content import NativeContent
from doxagent.codex_runtime.published_storage import load_published_body
root=Path('/data')
def connection(path):
 db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.execute('pragma query_only=ON')
 return db,NativeContent(path)
rt,rc=connection(root/'runtime/runtime.sqlite3')
rs,sc=connection(root/'research/research.sqlite3')
def load(db,codec,sql,args):
 row=db.execute(sql,args).fetchone()
 return codec.decode(json.loads(row[0])) if row else None
c=load(rt,rc,'select payload_json from runtime_v2_cases where case_id=?',(CASE_ID,))
if c is None:raise ValueError('Case not found')
research={};errors=[]
for name,key in [('document1','document1_run_id'),('document2','document2_run_id')]:
 try:
  run=c['version_pin'].get(key)
  b=load(rs,sc,"select payload_json from codex_runtime_records where record_type='bundles' and record_key=?",(run,))
  h=b['handoff'];aid=h.get('document_artifact_id') or h.get('document2_artifact_id')
  doc=load(rs,sc,"select payload_json from codex_runtime_records where record_type='published_documents' and run_id=? and record_key=?",(run,aid))
  text=doc.get('content_text')
  if text is None and doc.get('local_content_codec') is not None:text=load_published_body(root/'research',doc).decode('utf-8')
  if text is None and doc.get('storage_path'):text=Path(doc['storage_path']).read_text()
  research[name]=dict(content=text,run_id=run,created_at=doc.get('created_at') or b.get('created_at'))
 except Exception as e:errors.append(name+':'+str(e))
active=load(rt,rc,"select payload from runtime_values where namespace='execution' and key='active'",())
bundle=load(rt,rc,'select payload from runtime_snapshots where id=?',(active,))
print(json.dumps(dict(case=c,research=research,recovery_errors=errors,
 prompt_bundle_id=active,assets={k:v for k,v in bundle['assets'].items() if k in ['w3/AGENTS.md','w3/skills/skill.md']},
 contracts=bundle['contracts'])))
""".replace("CASE_ID", repr(case_id))
    native = remote_python(script, host)
    context_rows = acquire_contexts([case_id], host)
    context_row = context_rows[case_id]
    context = context_from_case(
        context_row["case"], events=context_row.get("events"),
        state=context_row.get("state"), recent=context_row.get("recent"),
        research_cutoffs={name: value["created_at"] for name, value in
                          (("D1", native["research"].get("document1")),
                           ("D2", native["research"].get("document2"))) if value},
    )
    (destination / "native.json").write_text(json.dumps(native, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    prompts = destination / "production_prompts"
    for key, text in native["assets"].items():
        target = prompts / key.removeprefix("w3/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (prompts / "contract.json").write_text(json.dumps(dict(w3_contract_version="persistent-runtime.w3.v6")), encoding="utf-8")
    return native


class HistoryProvider:
    """The model has no IBKR connection, account credentials or SSH identity."""
    def history(self, ticker, bar, duration, venue="SMART", end=None):
        from urllib.request import Request, urlopen

        body = json.dumps(dict(ticker=ticker, bar=bar, duration=duration, venue=venue,
                               end=end.isoformat() if end else None)).encode()
        request = Request(os.environ["LOCAL_W3_HISTORY_URL"], data=body,
                          headers={"Authorization": "Bearer " + os.environ["LOCAL_W3_HISTORY_TOKEN"],
                                   "Content-Type": "application/json"})
        with urlopen(request, timeout=100) as response:
            result = json.load(response)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result["bars"]

    def close(self):
        pass


class EvaluationWorker(_LocalWorker):
    async def run(self, request):
        if request.task_phase not in {"w3_template", "trade_decision"}:
            raise ValueError("only template and trade_decision are allowed")
        return await self.client.run(request)

    async def _ensure_research(self):
        if self.research is not None:
            return
        import uvicorn
        from doxagent.codex_worker.w3_market import W3MarketPreparer
        from doxagent.mcp.w3_research_server import ResearchMcp
        from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore
        from doxagent.settings import DoxAgentSettings

        self.research = W3ResearchStore(self.manager.store.root)
        await asyncio.to_thread(self.research.initialize)
        self.market = W3MarketPreparer(
            self.research, DoxAgentSettings(_env_file=None, ibkr_tws_enabled=True),
            provider=HistoryProvider(),
        )
        mcp = ResearchMcp(self.research, "w3-local-replay-only-no-mcp-capabilities", self.market)
        self.mcp_context = mcp.manager.run()
        await self.mcp_context.__aenter__()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(mcp, lifespan="off", log_level="error"))
        self.serving = asyncio.create_task(self.server.serve(sockets=[sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        self.manager._runtime.runtime._settings.w3_research_mcp_url = f"http://127.0.0.1:{port}/"

    async def bind_w3_context(self, case_id, phase, manifest):
        if phase != "trade_decision":
            raise ValueError("local evaluation supports trade_decision only")
        await self._ensure_research()
        manifest = dict(manifest, mode="LOCAL_EVALUATION")
        manifest.pop("market_view", None)
        binding = await super().bind_w3_context(case_id, phase, manifest)
        self.market.start(manifest)
        return binding

    async def start_w3_market(self, manifest):
        await self._ensure_research()
        self.market.start(manifest)

    async def prepare_thread(self, request):
        request = request.model_copy(update={"profile": {**request.profile, "web_search_mode": "live"}})
        return await self.client.prepare_thread(request)

    async def aclose(self):
        if hasattr(self, "market"):
            await self.market.close()
        await super().aclose()


def evaluation_worker(root, service_tier=None):
    from openai_codex import AsyncCodex
    from doxagent.codex_worker.thread_lifecycle import AppServerThreads

    worker, workspace = local_worker(root, time_awareness_enabled=True, service_tier=service_tier)
    runtime = worker.manager._runtime.runtime
    config = replace(runtime._config, config_overrides=tuple(
        value for value in runtime._config.config_overrides if value != 'web_search="disabled"'
    ) + ('web_search="live"',))
    runtime._config = config
    runtime._client = AsyncCodex(config)
    worker.manager.thread_lifecycle.transport = AppServerThreads(config)
    return EvaluationWorker(worker.manager), workspace


def run(source, output, prompts, model=None, effort=None, service_tier=None):
    from doxagent.persistent_runtime_v2.w3 import W3ExecutionEnvironment

    source, output, prompts = Path(source), Path(output), Path(prompts)
    output.mkdir(parents=True, exist_ok=False)
    native = json.loads((source / "native.json").read_text(encoding="utf-8"))
    original = native["case"]
    now = datetime.now(UTC).isoformat()
    identity = "eval_" + uuid.uuid4().hex
    data = output / "dataset"
    folder = data / "inputs" / identity
    (folder / "evidence").mkdir(parents=True)
    message = dict(original["source"]["snapshot"])
    message.update({key: original["source"].get(key) for key in ("published_at", "url", "publication_time_basis")})
    raw = dict(case_key=identity, original_case_id=original["case_id"],
               ticker=message["ticker"], message=message, as_of=now,
               w1=original.get("w1_final") or {}, w2=original.get("w2_final") or {},
               research_key="pinned")
    (folder / "case.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(source / "context.json", folder / "context.json")
    research = data / "research/pinned"
    research.mkdir(parents=True)
    for key, name in (("document1", "document1.md"), ("document2", "document2.json")):
        if key in native["research"]:
            (research / name).write_text(native["research"][key]["content"], encoding="utf-8")
    pack = output / "prompts"
    pack.mkdir()
    for name in ("AGENTS.md", "skills/skill.md", "contract.json"):
        if (prompts / name).is_file():
            target = pack / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prompts / name, target)
    if not (pack / "AGENTS.md").is_file() or not (pack / "AGENTS.md").read_text(encoding="utf-8-sig").strip():
        raise ValueError("nonempty AGENTS.md is required")
    if (prompts / "skill.md").is_file() and not (pack / "skills/skill.md").exists():
        (pack / "skills").mkdir(exist_ok=True)
        shutil.copyfile(prompts / "skill.md", pack / "skills/skill.md")
    if not (pack / "contract.json").exists():
        (pack / "contract.json").write_text(
            json.dumps(dict(w3_contract_version="persistent-runtime.w3.v6")), encoding="utf-8"
        )
    (pack / "backtest.md").write_text("", encoding="utf-8")
    runner = DecisionReplayRunner(
        data, output, prompt_root=pack, model=model, effort=effort,
        service_tier=service_tier, time_awareness_enabled=True,
        worker_factory=lambda worker_root: evaluation_worker(worker_root, service_tier),
    )
    runner.agent.environment = W3ExecutionEnvironment(mode="LOCAL_EVALUATION", web_search_mode="live", data_mcp_enabled=False)
    # Restrict unified-business-file support to this local agent instance.
    runner.agent._optional_replay_skill = lambda case, assets: (
        {"skills/skill.md": assets["w3/skills/skill.md"]} if "w3/skills/skill.md" in assets else {}
    )
    try:
        result = runner.run_case(data, identity, output)
        result.update(evaluation_id=identity, evaluation_started_at=now,
                      original_case_created_at=original.get("created_at"),
                      message_published_at=original["source"].get("published_at"),
                      source_case_id=original["case_id"], evaluation_scope="local_live_decision_only")
        result["research_versions"] = original.get("version_pin", {})
        result["capabilities"] = dict(web_search="live", research_assets="pinned",
                                      market="live_read_only", operations=False, trade_execution=False)
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        runner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("acquire")
    prepare.add_argument("--case-id", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--host", default="doxagent-sg")
    execute = sub.add_parser("run")
    execute.add_argument("--source", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--prompt-root", type=Path)
    execute.add_argument("--model")
    execute.add_argument("--effort")
    execute.add_argument("--service-tier", choices=("fast", "default"))
    evaluate = sub.add_parser("evaluate", help="Acquire by Case ID and execute locally in Docker")
    evaluate.add_argument("--case-id", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--prompt-root", type=Path)
    evaluate.add_argument("--model")
    evaluate.add_argument("--effort")
    evaluate.add_argument("--service-tier", choices=("fast", "default"))
    evaluate.add_argument("--host", default="doxagent-sg")
    evaluate.add_argument("--rounds", type=int, default=1)
    evaluate.add_argument("--parallel", type=int, default=4)
    evaluate.add_argument("--runtime-dir", type=Path, required=True)
    evaluate.add_argument("--image", required=True)
    args = parser.parse_args()
    if args.command == "acquire":
        value = acquire(args.case_id, args.output, args.host)
        print(json.dumps(dict(case_id=value["case"]["case_id"], recovery_errors=value["recovery_errors"])))
    elif args.command == "run":
        value = run(args.source, args.output, args.prompt_root or args.source / "production_prompts",
                    args.model, args.effort, args.service_tier)
        print(json.dumps(dict(status=value["status"], decision=value["decision"], error=value["error"]), ensure_ascii=False))
    else:
        from .live_launcher import evaluate
        evaluate(args)


if __name__ == "__main__":
    main()
