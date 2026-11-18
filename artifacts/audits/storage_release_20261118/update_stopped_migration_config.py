"""Keep the dormant migration entry on compatible code, without running it."""
import importlib.util
import json,sys
from pathlib import Path

root=Path('/var/lib/doxagent/releases')
spec=importlib.util.spec_from_file_location('storage_release',root/'storage_release.py')
owner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
with owner.lock(root):
    manifest=json.loads((root/'storage-source-release-20261118/final.json').read_text())
    candidate=manifest['components']['v2-api']
    if len(sys.argv) > 1:
        matches=list(root.glob(sys.argv[1]+'*.build.json'))
        assert len(matches)==1,matches
        built=json.loads(matches[0].read_text())
        candidate={'image':built['images']['backend']['image'],'environment':{
            'DOXAGENT_BUILD_COMMIT':built['commit'],'DOXAGENT_SOURCE_SHA256':built['source_archive_sha256'],
            'DOXAGENT_PUBLISHED_CONTENT_REFS':'1','DOXAGENT_CONTENT_SINGLE_FILE':'1'}}
    path=root/'compose.current.json'
    configuration=json.loads(path.read_text())
    entry=configuration['services'].setdefault('v2-migrate',{})
    entry['image']=candidate['image']
    for key in ('DOXAGENT_BUILD_COMMIT','DOXAGENT_SOURCE_SHA256','DOXAGENT_PUBLISHED_CONTENT_REFS','DOXAGENT_CONTENT_SINGLE_FILE'):
        entry.setdefault('environment',{})[key]=candidate['environment'][key]
    owner.atomic(path,configuration)
    print(json.dumps({'v2-migrate':{'configured_image':entry['image'],'executed':False,'command':entry.get('command')}}))
