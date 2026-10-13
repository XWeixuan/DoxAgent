"""Real model smoke, isolated state and Worker; never submit a trading intent.""" 
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from doxagent.settings import DoxAgentSettings
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
from doxagent.workflows.codex_document3.state_v21 import StateV21
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.persistent_runtime_v2.factory import build_persistent_runtime_v2_service
from doxagent.persistent_runtime_v2.schema import RuntimeCase, W3RouteCase, W3ThreadSlot, W2PolicyAssessmentResult
from doxagent.persistent_runtime_v2.w3 import PublishedW3ContextProvider

ROOT=Path('/data/v21-production-validation')
OPS=Path('/ops')
settings=DoxAgentSettings(_env_file=None)
settings=settings.model_copy(update={
    'codex_runtime_sqlite_path':str(ROOT/'research.sqlite3'),
    'codex_workspace_root':str(ROOT/'workspaces'),
    'persistent_runtime_v2_sqlite_path':str(ROOT/'runtime.sqlite3'),
    'ticker_initialization_control_path':str(ROOT/'control.sqlite3'),
    'codex_worker_base_url':'http://v21-production-smoke-worker:8791',
    'codex_published_storage_url':None, 'codex_published_storage_secret_key':None,
    'message_bus_v2_enabled':False,
})
manifest=json.loads((OPS/'ops/adoption-manifest.json').read_text())
production=json.loads((OPS/'before-window.json').read_text())
refs={r['ticker']:r['artifacts'] for r in production['semantics']['active_revisions']}
repository=SQLiteCodexRuntimeRepository(ROOT/'research.sqlite3')
policies=SQLiteDocument3PolicyRepository(ROOT/'research.sqlite3')
state=StateV21(ROOT/'research.sqlite3')
now=datetime.now(timezone.utc)
report=json.loads((ROOT/'model-smoke.json').read_text()) if (ROOT/'model-smoke.json').exists() else {}

def save():
    (ROOT/'model-smoke.json').write_text(json.dumps(report,indent=2,default=str))

def case_for(ticker, message):
    spec=manifest['tickers'][ticker]
    policy, receipt=state.get_staged_by_run(spec['adoption_run'])
    version=policy['policy_set_version']
    return RuntimeCase.model_validate(dict(case_id='v21-smoke-'+ticker.lower(),
        orchestration_revision=3,trading_date=now.date().isoformat(),
        source=dict(source_message_id='isolated-fixture-'+ticker,source_id='deployment-smoke',
            stream_item_id='isolated-stream-'+ticker,member_count=1,
            binding_id=ticker+':deployment-smoke',url='https://example.test/v21-deployment-smoke',
            published_at=now.isoformat(),collected_at=now.isoformat(),message_bus_event_time=now.isoformat(),
            snapshot=dict(ticker=ticker,title='Isolated synthetic deployment fixture',body=message)),
        version_pin=dict(document1_run_id=spec['d1_run'],document2_run_id=spec['d2_run'],
            event_library_root=refs[ticker]['event_library']['root'],
            event_library_version=refs[ticker]['event_library']['version'],
            policy_set_version=version,runtime_projection_version=version,provisional_snapshot_version=0)))

async def contexts():
    provider=PublishedW3ContextProvider(runtime_repository=repository,policy_repository=policies,
        event_library_reader=PublishedEventLibraryReader('/data/events'))
    for ticker in manifest['tickers']:
        context=await provider.load(case_for(ticker,'Deployment fixture.'))
        report[ticker]=dict(document1_chars=len(context.document1),
            document2_bytes=len(context.document2.model_dump_json().encode()),
            policy_count=len(context.policy_set.policies),reference_chars=len(context.reference_view),
            version_pin=context.version_pin.model_dump(mode='json'))
    save()

asyncio.run(contexts())
service=build_persistent_runtime_v2_service(settings)
policy=policies.get_version('MU',1).policies[0]
condition=policy.activation_conditions[0]
message=('Synthetic fixture used solely to test contract plumbing. Assume the following formal '
         'company announcement is newly published and its stated facts verified: '+condition.criterion+
         '\nMeasured outcome is explicitly on the triggering side of the calibrated boundary: '+
         condition.calibration.trigger_boundary+'. This fixture is not actual market news.')
case=case_for('MU',message)
case.frozen_inputs['w3_assets']={'w3/'+p.relative_to(OPS/'prompts/persistent_runtime_v2/w3').as_posix():p.read_text()
    for p in (OPS/'prompts/persistent_runtime_v2/w3').rglob('*') if p.is_file()}
service.repository.save_case(case)
started=time.monotonic()
if 'w2' in report:
    r2=W2PolicyAssessmentResult.model_validate(report['w2']['r2'])
else:
    r1,r2,response=service._run_w2_hot(case,policies.get_projection('MU',1))
    report['w2']=dict(seconds=time.monotonic()-started,r1=r1.model_dump(mode='json'),
        r2=r2.model_dump(mode='json'),response_id=response)
save()
route=W3RouteCase.model_validate(dict(case_id=case.case_id,ticker='MU',source=case.source.model_dump(mode='json'),
    orchestration_revision=3,w1_final=dict(result='NEW',confidence='normal',reference_ids=[],reason='Isolated synthetic new fact'),
    w2_final=r2.model_dump(mode='json'),event_library_version=case.version_pin.event_library_version,
    policy_set_version=1,route_reason='Isolated deployment condition-selection verification',
    mode='REVALIDATE_THEN_EVALUATE',attempt_count=2))
slot=W3ThreadSlot(ticker='MU',case_id=case.case_id,kind='MAIN')
started=time.monotonic()
try:
    if 'w3' not in report:
        result,thread,pin=service._w3_agent.run(case=case,w3_case=route,slot=slot)
        report['w3']=dict(seconds=time.monotonic()-started,result=result.model_dump(mode='json'),
            thread=thread,pin=pin.model_dump(mode='json'))
        save()
finally:
    service.close()

async def maintain():
    worker=HttpCodexWorkerClient(str(settings.codex_worker_base_url),settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret)
    o3=build_document3_orchestrator(settings,worker=worker,orchestration_version='v2.1')
    note=ROOT/'maintain-smoke-scope.md'
    note.write_text('This is an isolated deployment smoke test. No new research evidence or consumption exists. '
        'Read the migrated base, D1/D2 and current Event reference. Check one inherited Policy for '
        'contract readability. Do not perform external research or resolve historical coverage gaps. '
        'Return a valid no-business-change maintenance patch; retain existing policies, authorization and gaps. '
        'The purpose is to verify the new single MAINTAIN turn and its output contract, not to approve research quality.')
    event=refs['MU']['event_library']
    configured=o3.preparer.legacy
    configured._event_library_reader=PublishedEventLibraryReader(event['root'])
    started=time.monotonic()
    try:
        result=await o3.maintain(ticker='MU',base_policy_version=1,as_of=now,
            event_library_version=event['version'],explicit_maintenance=True,
            additional_materials=[str(note)],run_id='v21-deployment-maintain-smoke-3')
        report['maintain']=dict(seconds=time.monotonic()-started,result=result.model_dump(mode='json'))
        save()
    finally:
        await worker.aclose()

asyncio.run(maintain())
print(json.dumps(report,default=str))
