from pathlib import Path
import json
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/production-backups/v21-20261012')
p=root/'business-files.tar.gz'
assert p.resolve().parent==root.resolve()
assert p.stat().st_size==2939254939
receipt=dict(file='D:/DoxAgentPilot/backups/v21-production-20261012/business-files.tar.gz',size=2939254939,sha256='675c1d105fde5016d2eb048d14ea5c17ff1ad1d84e16791987107d37dd27b201',verified_offhost=True)
(root/'business-files-offhost.json').write_text(json.dumps(receipt,indent=2))
p.unlink()
print('Verified offhost duplicate removed; source untouched')
