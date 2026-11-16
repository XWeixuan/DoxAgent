"""Preserve the remote changelog tail while fast-forwarding reviewed main."""
from pathlib import Path
import subprocess
import json

repo=Path('/home/ubuntu/doxagent')
backup=Path('/home/ubuntu/.cache/doxagent-w3-release-20261116')
backup.mkdir(mode=0o700,parents=True,exist_ok=True)
def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
dirty=git('diff','--name-only').decode().splitlines()
assert set(dirty) <= {'changelog'},dirty
head=git('rev-parse','HEAD').decode().strip()
ours=(repo/'changelog').read_bytes()
(backup/'original').write_bytes(ours)
(backup/'base').write_bytes(git('show',head+':changelog'))
if dirty:subprocess.run(['git','-C',str(repo),'restore','--worktree','--','changelog'],check=True)
subprocess.run(['git','-C',str(repo),'pull','--ff-only'],check=True,stdout=subprocess.PIPE)
(backup/'theirs').write_bytes((repo/'changelog').read_bytes())
result=subprocess.run(['git','merge-file','-p',str(backup/'original'),str(backup/'base'),str(backup/'theirs')],stdout=subprocess.PIPE)
assert result.returncode in (0,1)
merged=result.stdout
if result.returncode:
    merged=b''.join(line for line in merged.splitlines(keepends=True)
                    if not line.startswith((b'<<<<<<< ',b'>>>>>>> ')) and line.strip()!=b'=======')
    assert set(ours.splitlines()) <= set(merged.splitlines())
(repo/'changelog').write_bytes(merged)
print(json.dumps(dict(before=head,after=git('rev-parse','HEAD').decode().strip(),remote_changelog_preserved=True)))
