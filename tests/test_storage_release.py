import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


spec=importlib.util.spec_from_file_location('storage_release',Path(__file__).parents[1]/'scripts/ops/storage_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


def test_status_remains_readable_during_a_deploy_writer_lock(tmp_path,monkeypatch,capsys):
    def writer_lock(*args):raise AssertionError('read-only status acquired the deploy lock')
    monkeypatch.setattr(release,'lock',writer_lock)
    monkeypatch.setattr(release,'inspect_containers',lambda:[])
    monkeypatch.setattr(sys,'argv',['storage-release','storage-status','--root',str(tmp_path)])
    release.main()
    assert json.loads(capsys.readouterr().out)['owners']=={}


def image(identity,tag):
    return {'Id':identity,'RepoTags':[tag]}


def test_per_component_rollback_and_live_dynamic_templates():
    state={'components':{},'dependencies':{'backend':{'image':'deps'}}}
    release.promote(state,'api','a',commit='a')
    release.promote(state,'worker','a',commit='a')
    release.promote(state,'api','b',commit='b')
    release.promote(state,'api','c',commit='c')
    images=[image('agent','doxagent-repair:old'),image('deps','doxagent-deps:stable')]
    containers=[{'Image':'guardian','Config':{'Env':['TEMPLATE={"image":"doxagent-repair:old"}']}}]
    assert release.protected_images(state,images,containers)=={'a','b','c','deps','guardian','agent'}
    release.promote(state,'worker','d',commit='d')
    release.promote(state,'worker','e',commit='e')
    assert 'a' not in release.protected_images(state,images,containers)


def test_reconcile_retries_after_acceptance_without_retiring_shared_or_foreign_resources(tmp_path,monkeypatch):
    state={'format':'doxagent.component-releases.1','components':{}}
    release.promote(state,'api','b',commit='b')
    release.promote(state,'api','c',commit='c')
    release.atomic(tmp_path/'state.json',state)
    images=[image('a','doxagent-v2:old'),image('b','doxagent-v2:previous'),
            image('c','doxagent-v2:current'),image('foreign','unrelated:old')]
    monkeypatch.setattr(release,'inspect_images',lambda:images)
    monkeypatch.setattr(release,'inspect_containers',lambda:[{'Image':'c','Config':{'Env':[]}}])
    monkeypatch.setattr(release,'run',lambda args,**kwargs:'')
    monkeypatch.setattr(release.shutil,'disk_usage',lambda _:SimpleNamespace(free=30*1024**3))
    calls=[]
    def execute(command,**kwargs):
        calls.append(command)
        if command[:3]==['docker','image','rm'] and sum(c[:3]==command[:3] for c in calls)==1:
            raise subprocess.CalledProcessError(1,command,stderr='temporary engine error')
    monkeypatch.setattr(release.subprocess,'run',execute)
    assert release.reconcile(tmp_path,state)['errors']
    assert state['cleanup_pending']
    assert not release.reconcile(tmp_path,state)['errors']
    assert not state['cleanup_pending']
    removed=[c[3] for c in calls if c[:3]==['docker','image','rm']]
    assert removed==['doxagent-v2:old','doxagent-v2:old']


def test_partial_deploy_keeps_accepted_component_and_rolls_back_failed_component(tmp_path,monkeypatch):
    state={'format':'doxagent.component-releases.1','components':{}}
    for component in ('api','worker'):release.promote(state,component,'a',commit='a')
    manifest={'compose_command':['docker','compose','-f','base.yml'],'commit':'new',
              'components':{'api':{'image':'b'},'worker':{'image':'c'}}}
    up=[]
    def execute(command,**kwargs):
        component=command[-1];up.append(component)
        if component=='worker' and up.count('worker')==1:
            raise subprocess.CalledProcessError(1,command)
    def output(command,**kwargs):
        if command[:3]==['docker','image','inspect']:return command[-1]
        if command[-3:-1]==['ps','-q']:return command[-1]+'-container'
        if command[:2]==['docker','inspect']:
            return json.dumps([{'Image':'b','State':{'Running':True,'Health':{'Status':'healthy'}}}])
        raise AssertionError(command)
    monkeypatch.setattr(release.subprocess,'run',execute)
    monkeypatch.setattr(release,'run',output)
    monkeypatch.setattr(release,'inspect_images',lambda:[])
    monkeypatch.setattr(release,'inspect_containers',lambda:[])
    with pytest.raises(subprocess.CalledProcessError):release.deploy(tmp_path,state,manifest)
    saved=json.loads((tmp_path/'state.json').read_text())
    assert saved['components']['api']['current']['image']=='b'
    assert saved['components']['api']['previous']['image']=='a'
    assert saved['components']['worker']['current']['image']=='a'
    assert json.loads((tmp_path/'compose.current.json').read_text())['services']['worker']['image']=='a'
    assert up==['api','worker','worker']
    assert saved['cleanup_pending']


def test_retirement_requires_bootstrapped_rollback_inventory(tmp_path):
    with pytest.raises(ValueError,match='bootstrap'):release.reconcile(tmp_path,{})


def test_companion_exits_with_its_guardian_and_live_reference_survives_promotion():
    state={'components':{'guardian':{'current':{'image':'g2'},'previous':{'image':'g1'}}},
           'holds':[{'image':'a1','component':'guardian','release_image':'g1'}]}
    images=[image('a2','doxagent-agent:current')]
    containers=[{'Image':'g2','Config':{'Labels':{'com.docker.compose.service':'guardian'},'Env':['AGENT=doxagent-agent:current']}}]
    release.record_live_references(state,images,containers)
    release.promote(state,'guardian','g3',commit='g3')
    protected=release.protected_images(state,images,[])
    assert 'a2' in protected
    assert 'a1' not in protected


def test_compose_interpolation_metadata_does_not_create_permanent_runtime_pins():
    images=[image('old','doxagent-v2:old'),image('agent','doxagent-agent:current')]
    container={'Image':'current','Config':{'Env':['DOXAGENT_V2_IMAGE=doxagent-v2:old','DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE=doxagent-agent:current']}}
    assert release.protected_images({},images,[container])=={'current','agent'}


def test_actual_unfinished_repair_image_refs_exit_after_incident_closes(tmp_path):
    import sqlite3
    path=tmp_path/'control.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE initialization_repair_incidents(id,status,payload);CREATE TABLE initialization_repair_rounds(incident_id,payload);')
        db.execute('INSERT INTO initialization_repair_incidents VALUES(?,?,?)',('repair','HUMAN_REQUIRED',json.dumps(dict(source_image_id='source'))))
        db.execute('INSERT INTO initialization_repair_rounds VALUES(?,?)',('repair',json.dumps(dict(image_id='candidate'))))
    container={'Config':{'Labels':{'com.docker.compose.service':'initialization-guardian'},'Cmd':['guardian','--initialization-db','/data/control.db']},'Mounts':[{'Source':str(tmp_path),'Destination':'/data'}]}
    state={};release.record_repair_references(state,[container]);assert state['repair_images']==['candidate','source']
    with sqlite3.connect(path) as db:db.execute("UPDATE initialization_repair_incidents SET status='SUCCEEDED'")
    release.record_repair_references(state,[container]);assert not state['repair_images']


