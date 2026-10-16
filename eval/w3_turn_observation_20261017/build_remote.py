"""Build exactly committed MAIN, never the dirty development checkout."""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

repo = pathlib.Path('/home/ubuntu/doxagent')
root = pathlib.Path('/var/lib/doxagent/w3-turn-observation-20261017')
if '--reference-fix' in sys.argv:
    root = root/'reference-fix'
root.mkdir(parents=True, exist_ok=True, mode=0o700)
git = ['git', '-c', 'safe.directory='+str(repo)]
def out(args):
    return subprocess.check_output(args, cwd=repo, text=True).strip()
commit = out(git+['rev-parse', 'HEAD'])
assert commit == sys.argv[1], 'unexpected_main'
assert not out(git+['status','--porcelain','--untracked-files=no']), 'dirty_remote'
assert shutil.disk_usage('/').free > 6*1024**3, 'insufficient_build_space'
archive = subprocess.Popen(git+['archive','HEAD'], cwd=repo, stdout=subprocess.PIPE)
digest = hashlib.sha256()
for chunk in iter(lambda: archive.stdout.read(1048576), b''):
    digest.update(chunk)
assert archive.wait() == 0
image = 'doxagent-v2:main-'+commit[:8]
archive = subprocess.Popen(git+['archive','HEAD'], cwd=repo, stdout=subprocess.PIPE)
subprocess.run(['docker','build','-f','Dockerfile.v2','-t',image,
                '--build-arg','DOXAGENT_BUILD_COMMIT='+commit,
                '--build-arg','DOXAGENT_SOURCE_SHA256='+digest.hexdigest(),'-'],
               cwd=repo, stdin=archive.stdout, check=True)
assert archive.wait() == 0
receipt={'commit':commit,'image':image,'source_sha256':digest.hexdigest(),
         'image_id':out(['docker','image','inspect',image,'--format','{{.Id}}'])}
(root/'build.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
