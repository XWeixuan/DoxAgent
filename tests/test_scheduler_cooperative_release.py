import importlib.util
import json
import sqlite3
import subprocess
from concurrent.futures import Future
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from doxagent.runtime_scheduler.v2 import V2Scheduler


def scheduler():
    c = SimpleNamespace(_dispatch_lock=Lock(), _tasks_lock=Lock(), _futures={},
                        _public_futures={}, _supplement_futures={}, _effect_future=None,
                        _delivery_future=None, _w3_maintenance_future=None, _storage_future=None,
                        _claims_inflight=0, _closed=False)
    runtime = SimpleNamespace(coordinator=c, _effect_dispatch_lock=Lock(), _w3_threads_lock=Lock(),
                              _active_effect_ids=set(), _phase_waiters={}, _w3_threads=set(), _draining=False)
    s = object.__new__(V2Scheduler)
    s.runtime_v2_service = runtime
    return s, runtime, c


@pytest.mark.parametrize('busy', ['claim', 'case', 'paid_result', 'public', 'mutex'])
def test_release_refuses_raced_work_without_stopping_ingress(busy):
    s, r, c = scheduler()
    stop = Event()
    if busy == 'claim':
        c._claims_inflight = 1
    elif busy == 'case':
        c._futures['case'] = Future()
    elif busy == 'paid_result':
        r._active_effect_ids.add('finished-job-owner')
    elif busy == 'public':
        c._public_futures['MU'] = Future()
    else:
        c._dispatch_lock.acquire()
    try:
        assert not s.stop_if_idle(stop)
        assert not stop.is_set() and not c._closed and not r._draining
    finally:
        if busy == 'mutex':
            c._dispatch_lock.release()


def test_release_commits_local_stop_under_existing_owner_locks():
    s, r, c = scheduler()
    stop = Event()
    assert s.stop_if_idle(stop)
    assert stop.is_set() and c._closed and r._draining
    assert c._tasks_lock.acquire(False)
    c._tasks_lock.release()


