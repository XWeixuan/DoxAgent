import json
from datetime import UTC, datetime

from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3
from doxagent.workflows.codex_document3.state_v21 import StateV21
from tests.test_runtime_policy_direction_usage import policy


def test_formal_staged_and_history_share_objects_without_rebuilding_hot_projection(tmp_path):
    (tmp_path/'.d3-shared-policy-ready').touch()
    repo = SQLiteDocument3PolicyRepository(tmp_path/'research.db')
    now = datetime.now(UTC)
    p = policy().model_copy(update={'transmission': 'unique business channel ' * 1500})
    first = PolicySetV3(ticker='MU', policy_set_version=1, as_of=now, published_at=now,
                       publication_state='COMPLETE', policies=[p])
    repo.publish(first, expected_base_version=None)
    state = StateV21(repo.path)
    state.save_staged_v3('first', first, {'accepted_rearm': [], 'projection': {'redundant': True}})
    state.save_staged_v3('first', first, {'accepted_rearm': [], 'projection': {'redundant': True}})
    second = first.model_copy(update={'policy_set_version': 2})
    repo.publish(second, expected_base_version=1)
    assert repo.get_current('MU') == second
    assert repo.get_version('MU', 1) == first
    assert state.get_staged_v3('MU', 1) == first
    assert 'projection' not in state.get_staged_by_run('first')[1]
    assert repo.get_projection('MU', 1).policy_set_version == 1
    assert repo.get_current_projection('MU').policy_set_version == 2
    with repo._connect() as db:
        rows = db.execute('SELECT policy_set_json,runtime_projection_json FROM codex_document3_policy_sets ORDER BY policy_set_version').fetchall()
        assert len(rows[0][0]) < 2048 and rows[0][1] == '{}'
        hashes = db.execute("SELECT DISTINCT artifact_id FROM artifact_ref WHERE owner_type IN ('codex_document3_policy_sets','codex_document3_v21_staged_sets')").fetchall()
        assert len(hashes) == 1
        assert json.loads(rows[0][0])['policies'] == json.loads(rows[1][0])['policies']

    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.codex_runtime.schema import ArtifactRef, PublishedDocument
    from doxagent.workflows.codex_document3.semantic_exports import locator
    from doxagent.v2_read.artifacts import PublishedArtifacts
    runtime=SQLiteCodexRuntimeRepository(repo.path)
    artifact=ArtifactRef(run_id='first',artifact_id='policy-body',node='d3_publish',attempt_id='publish',
                         workflow_version='codex_document3_v1',research_lane='document3',
                         kind='bundle',relative_path='artifacts/document3.json',published=True,
                         sha256='0'*64,size_bytes=9876,content_type='application/json',created_at=now)
    runtime.save_artifact(artifact)
    runtime.save_published_document(PublishedDocument(run_id='first',artifact_id='policy-body',
        artifact_kind='bundle',sha256=artifact.sha256,size_bytes=artifact.size_bytes,
        content_type='application/json',storage_path=locator(repo.path.name,'first','document3.json')))
    restored=runtime.get_published_document('first','policy-body')
    assert PolicySetV3.model_validate_json(restored.content_text)==first
    assert restored.sha256 != artifact.sha256
    descriptor,body=PublishedArtifacts(repo.path).body('first','policy-body')
    assert PolicySetV3.model_validate_json(body)==first
    assert descriptor['sha256']==restored.sha256
    with runtime._connect() as db:
        raw=db.execute("SELECT payload_json FROM codex_runtime_records WHERE record_type='published_documents'").fetchone()[0]
        assert len(raw)<1024 and 'unique business channel' not in raw


def test_condition_highwater_survives_bounded_draft_exit(tmp_path):
    state=StateV21(tmp_path/'research.db')
    for n in range(5):
        state.save_draft('old',str(n),{'policy':{'policy_id':'P1'},'max_condition':n+10})
    assert not state.backfill_condition_highwater(limit=2)
    state.save_draft('new','later',{'policy':{'policy_id':'P1'},'max_condition':99})
    while not state.backfill_condition_highwater(limit=2):pass
    with state.connect() as db:db.execute('DELETE FROM codex_document3_v21_draft_ids')
    assert state.maximum_condition('P1')==99


def test_history_loses_unreferenced_recovery_without_losing_current_or_case_pins(tmp_path):
    import pytest
    from datetime import timedelta
    from doxagent.workflows.codex_document3.history_exit import collect,register_legacy
    (tmp_path/'.d3-shared-policy-ready').touch()
    repo=SQLiteDocument3PolicyRepository(tmp_path/'research.db');state=StateV21(repo.path)
    now=datetime.now(UTC);old=now-timedelta(days=30)
    for version in range(1,7):
        item=PolicySetV3(ticker='MU',policy_set_version=version,as_of=old,published_at=old,publication_state='COMPLETE',policies=[policy()])
        repo.publish(item,expected_base_version=version-1 or None)
        state.save_staged_v3(str(version),item,{})
    while not register_legacy(state):pass
    result=collect(state,'MU',keep_versions={2},now=now,is_live=lambda run:False)
    assert result['retired_version']==1
    with pytest.raises(ValueError,match='HISTORY_RETIRED'):repo.get_version('MU',1)
    with pytest.raises(ValueError,match='HISTORY_RETIRED'):state.get_staged_v3('MU',1)
    assert repo.get_version('MU',2).policy_set_version==2
    assert repo.get_current('MU').policy_set_version==6
    assert collect(state,'MU',keep_versions={2},now=now,is_live=lambda run:False)['retired_version']==3
    assert collect(state,'MU',keep_versions={2},now=now,is_live=lambda run:False)['idle']
