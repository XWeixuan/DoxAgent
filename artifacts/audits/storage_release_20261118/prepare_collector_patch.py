import copy,json,os
from pathlib import Path
root=Path('/var/lib/doxagent/releases')
audit=root/'storage-source-release-20261118'
manifest=json.loads((audit/'final.json').read_text())
matches=list(root.glob('62bdc9b04*.build.json'));assert len(matches)==1
built=json.loads(matches[0].read_text())
candidate=copy.deepcopy(manifest['components']['v2-message-collector'])
candidate['image']=built['images']['backend']['image']
candidate['environment'].update(DOXAGENT_BUILD_COMMIT=built['commit'],DOXAGENT_SOURCE_SHA256=built['source_archive_sha256'])
patch=dict(compose_command=manifest['compose_command'],commit=built['commit'],components={'v2-message-collector':candidate})
path=audit/'collector-patch.json';path.write_text(json.dumps(patch));os.chmod(path,0o600)
# Keep the full acceptance inventory aligned with the narrowly changed role.
manifest['components']['v2-message-collector']=candidate
manifest['schema_patch']=built['commit']
path=audit/'final.json';path.write_text(json.dumps(manifest));os.chmod(path,0o600)
print(json.dumps({'collector_patch':built['commit']}))
