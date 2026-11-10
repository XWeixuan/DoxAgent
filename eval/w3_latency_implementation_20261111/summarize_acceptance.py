"""Export only timing/version/resource evidence, never frozen business inputs."""
import json
from pathlib import Path
import sys


def summarize(root):
    root=Path(root)
    result={}
    for name in ['build.json','release.json']:
        if (root/name).exists():result[name]=json.loads((root/name).read_bytes())
    for name in ['acceptance-frozen.json','acceptance-final.json','acceptance-followthrough.json','acceptance-10min.json']:
        if not (root/name).exists():continue
        value=json.loads((root/name).read_bytes())
        keys=['at','since','until','summary','full_case','trace_count','case_rows','case_ids',
              'selector_truncated','metadata_budget_exhausted','log_budgets','trace_parse_errors',
              'unattributed_tool_receipts','external_provider_wait_excluded_s']
        compact={k:value[k] for k in keys if k in value}
        compact['phases']=[{k:r[k] for k in ['case_id','phase','complete','missing','non_sdk_s',
                                           'parts','sdk_envelope_s','nested_dox_tool_s','known_job_queue_s',
                                           'known_result_to_stage_commit_lower_bound_s',
                                           'known_boundary_internal_lower_bound_s'] if k in r}
                           for r in value.get('rows',[])]
        result[name]=compact
    p=root/'observation-final.jsonl'
    if p.exists():
        all_rows=[json.loads(line) for line in p.read_text().splitlines()]
        rows=[r for r in all_rows if 'memory' in r]
        if rows:
            a,b=rows[0],rows[-1]
            resource=dict(samples=len(all_rows),errors=len(all_rows)-len(rows),first=a['at'],last=b['at'],
                          elapsed_s=b['elapsed_s']-a['elapsed_s'],
                          min_available_gib=min(r['memory']['MemAvailable'] for r in rows)/1048576,
                          vmstat_delta={k:b['vmstat'][k]-a['vmstat'][k] for k in a['vmstat']},roles={})
            for role in a['roles']:
                selected=[r['roles'][role] for r in rows]
                resource['roles'][role]=dict(images=sorted({r['image'] for r in selected}),
                    started_at=sorted({r['started_at'] for r in selected}),
                    max_restarts=max(r['restarts'] for r in selected),oom=any(r['oom'] for r in selected),
                    all_running=all(r['running'] for r in selected),
                    max_memory_gib=max(int(r.get('counters',{}).get('memory.current',0)) for r in selected)/1073741824)
            result['resource']=resource
    return result


if __name__=='__main__':
    print(json.dumps(summarize(sys.argv[1]),ensure_ascii=False,indent=2))
