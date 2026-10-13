"""Record final local hashes and compare the deployed P4 source hashes."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[2]
stage=Path(__file__).resolve().parent
manifest=json.loads((stage/'code-manifest.json').read_text(encoding='utf-8'))
additional=['src/doxagent/production_v2.py','src/doxagent/v2_read/backup_retention.py',
    'src/doxagent/v2_read/policy_hits.py','src/doxagent/initialization_repair/containers.py',
    'src/doxagent/source_maintenance/containers.py','scripts/ops/storage_report.py',
    'deploy/Dockerfile.initialization-guardian','deploy/docker-compose.initialization-repair.yml',
    'deploy/docker-compose.source-maintenance.yml']
for path in list(manifest)+additional:
    manifest[path]=hashlib.sha256((root/path).read_bytes()).hexdigest()
(stage/'code-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
snapshot=json.loads((stage/'final-snapshot.json').read_text(encoding='utf-8'))
for values in snapshot['p4_code_sha256'].values():
    for absolute,sha in values.items():
        assert manifest[absolute.removeprefix('/app/')]==sha,absolute
assert all(c['running'] for c in snapshot['containers'] if 'migrate' not in c['name'])
assert all(c['health'] in (None,'healthy') for c in snapshot['containers'])
report={'p4_deployed_hashes_verified':True,'running_container_health_verified':True,
        'root_free_gib':snapshot['disk']['free']/1024**3,
        'tests':{'p1_p3_passed':31,'p4_passed':37,'p4_existing_clock_fixture_deselected':1,
                 'policy_lookup_passed':7},
        'long_term_growth_acceptance':'not_completed','projection_backlog':'still_present'}
(stage/'final-acceptance.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
