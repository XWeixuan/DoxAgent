"""Read-only production cohort extraction and isolated provider replay."""
import argparse
import collections
import copy
import hashlib
import json
import re
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from pydantic import BaseModel
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2.schema import W2Round1RecallResult
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient, RuntimeResponsesRequest, repair_known_json_string_quotes,
)

START = '2026-09-24T16:35:00+00:00'
END = '2026-10-01T16:35:00+00:00'
MISSING = re.compile(
    r'(?:\u672a.{0,15}(?:\u63d0\u4f9b|\u6ce8\u5165).{0,28}source_message|'
    r'\u672a.{0,15}(?:\u63d0\u4f9b|\u6ce8\u5165).{0,25}\u4e1a\u52a1\u6d88\u606f|'
    r'\u4ec5(?:\u6709|\u5305\u542b|\u6ce8\u5165).{0,25}Policy.{0,25}(?:\u6570\u636e|\u5b9a\u4e49|\u53c2\u8003)|'
    r'\u65e0.{0,20}source_message|\u6ca1\u6709.{0,20}source_message|'
    r'\u4ec5\u5305\u542b\u7cfb\u7edf\u6307\u4ee4\u4e0ePolicy)', re.I,
)
MISSING_REPLAY = re.compile(
    r'(?:\u672a.{0,15}(?:\u63d0\u4f9b|\u6ce8\u5165|\u5305\u542b|\u63a5\u6536|\u6536\u5230|\u53d1\u73b0).{0,40}(?:source_message|\u4e1a\u52a1\u6d88\u606f)|'
    r'\u65e0.{0,20}source_message|\u6ca1\u6709.{0,20}source_message|'
    r'source_message.{0,10}(?:\u4e3a\u7a7a|\u7f3a\u5931)|'
    r'\u4ec5(?:\u6709|\u5305\u542b|\u6ce8\u5165).{0,25}Policy.{0,25}(?:\u6570\u636e|\u5b9a\u4e49|\u53c2\u8003))', re.I,
)

def emit(v):
    print(json.dumps(v, ensure_ascii=True, separators=(',', ':')), flush=True)

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

