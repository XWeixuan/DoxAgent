"""Content-free production version proof plus exact source files for local review."""
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root=Path('/tmp/w3-latency-review-20261111');root.mkdir(exist_ok=True)
common=['control_io.py','w3_timing.py','persistent_runtime_v2/w3_research_store.py']
roles={
 'v2-scheduler':common+['persistent_runtime_v2/'+p+'.py' for p in
                       ['coordinator','journal','repository','service','w3','w3_execution','w3_threads','worker_receipts']],
 'codex-worker':common+['codex_worker/'+p+'.py' for p in ['app','io_budget','sdk_runtime','__main__']]+['mcp/w3_research_server.py'],
}
state=json.loads(Path('/var/lib/doxagent/releases/state.json').read_bytes())
proof=dict(at=datetime.now(UTC).isoformat(),roles={})
for role,paths in roles.items():
    container='doxagent-v2-'+role+'-1'
    info=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
    accepted=state['components'][role]['current']
    assert info['Image']==accepted['image']
    row=dict(image=info['Image'],accepted_commit=accepted['commit'],started_at=info['State']['StartedAt'],files={})
    for relative in paths:
        path='src/doxagent/'+relative
        raw=subprocess.check_output(['docker','cp',container+':/app/'+path,'-'])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            content=archive.extractfile(archive.getmembers()[0]).read()
        target=root/role/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        row['files'][path]=hashlib.sha256(content).hexdigest()
    proof['roles'][role]=row
(root/'proof.json').write_text(json.dumps(proof,indent=2))
with tarfile.open(str(root)+'.tar.gz','w:gz') as archive:
    for path in root.rglob('*'):
        if path.is_file():archive.add(path,arcname=str(path.relative_to(root)))
Path(str(root)+'.tar.gz').chmod(0o644)
print(json.dumps(proof))
