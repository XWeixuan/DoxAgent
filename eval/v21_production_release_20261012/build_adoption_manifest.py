import copy
import json
import re
from pathlib import Path

from doxagent.workflows.codex_document3.schema_v21 import PolicyV3
from doxagent.workflows.codex_document3.state_v21 import digest

root = Path(__file__).parent
quality = json.loads((root / 'research_quality.json').read_text(encoding='utf-8-sig'))
validated = json.loads((root.parent / 'v21_deployment_preflight_20261012/local_contract_validation.json').read_text())
manifest = {'release': 'v21-production-20261012', 'tickers': {}}
repairs = []
for ticker in ('MU','BE','INTC'):
    prefix = {'MU':'mu-v21-20261010-02','BE':'be-v21-20261011-01','INTC':'intc-v21-20261011-01'}[ticker]
    item = validated[ticker]
    source_run = quality[ticker]['run']
    spec = dict(ticker=ticker, source_database=f'/data/{ticker.lower()}-v21-test/research.sqlite3',
                source_workspace=f'/data/{ticker.lower()}-v21-test/workspaces',
                d1_run=prefix+'-d1', d2_run=prefix+'-d2', d3_run=source_run,
                source_sha256=item['d3_sha256'],
                adoption_run=f'adopt-v21-{ticker.lower()}-20261012-'+item['d3_sha256'][:12]+'-d3',
                policy_repairs=[], repair_paths=[])
    for issue in quality[ticker]['incomplete']:
        if not issue['accepted'] or not all('unknown explicit Condition ID' in e['error'] for e in issue['errors']):
            raise ValueError('unexpected content issue requires explicit diagnosis')
        original = issue['task_outputs'][0]['raw']
        fixed = copy.deepcopy(original)
        changes = {}
        for condition in fixed['activation_conditions']:
            identity = condition['condition_id']
            match = re.fullmatch(r'pol_[0-9a-f]+:(C\d+)', identity)
            if match:
                changes[identity] = match[1]
                condition['condition_id'] = match[1]
        parsed = PolicyV3.model_validate(fixed)
        assert parsed.policy_id == issue['final_policy']['policy_id']
        spec['policy_repairs'].append(parsed.model_dump(mode='json'))
        spec['repair_paths'].append(issue['path'])
        repairs.append(dict(ticker=ticker, path=issue['path'], policy_id=parsed.policy_id,
                            source_final_edit_hash=digest(original), repaired_hash=digest(fixed),
                            identity_corrections=changes, intended_final_edit=original,
                            previously_accepted=issue['final_policy']))
    manifest['tickers'][ticker] = spec
(root / 'adoption-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2),encoding='utf-8')
(root / 'intc-repair-evidence.json').write_text(json.dumps(repairs, ensure_ascii=False, indent=2),encoding='utf-8')
print(json.dumps({'tickers':list(manifest['tickers']), 'repaired_policy_count':len(repairs)}))
