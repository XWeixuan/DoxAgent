from __future__ import annotations
import asyncio,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from doxagent.codex_worker import sdk_runtime
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_runtime.schema import CodexPersistentRuntimeNode,CodexPersistentRuntimeAgentRole
from doxagent.settings import DoxAgentSettings
from datetime import datetime,timezone
class _RawSdkResult:
    id = "turn-sdk-1"
    status = "completed"
    error = None
    final_response = '{"status":"completed"}'

class _RawSdkHandle:
    async def run(self):
        return _RawSdkResult()

    async def interrupt(self) -> None:
        return None

class _AsyncSdkTurn:
    id = "thread-sdk-1"

    def __init__(self) -> None:
        self.turn_was_awaited = False
        self.turn_kwargs: dict[str, object] | None = None

    async def turn(self, *args, **kwargs):
        self.turn_was_awaited = True
        self.turn_kwargs = kwargs
        return _RawSdkHandle()

class _AsyncSdkClient:
    def __init__(self, *args, **kwargs) -> None:
        self.thread = _AsyncSdkTurn()
        self.thread_start_kwargs: dict[str, object] | None = None
        self.thread_resume_id: str | None = None
        self.thread_resume_kwargs: dict[str, object] | None = None

    async def thread_start(self, **kwargs):
        self.thread_start_kwargs = kwargs
        return self.thread

    async def thread_resume(self, thread_id: str, **kwargs):
        self.thread_resume_id = thread_id
        self.thread_resume_kwargs = kwargs
        return self.thread
async def main():
    results=[]
    for phase in ('trade_decision','post_decision_operations'):
        sdk=_AsyncSdkClient()
        sdk_runtime.AsyncCodex=lambda *a,**k:sdk
        runtime=sdk_runtime.OpenAICodexRuntime(capability_secret='s'*32,container_isolated=True,
            settings=DoxAgentSettings(ibkr_tws_enabled=False))
        with tempfile.TemporaryDirectory(prefix='w3-release-config-') as folder:
            await runtime.start(WorkerRunRequest(workflow_version='persistent_runtime_w3_v1',
                research_lane='persistent_runtime',run_id='config-probe',ticker='MU',
                node=CodexPersistentRuntimeNode.W3,agent_role=CodexPersistentRuntimeAgentRole.W3,
                attempt_id='probe',cutoff_at=datetime.now(timezone.utc),prompt='configuration probe',
                output_schema={'type':'object'},task_phase=phase,read_only=True,data_mcp_enabled=False,
                allow_subagents=False,model='gpt-6-luna',effort='max'),Path(folder))
            cfg=sdk.thread_start_kwargs['config']
            results.append(dict(phase=phase,model=sdk.thread_start_kwargs['model'],
                tier=cfg['service_tier'],fast_mode=cfg['features.fast_mode']))
    assert results[0]['tier']=='fast' and results[0]['fast_mode']
    assert results[1]['tier']=='default' and not results[1]['fast_mode']
    print(json.dumps(dict(model_turns=0,configuration=results)))
asyncio.run(main())
