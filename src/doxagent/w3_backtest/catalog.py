"""Host-only reviewed metadata. Never imported by model input assembly."""
import json
from pathlib import Path

SUITES = ('decision', 'timeliness')
SPLITS = ('dev', 'validation')
TAGS = {'edge_case', 'parallel_misjudgment', 'sporadic_error', 'systematic_error', 'timeliness_nontradable'}

def load_catalog(dataset):
    path = Path(dataset) / 'catalog.jsonl'
    marker = Path(dataset) / 'manifest.json'
    if not marker.exists() or json.loads(marker.read_text(encoding='utf-8')).get('catalog_version') != 'w3-backtest.catalog.v1':
        return []
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    ids, keys = set(), set()
    for row in rows:
        if row['case_id'] in ids or row['case_key'] in keys:
            raise ValueError('duplicate Catalog identity')
        ids.add(row['case_id']); keys.add(row['case_key'])
        if row['suite'] not in SUITES or row['split'] not in SPLITS or row['gold'] not in {'LONG','SHORT','NO_TRADE'}:
            raise ValueError('invalid Catalog membership or gold')
        if row['gold_confidence'] not in {'A','B'} or row['transmission'] not in {'direct','indirect','industry'}:
            raise ValueError('invalid Catalog confidence or transmission')
        if row['case_source'] not in {'correct_no_trade','correct_trade','false_trade','missed_trade'} or set(row['tags'])-TAGS:
            raise ValueError('invalid Catalog history or tags')
        if row['suite']=='timeliness' and row['gold']=='NO_TRADE':
            raise ValueError('timeliness suite requires valuable directional event')
        if row['suite']=='decision' and 'timeliness_nontradable' in row['tags']:
            raise ValueError('expired event belongs to timeliness suite')
    return rows

def expected_decision(row):
    return 'NO_TRADE' if 'timeliness_nontradable' in row.get('tags',[]) else row['gold']

def scoring_gold(dataset):
    rows=load_catalog(dataset)
    if not rows:
        p=Path(dataset)/'gold.jsonl'
        return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()] if p.exists() else []
    return [{**r, 'gold_source':'Case Gold Review', 'expected_action':'NO_TRADE' if expected_decision(r)=='NO_TRADE' else 'TRADE',
             'expected_direction': None if expected_decision(r)=='NO_TRADE' else expected_decision(r)} for r in rows]

def select_cases(dataset, cases, *, keys=None, suite=None, split=None):
    catalog=load_catalog(dataset)
    if (suite or split) and not catalog:
        raise ValueError('suite/split filters require a reviewed Catalog')
    if suite is not None and suite not in SUITES or split is not None and split not in SPLITS:
        raise ValueError('invalid suite/split')
    aliases={r['case_id']:r['case_key'] for r in catalog}
    wanted={aliases.get(k,k) for k in keys} if keys else None
    available={c['case_key'] for c in cases}
    if wanted and wanted-available:raise ValueError('unknown case keys: '+str(sorted(wanted-available)))
    members={r['case_key'] for r in catalog if (not suite or r['suite']==suite) and (not split or r['split']==split)}
    return [c for c in cases if (wanted is None or c['case_key'] in wanted) and (not catalog or c['case_key'] in members)]