def helper():
    p = Path(__file__).parents[1] / 'scripts/ops/storage_release.py'
    spec = importlib.util.spec_from_file_location('scheduler_release', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize('busy', [True, False])
def test_host_cutover_never_forces_a_busy_owner(busy):
    now, calls, running = [0], [], [True]
    def call(args, **kw):
        calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            return json.dumps([dict(Id='original', State=dict(Running=running[0]), HostConfig=dict(RestartPolicy=dict(Name='unless-stopped')))])
        if '--signal=USR1' in args and not busy:
            running[0] = False
        return ''
    def sleep(seconds):
        now[0] += seconds
    if busy:
        with pytest.raises(RuntimeError, match='keep serving'):
            helper().stop_idle_scheduler(seconds=2, call=call, clock=lambda: now[0], sleep=sleep)
    else:
        assert helper().stop_idle_scheduler(seconds=2, call=call, clock=lambda: now[0], sleep=sleep)['container'] == 'original'
    assert not any('stop' in a or 'SIGKILL' in a or '--signal=TERM' in a for a in calls)
    assert ['docker','update','--restart=no','original'] in calls
    assert (['docker','update','--restart=unless-stopped','original'] in calls) == busy


def test_unsupported_old_image_is_never_sent_default_terminating_usr1():
    calls = []
    def call(args, **kw):
        calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            return json.dumps([dict(Id='old', State=dict(Running=True))])
        raise subprocess.CalledProcessError(1, args)
    with pytest.raises(subprocess.CalledProcessError):
        helper().stop_idle_scheduler(call=call)
    assert not any('kill' in a for a in calls)


@pytest.mark.parametrize('race', [False, True])
def test_worker_race_aborts_before_stop_and_restores_scheduler(tmp_path, race):
    jobs = tmp_path / 'workspaces/worker-jobs.sqlite3'
    jobs.parent.mkdir()
    with sqlite3.connect(jobs) as db:
        db.execute('CREATE TABLE jobs(status TEXT)')
        if not race:
            db.execute("INSERT INTO jobs VALUES('running')")
    calls = []
    def call(args, **kw):
        calls.append(args)
        if args[:2] == ['docker','inspect']:
            info = dict(Id='worker', State=dict(Running=True), Mounts=[dict(Source=str(tmp_path),Destination='/data')])
            return json.dumps([info])
        return ''
    def idle(**kw):
        with sqlite3.connect(jobs) as db:
            db.execute("INSERT INTO jobs VALUES('queued')")
        return dict(container='scheduler',restart_policy='unless-stopped')
    with pytest.raises((ValueError, RuntimeError), match='accepted'):
        helper().stop_worker_for_release(call=call, stop_scheduler=idle)
    assert (['docker','start','scheduler'] in calls) == race
    assert not any('stop' in a for a in calls)


@pytest.mark.parametrize('fail_bookkeeping', [False, True])
def test_formal_owner_cuts_runtime_last_and_restores_on_bookkeeping_failure(tmp_path, monkeypatch, fail_bookkeeping):
    script = Path(__file__).parents[1] / 'scripts/ops/storage_release.py'
    spec = importlib.util.spec_from_file_location('formal_owner', script)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    roles = ['v2-scheduler','codex-worker','v2-api']
    state = dict(components={r: {'current': {'image': 'old-'+r}} for r in roles})
    manifest = dict(commit='new', compose_command=['docker','compose'],
                    components={r: {'image':'new-'+r} for r in roles})
    calls = []
    def execute(args, **kw):
        if args[:3] == ['docker','image','inspect']:
            return args[-1]
        if args[:2] == ['docker','inspect']:
            return json.dumps([dict(Image='new-'+args[-1], State=dict(Running=True,Health=dict(Status='healthy')))])
        return args[-1]
    monkeypatch.setattr(owner, 'run', execute)
    monkeypatch.setattr(owner.subprocess, 'check_output', lambda args, **kw: calls.append(args) or '')
    monkeypatch.setattr(owner.subprocess, 'run', lambda args, **kw: calls.append(args))
    monkeypatch.setattr(owner, 'inspect_images', lambda: [])
    monkeypatch.setattr(owner, 'inspect_containers', lambda: [])
    monkeypatch.setattr(owner, 'reconcile', lambda *a: {'ok':True})
    monkeypatch.setattr(owner, 'resume_scheduler', lambda receipt: calls.extend([['docker','start',receipt['container']], ['docker','update','--restart='+receipt['restart_policy'],receipt['container']]]))
    monkeypatch.setattr(owner, 'stop_worker_for_release', lambda: {'container':'scheduler','restart_policy':'unless-stopped'})
    monkeypatch.setattr(owner, 'stop_idle_scheduler', lambda: {'already_stopped':True})
    promote = owner.promote
    def promoted(state, component, *a, **kw):
        if fail_bookkeeping and component == 'codex-worker':
            raise OSError('bookkeeping unavailable')
        return promote(state, component, *a, **kw)
    monkeypatch.setattr(owner, 'promote', promoted)
    if fail_bookkeeping:
        with pytest.raises(OSError, match='bookkeeping'):
            owner.deploy(tmp_path, state, manifest)
        assert ['docker','start','scheduler'] in calls
        assert ['docker','update','--restart=unless-stopped','scheduler'] in calls
    else:
        assert owner.deploy(tmp_path, state, manifest) == {'ok':True}
        assert [a[-1] for a in calls if 'up' in a] == ['v2-api','codex-worker','v2-scheduler']


def test_formal_owner_is_standalone_when_copied_to_audit_directory(tmp_path):
    import shutil
    source = Path(__file__).parents[1] / 'scripts/ops/storage_release.py'
    target = tmp_path / source.name
    shutil.copyfile(source, target)
    spec = importlib.util.spec_from_file_location('isolated_owner', target)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    assert callable(owner.stop_idle_scheduler)
    assert callable(owner.stop_worker_for_release)
    assert callable(owner.stop_idle_worker)
    calls = []
    owner.resume_scheduler({'container':'original','restart_policy':'on-failure:3'},
                           call=lambda args, **kw: calls.append(args))
    assert calls == [['docker','start','original'], ['docker','update','--restart=on-failure:3','original']]