def test_one_command_release_builds_and_retargets_companion_without_mutating_previous(tmp_path,monkeypatch):
    state={'compose_base_command':['docker','compose','-f','base.yml'],'components':{'initialization-guardian':{'current':{'image':'old','compose':{'environment':{'DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE':'old-agent'}}}}}}
    release.atomic(tmp_path/'state.json',state)
    def build(repo,commit,root,kinds):
        assert set(kinds)=={'guardian','repair-agent'}
        return {'commit':'new','images':{'guardian':{'image':'new'},'repair-agent':{'tag':'new-agent'}}}
    def deploy(root,s,manifest):
        assert s['components']['initialization-guardian']['current']['compose']['environment']['DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE']=='old-agent'
        assert manifest['components']['initialization-guardian']['environment']['DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE']=='new-agent'
        return {'accepted':True}
    monkeypatch.setattr(release,'build',build);monkeypatch.setattr(release,'deploy',deploy)
    assert release.release_components(tmp_path,state,tmp_path,'new',['initialization-guardian'])=={'accepted':True}


def test_collector_registration_uses_formal_migration_and_stops_old_acquisition(tmp_path,monkeypatch):
    state={'compose_base_command':['docker','compose','-f','base.yml'],'components':{
        'v2-message-bus':{'current':{'image':'old','compose':{}}},
        'v2-content-enrichment':{'current':{'image':'old','compose':{}}}}}
    release.atomic(tmp_path/'state.json',state)
    calls=[]
    monkeypatch.setattr(release,'build',lambda *a:{'commit':'new','images':{'backend':{'image':'new-backend'}}})
    monkeypatch.setattr(release.subprocess,'run',lambda c,**kw:calls.append(c))
    monkeypatch.setattr(release,'run',lambda c,**kw:'0' if c[:2]==['docker','wait'] else 'formal-migration-id')
    def deployed(root,current,manifest):
        assert manifest['migration']['exit_code']=='0'
        assert manifest['components']['v2-message-collector']['image']=='new-backend'
        assert manifest['components']['v2-message-bus']['environment']['DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED']=='true'
        return True
    monkeypatch.setattr(release,'deploy',deployed)
    assert release.release_components(tmp_path,state,tmp_path,'new',['v2-message-bus','v2-content-enrichment','v2-message-collector'])
    assert any(c[-3:]==['stop','v2-message-bus','v2-content-enrichment'] for c in calls)
    assert any(c[-1]=='v2-migrate' and '--force-recreate' in c for c in calls)
    assert not any('run' in c for c in calls)