class Capture:
    def __init__(self):
        self.responses = self
        self.kwargs = None
    def create(self, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError('AUDIT_CAPTURE_ONLY')

class SourceProbe(BaseModel):
    has_source_message: bool
    source_title: str
    source_body_opening: str

def wire(record, settings, variant='original'):
    f = record['frozen']
    payload = copy.deepcopy(f['payload'])
    instructions = f['instructions']
    output_model = W2Round1RecallResult
    schema_name = 'w2_round1_recall_result'
    if variant == 'probe':
        instructions = (
            'Locate the source_message object in the input. Return its title '
            'exactly, and the first sentence or first 100 characters of its body. '
            'Set has_source_message=false only if the object is absent. '
            'Policy reference data is not the source message. Do not evaluate policies.'
        )
        output_model = SourceProbe
        schema_name = 'source_visibility_probe'
    capture = Capture()
    adapter = BailianRuntimeResponsesClient(
        api_key='', base_url=settings.dashscope_chat_base_url,
        model=record['model'], reasoning_effort='medium',
        session_cache=settings.persistent_runtime_v2_session_cache_enabled,
        client=capture,
    )
    try:
        adapter.complete(RuntimeResponsesRequest(
            instructions=instructions, payload=payload,
            output_model=output_model, schema_name=schema_name,
            cache_context_keys=tuple(f['cache_context_keys']),
            metadata={'audit':'be_r1_missing_source_20261002'},
        ))
    except Exception:
        if capture.kwargs is None:
            raise
    kw = capture.kwargs
    if variant == 'two_messages':
        prefix, current = kw['input'].split('\n\n# Current Case Input\n', 1)
        kw['input'] = [
            {'role':'user', 'content':prefix},
            {'role':'user', 'content':'# Current Case Input\n'+current},
        ]
    elif variant == 'source_first':
        prefix, current = kw['input'].split('\n\n# Current Case Input\n', 1)
        kw['input'] = '# Current Case Input\n'+current+'\n\n'+prefix
    elif variant == 'reminder':
        kw['input'] += (
            '\n\nEvaluate the source_message in Current Case Input above '
            'against the policy reference data. The title and body are the '
            'current business observation, including when the body is short.'
        )
    elif variant == 'nonce_prefix':
        kw['input'] = '# Request ID: '+str(uuid.uuid4())+'\n\n'+kw['input']
    return kw

def cohort(settings):
    path = Path(settings.persistent_runtime_v2_sqlite_path)
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = NativeContent(path).row
    out=[]
    for row in db.execute(
        'SELECT case_id,source_message_id,created_at,payload_json FROM runtime_v2_cases '
        'WHERE ticker=? AND created_at>=? AND created_at<? ORDER BY created_at',
        ('BE',START,END),
    ):
        j=json.loads(row['payload_json']);r1=j.get('w2_round1')
        if r1 is None:
            continue
        fr=db.execute('SELECT payload FROM runtime_values WHERE namespace=? AND key=?',
                      ('round_inputs',row['case_id']+':W2:R1')).fetchone()
        if not fr:
            continue
        frozen=json.loads(fr['payload'])
        turns=[json.loads(t['payload_json']) for t in db.execute(
            'SELECT payload_json FROM runtime_v2_turns WHERE case_id=? AND lane=? '
            'AND round_name=? AND status=? ORDER BY attempt_number',
            (row['case_id'],'W2','R1','OK'),
        )]
        if not turns:
            continue
        t=turns[-1];s=frozen['payload']['source_message']
        out.append({
            'case_id':row['case_id'],'message_id':row['source_message_id'],
            'created_at':row['created_at'],'frozen':frozen,
            'title':s.get('title'),'body_length':len(s.get('body') or ''),
            'policy_version':j['version_pin']['policy_set_version'],
            'historical_result':r1,'historical_usage':{k:t.get(k) for k in (
                'input_tokens','cached_input_tokens','output_tokens','reasoning_tokens',
                'latency_ms','response_id','prefix_fingerprint')},
            'missing_source':bool(MISSING.search(r1.get('reason') or '')),
            'model':t['model'],
        })
    db.close()
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['extract','pilot','full'],default='extract')
    ap.add_argument('--variants',default='original,two_messages,probe')
    ap.add_argument('--jobs',default='')
    ap.add_argument('--repeat',type=int,default=1);args=ap.parse_args()
    settings=DoxAgentSettings();records=cohort(settings)
    emit({'kind':'configuration','model':settings.persistent_runtime_v2_model,
          'session_cache':settings.persistent_runtime_v2_session_cache_enabled,
          'provider_host':urlparse(settings.dashscope_chat_base_url).hostname,
          'start':START,'end':END})
    buckets=collections.defaultdict(lambda:[0,0])
    for r in records:
        n=r['body_length'];key='0' if not n else '1-350' if n<=350 else '351-1000' if n<=1000 else '>1000'
        buckets[key][0]+=1;buckets[key][1]+=r['missing_source']
    emit({'kind':'cohort_stats','length_buckets':dict(buckets),'count':len(records)})
    bad=[r for r in records if r['missing_source']]
    good_short=[r for r in records if not r['missing_source'] and r['body_length']<=350]
    positive=[r for r in good_short if r['historical_result']['candidate_policy_ids']]
    controls=(positive[:3]+[r for r in good_short if r not in positive][:3])
    if args.mode=='extract':
        for r in bad+controls:
            kw=wire(r,settings);s=r['frozen']['payload']['source_message']
            emit({'kind':'record',**r,'wire':kw,
                  'wire_hash':digest(kw['input']),
                  'source_serialized_in_wire':json.dumps(s,ensure_ascii=False,sort_keys=True,separators=(',',':')) in kw['input']})
        return
    if args.mode=='pilot':
        prefixes=['46b66498','75fa4f33','086f1441','fe333efd','86078f61','0331f3e9']
        selected=[r for r in bad if any(r['case_id'].startswith('case_'+p) for p in prefixes)]
    else:
        selected=bad
    client=BailianRuntimeResponsesClient(
        api_key=settings.require_dashscope_api_key(),base_url=settings.dashscope_chat_base_url,
        model=settings.persistent_runtime_v2_model,reasoning_effort='medium',
        timeout_seconds=120,session_cache=settings.persistent_runtime_v2_session_cache_enabled,
    )._client
    def run(r,variant,repeat):
        kw=wire(r,settings,variant);started=perf_counter()
        raw=None;response_id=None
        try:
            resp=client.responses.create(**kw)
            raw=resp.output_text;response_id=resp.id
            repaired,warnings=repair_known_json_string_quotes(raw,kw['text']['format']['name'])
            output_model=SourceProbe if variant=='probe' else W2Round1RecallResult
            result=output_model.model_validate_json(repaired).model_dump(mode='json')
            u=resp.usage.model_dump() if resp.usage else None
            return {'kind':'replay','case_id':r['case_id'],'variant':variant,'repeat':repeat,
                    'result':result,'missing_source':bool(MISSING_REPLAY.search(result.get('reason') or '')),
                    'source_title_exact':result.get('source_title')==r['title'] if variant=='probe' else None,
                    'raw_output':raw,'validation_warnings':warnings,
                    'usage':u,'response_id':resp.id,'latency_seconds':round(perf_counter()-started,3)}
        except Exception as e:
            return {'kind':'replay','case_id':r['case_id'],'variant':variant,'repeat':repeat,
                    'error_type':type(e).__name__,'raw_output':raw,'response_id':response_id,
                    'latency_seconds':round(perf_counter()-started,3)}
    jobs=[(r,v,n) for n in range(args.repeat) for r in selected for v in args.variants.split(',')]
    if args.jobs:
        byid={r['case_id']:r for r in bad+controls}
        jobs=[(byid[cid],v,n) for n in range(args.repeat) for cid,v in (x.split('@') for x in args.jobs.split(','))]
    emit({'kind':'dispatch','cases':len({r['case_id'] for r,_,_ in jobs}),
          'requests':len(jobs),'variants':sorted({v for _,v,_ in jobs})})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run,*job) for job in jobs]
        for f in as_completed(futures):emit(f.result())

if __name__=='__main__':main()
