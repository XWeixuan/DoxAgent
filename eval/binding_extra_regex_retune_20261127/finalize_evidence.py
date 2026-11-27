"""Produce reproducible counts and a review ledger, never infer full-body GOLD."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / 'binding_extra_regex_20261127'


def main():
    results = list(csv.DictReader((ROOT/'all-history-results.csv').open(encoding='utf-8-sig')))
    blocked = {(r['ticker'],r['id']) for r in results if r['extra_allowed']=='False'}
    original = {(r['ticker'],r['id']) for r in csv.DictReader((BASE/'all-history-results.csv').open(encoding='utf-8-sig')) if r['extra_allowed']=='False'}
    audit = Path(__file__).resolve().parents[3] / 'eval/distribution_title_audit_20261127/title-review.jsonl'
    if not audit.exists():
        audit = Path('C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_title_audit_20261127/title-review.jsonl')
    reviewed = [json.loads(line) for line in audit.read_text(encoding='utf-8-sig').splitlines()]
    keys = {(r['ticker'],r['id']) for r in results}
    overlap = [r for r in reviewed if (r['ticker'],r['id']) in keys]
    counter = Counter(r['review'] for r in overlap)
    comparison = {'comparable_cases':len(overlap),'title_warning_labels':dict(counter),
        'old_blocked':dict(Counter(r['review'] for r in overlap if (r['ticker'],r['id']) in original)),
        'new_blocked':dict(Counter(r['review'] for r in overlap if (r['ticker'],r['id']) in blocked)),
        'old_total_blocked':len(original),'new_total_blocked':len(blocked),
        'new_exclusions':len(blocked-original),'old_exclusions_now_retained':len(original-blocked),
        'caveat':'Title warnings are diagnostic candidates, not independent full-body GOLD or precision/recall.'}
    (ROOT/'prior-audit-overlap.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    labels={(r['ticker'],r['id']):r['review'] for r in overlap}
    cases=json.loads((BASE/'cases.json').read_text(encoding='utf-8'))['cases']
    rows=[]
    for r in cases:
        key=r['ticker'],r['id']
        if key not in blocked: continue
        rows.append({k:r[k] for k in ('ticker','id','source_id','title','url')} | {
            'prior_title_review':labels.get(key,'OUTSIDE_RECENT_AUDIT'),
            'review_scope':'TITLE_REVIEW_WITH_TARGET_BUSINESS_CUE_CHECK; risk/core cases checked in context',
            'review_decision':'ACCEPT_EXTRA_FILTER_UNDER_CURRENT_SCOPE',
            'caveat':'Not a full-body GOLD label; weak indirect links may be intentionally excluded',
            'body_present':bool(r.get('body')), 'body_sha256':hashlib.sha256((r.get('body')or'').encode()).hexdigest()})
    with (ROOT/'blocked-review.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps(comparison,ensure_ascii=False))


if __name__=='__main__': main()
