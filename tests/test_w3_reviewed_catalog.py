import json
import pytest
from doxagent.w3_backtest.catalog import expected_decision, scoring_gold, select_cases, load_catalog
from doxagent.w3_backtest.report import score

def fixture(tmp_path, **changes):
    row=dict(case_id='native1',case_key='alias1',suite='timeliness',split='dev',case_source='false_trade',tags=['timeliness_nontradable'],transmission='direct',gold='LONG',gold_confidence='A')
    row.update(changes)
    (tmp_path/'manifest.json').write_text(json.dumps(dict(catalog_version='w3-backtest.catalog.v1')))
    (tmp_path/'catalog.jsonl').write_text(json.dumps(row)+'\n')
    return row

def test_event_gold_and_time_veto_are_separate(tmp_path):
    fixture(tmp_path)
    g=scoring_gold(tmp_path)[0]
    assert g['gold']=='LONG' and g['expected_action']=='NO_TRADE'
    assert score({'decision':{'decision':'NO_TRADE'}},g,'NO_TRADE')['score'] is True
    assert score({'decision':{'decision':'LONG'}},g,'LONG')['category']=='WRONG_TRADE'
    assert 'expected_action' not in (tmp_path/'catalog.jsonl').read_text()

def test_fresh_directional_control_is_valid(tmp_path):
    row=fixture(tmp_path,tags=[])
    assert expected_decision(row)=='LONG'
    assert scoring_gold(tmp_path)[0]['expected_direction']=='LONG'

def test_native_id_selection_and_empty_validation(tmp_path):
    fixture(tmp_path)
    cases=[{'case_key':'alias1'}]
    assert select_cases(tmp_path,cases,keys=['native1'],suite='timeliness',split='dev')==cases
    assert select_cases(tmp_path,cases,split='validation')==[]

def test_timeliness_rejects_no_event_value(tmp_path):
    fixture(tmp_path,gold='NO_TRADE')
    with pytest.raises(ValueError,match='valuable'):load_catalog(tmp_path)

def test_old_runs_keep_their_gold_contract(tmp_path):
    (tmp_path/'gold.jsonl').write_text(json.dumps(dict(case_key='old',expected_action='TRADE',expected_direction='SHORT'))+'\n')
    assert scoring_gold(tmp_path)[0]['expected_direction']=='SHORT'

def test_catalog_never_enters_agent_payload(tmp_path):
    from doxagent.w3_backtest.decision_runner import agent_payload
    fixture(tmp_path)
    raw=dict(case_key='alias1',ticker='MU',as_of='2026-11-16T12:00:00Z',message=dict(title='company report',body='facts'),w1={},w2={})
    wire=json.dumps(agent_payload(raw))
    assert all(field not in wire for field in ['gold_confidence','case_source','timeliness_nontradable','expected_action'])

def test_report_scores_catalog_and_reports_collection(tmp_path):
    from doxagent.w3_backtest.report import render_report
    data=tmp_path/'data';data.mkdir();fixture(data)
    (data/'manifest.json').write_text(json.dumps(dict(catalog_version='w3-backtest.catalog.v1',schema_version='w3-backtest.dataset.v2',cases=[dict(case_key='alias1')])) )
    f=data/'inputs/alias1';f.mkdir(parents=True);(f/'case.json').write_text(json.dumps(dict(case_key='alias1')))
    run=tmp_path/'run';target=run/'cases/alias1';target.mkdir(parents=True)
    (run/'run_manifest.json').write_text(json.dumps(dict(dataset_path=str(data))))
    (target/'result.json').write_text(json.dumps(dict(case_key='alias1',status='SUCCEEDED',decision=dict(decision='NO_TRADE'),evaluation_scope='w3_decision_only',started_at='2026-11-16T12:00:00Z',finished_at='2026-11-16T12:01:00Z')))
    result=render_report(run,suite='timeliness',split='dev')
    assert result['correct']==1
    assert result['collections']['timeliness/dev']['correct']==1
    assert result['collections']['decision/validation']['cases']==0


def test_mixed_catalog_report_has_no_pooled_accuracy(tmp_path):
    from doxagent.w3_backtest.report import render_report
    data=tmp_path/'data';data.mkdir()
    time_row=fixture(data)
    decision_row={**time_row,'case_id':'native2','case_key':'alias2','suite':'decision','tags':[]}
    (data/'catalog.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [time_row,decision_row]))
    (data/'manifest.json').write_text(json.dumps(dict(schema_version='w3-backtest.dataset.v2',catalog_version='w3-backtest.catalog.v1',cases=[dict(case_key=r['case_key']) for r in [time_row,decision_row]])))
    run=tmp_path/'run';run.mkdir()
    (run/'run_manifest.json').write_text(json.dumps(dict(dataset_path=str(data))))
    for row in [time_row,decision_row]:
        key=row['case_key'];f=data/'inputs'/key;f.mkdir(parents=True)
        (f/'case.json').write_text(json.dumps(dict(case_key=key)))
        target=run/'cases'/key;target.mkdir(parents=True)
        (target/'result.json').write_text(json.dumps(dict(case_key=key,status='SUCCEEDED',decision=dict(decision='NO_TRADE'),evaluation_scope='w3_decision_only',started_at='2026-11-16T12:00:00Z',finished_at='2026-11-16T12:01:00Z')))
    result=render_report(run)
    assert result['accuracy'] is None and result['correct'] is None
    assert result['scoring_scope']=='SEPARATE_COLLECTIONS'
    assert result['collections']['decision/dev']['accuracy']==0
    assert result['collections']['timeliness/dev']['accuracy']==1
