"""A Worker replacement cannot race a durable new model submission."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest


def helper():
    path=Path(__file__).resolve().parents[1]/'scripts/ops/storage_lifecycle_release_20261111.py'
    spec=importlib.util.spec_from_file_location('storage_release_window',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('busy',['job','effect',None])
def test_worker_stop_is_idle_and_submission_fenced(tmp_path,busy):
    database=tmp_path/'jobs.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT)')
        if busy=='job':db.execute("INSERT INTO jobs VALUES('accepted','running')")
    stopped=[];guards=[]
    def guard(path):
        guards.append(path)
        if busy=='effect':raise ValueError('live result consumer')
    def stop(identity):
        with sqlite3.connect(database,timeout=0) as submitter:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                submitter.execute("INSERT INTO jobs VALUES('new','queued')")
        stopped.append(identity)
    action=lambda:helper().stop_idle_worker(database,'runtime.sqlite3','immutable-worker-id',guard,stop=stop)
    if busy:
        with pytest.raises(ValueError):action()
    else:action()
    assert stopped==([] if busy else ['immutable-worker-id'])
    assert guards==([] if busy=='job' else ['runtime.sqlite3'])
    # Failure and success both release the short admission fence.
    with sqlite3.connect(database,timeout=0) as db:
        db.execute("INSERT INTO jobs VALUES('after','queued')")
