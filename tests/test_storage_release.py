import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


spec=importlib.util.spec_from_file_location('storage_release',Path(__file__).parents[1]/'scripts/ops/storage_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


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
