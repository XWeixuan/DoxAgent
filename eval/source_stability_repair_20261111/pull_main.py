import os,subprocess
from pathlib import Path
os.umask(0o077);repo=Path('/home/ubuntu/doxagent');path=repo/'changelog'
base=subprocess.check_output(['git','-C',str(repo),'show','HEAD:changelog']);raw=path.read_bytes()
if not raw.startswith(base):raise SystemExit('changelog is not append-only; preserve without auto merge')
suffix=raw[len(base):];backup=repo/'.git/source-stability-changelog-preserved-final-20261111';backup.write_bytes(raw)
subprocess.run(['git','-C',str(repo),'restore','--source=HEAD','--worktree','changelog'],check=True)
try:subprocess.run(['git','-C',str(repo),'pull','--ff-only','origin','main'],check=True)
finally:path.write_bytes(path.read_bytes()+suffix)
print('Preserved remote-only changelog suffix bytes:',len(suffix))
