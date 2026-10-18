"""Site-only immutable repair and committed desktop UI; keep native Chrome scenes."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

repo = Path('/home/ubuntu/doxagent')
spec = importlib.util.spec_from_file_location('site_repair_release', repo / 'eval/site_maintenance_recovery_20261019/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
release.ROOT = Path('/var/lib/doxagent/site-maintenance-sessions-20261019')
sys.argv = [sys.argv[0], '--site-only']
release.main()
target = Path('/usr/local/bin/doxagent-site-login-ui')
backup = release.ROOT / 'desktop-ui.before'
if not backup.exists():
    backup.write_bytes(target.read_bytes())
    backup.chmod(0o600)
source = subprocess.check_output(['git', 'show', 'HEAD:deploy/site-login-ui.py'], cwd=repo)
compile(source, str(target), 'exec')
target.write_bytes(source)
os.chown(target, 0, 0)
target.chmod(0o755)
receipt = {'sha256': hashlib.sha256(source).hexdigest(), 'root_owned': target.stat().st_uid == 0,
           'mode': oct(target.stat().st_mode & 0o777)}
(release.ROOT / 'desktop-ui.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps({'desktop_ui': receipt}))
