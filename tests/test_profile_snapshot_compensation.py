import json
import os
from types import SimpleNamespace

from doxagent.site_strategy.profile_storage import ProfileSnapshotManager
from doxagent.site_strategy.runtime import OwnerFileLock


def test_missed_prune_retries_cold_profile_but_defers_current_owner(tmp_path):
    profile=SimpleNamespace(profile_id='cold',directory_key='cold-dir',maintenance_session_id=None)
    manager=ProfileSnapshotManager(tmp_path/'profiles',SimpleNamespace(list_profiles=lambda:[profile]))
    for index in range(3):
        archive=manager.snapshot_root/f'cold-{index}.tar.gz';archive.write_bytes(b'snapshot')
        manifest=archive.with_suffix('.json');manifest.write_text(json.dumps(dict(archive=archive.name)))
        os.utime(manifest,(1000+index,1000+index))
    lock=OwnerFileLock(manager.profile_root/'.profile-locks'/'cold-dir.lock');lock.acquire()
    try:manager.maintain()
    finally:lock.release()
    assert len(list(manager.snapshot_root.glob('*.tar.gz')))==3
    manager.maintain();manager.maintain()
    assert len(list(manager.snapshot_root.glob('*.tar.gz')))==2
    assert not (manager.snapshot_root/'cold-0.tar.gz').exists()
