"""Run a checked-in read-only collector through an existing production container."""
import argparse
import subprocess
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('script')
parser.add_argument('output')
parser.add_argument('--container',default='')
parser.add_argument('args',nargs='*')
options=parser.parse_intermixed_args()
remote=['sudo','-n']
if options.container:
    remote += ['docker','exec','-i',options.container,'python','-']
else:remote += ['python3','-']
remote += options.args
import shlex
result=subprocess.run(['ssh','-o','BatchMode=yes','doxagent-sg',shlex.join(remote)],
    input=Path(options.script).read_bytes(),capture_output=True,timeout=240)
target=Path(options.output);target.parent.mkdir(parents=True,exist_ok=True)
target.write_bytes(result.stdout)
target.with_suffix(target.suffix+'.log').write_bytes(result.stderr)
print(dict(exit=result.returncode,bytes=len(result.stdout),stderr=result.stderr.decode(errors='replace')[-2500:]))
raise SystemExit(result.returncode)
