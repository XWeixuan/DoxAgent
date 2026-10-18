"""Sparse positive settlements; never materialize Case x non-hit Policy rows."""
from datetime import datetime
from doxagent.semantic_clock import semantic_day


def project(store, case, summary, event):
    ticker,case_id=case['source']['snapshot']['ticker'],case['case_id']
    final=summary['final_policy_hit']['value']
    result=(case.get('w3_result') or {}).get('policy') or case.get('w2_final') or {}
    hit_ids=set(result.get('policy_ids',[])) if final is True else set()
    previous=store.get('policy_hit_set',ticker,case_id) or {'hits':{}}
    old=previous['hits'];hits={};revisions=None
    for policy in (case.get('frozen_inputs',{}).get('projection') or {}).get('policies',[]):
        policy_id=policy['policy_id']
        if policy_id not in hit_ids:continue
        ar=policy.get('activation_revision')
        if ar is None:
            if revisions is None:
                activation=store.get('activation_revision',ticker,case['version_pin'].get('activation_revision_id') or '') or {}
                artifact=activation.get('policy_set',{}).get('artifact_id')
                from .lifecycle import rows
                revisions={}
                for detail in (rows(store,'policy_detail',ticker,artifact).values() if artifact else []):
                    item=detail['summary'];revisions.setdefault(item['policy_id'],item['policy_revision_id'])
            ar=revisions.get(policy_id)
            if ar is None:continue
        prior=old.get(policy_id,{})
        at=prior.get('occurred_at') if prior.get('ar')==ar else None
        if at is None and event['operation']!='BACKFILL':at=event['recorded_at']
        hits[policy_id]={'ar':ar,'occurred_at':at}
    records=[];contributions=[]
    if hits or old:
        records.append({'kind':'policy_hit_set','ticker':ticker,'id':case_id,'parent':case_id,
                        'data':{'hits':hits} if hits else None})
    for policy_id in sorted(set(old)|set(hits)):
        item=hits.get(policy_id)
        if item and item['occurred_at']:
            day=semantic_day(datetime.fromisoformat(item['occurred_at'])).isoformat()
            contributions.append({'metric':'policy_ar_hits','ticker':ticker,'entity':case_id+':'+policy_id,
                'day':day,'value':'1','dimensions':{'business_key':policy_id+':'+item['ar'],
                                                   'policy_id':policy_id,'ar':item['ar']}})
        elif policy_id in old:
            # The Case summary distinguishes undecided from a decided non-hit.
            contributions.append({'metric':'policy_ar_hits','ticker':ticker,'entity':case_id+':'+policy_id,
                                  'day':'','value':None})
    return records,contributions
