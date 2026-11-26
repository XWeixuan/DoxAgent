"""Regenerable D3 representations backed by the exact typed release."""
from pathlib import Path
import re

PREFIX='d3-semantic-v1:'
NAMES={'document3.json','runtime_projection.json','coverage_map.json','policy_origins.json','document3.md'}


def locator(database, run_id, name):
    if name not in NAMES or ':' in run_id or Path(database).name!=str(database):
        raise ValueError('invalid D3 export locator')
    return PREFIX+f'{database}:{run_id}:{name}'


def render(directory, record):
    from .state_v21 import StateV21, canonical
    from .consumer_contracts import parse_policy_set
    from .runtime_projection import project_policy_set_v3
    from .publication_v21 import render_policy_markdown

    database,run_id,name=record['storage_path'][len(PREFIX):].split(':')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',database) or database in {'.','..'} or name not in NAMES or run_id!=record['run_id']:
        raise ValueError('invalid D3 semantic source')
    state=StateV21(Path(directory)/database,read_only=True)
    staged=state.get_staged_by_run(run_id)
    if staged is None:
        raise ValueError('HISTORY_RETIRED: D3 release is outside retained business history')
    policy=parse_policy_set(staged[0]);receipt=staged[1]
    if name=='document3.json':
        text=policy.model_dump_json(indent=2)
    elif name=='runtime_projection.json':
        text=canonical(project_policy_set_v3(policy))
    elif name=='policy_origins.json':
        text=canonical(receipt.get('policy_origins',{}))
    else:
        coverage=receipt.get('coverage')
        if coverage is None:
            coverage=state.coverage(run_id)
        if coverage is None:
            raise ValueError('D3 semantic coverage unavailable')
        text=canonical(coverage) if name=='coverage_map.json' else render_policy_markdown(policy,status=policy.publication_state,coverage=coverage)
    return text.encode('utf-8')
