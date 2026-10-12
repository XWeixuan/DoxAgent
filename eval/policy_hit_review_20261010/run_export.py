"""Run the read-only SSH export over byte streams, preserving JSONL lines."""
import subprocess
import sys
import json
from pathlib import Path

here = Path(__file__).resolve().parent
source = (here / 'export_remote.py').read_bytes()
remote = subprocess.run(
    ['ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
     'doxagent-sg', 'sudo -n python3 -'],
    input=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    timeout=180, check=False,
)
if remote.returncode:
    sys.stderr.buffer.write(remote.stderr)
    raise SystemExit(remote.returncode)
for number, line in enumerate(remote.stdout.splitlines(), 1):
    try:
        json.loads(line)
    except json.JSONDecodeError as exc:
        print(f'remote JSONL invalid at line {number}, byte length {len(line)}, '
              f'column {exc.colno}: {line[max(0, exc.pos-100):exc.pos+100]!r}',
              file=sys.stderr)
        raise SystemExit(1)
split = subprocess.run([sys.executable, str(here / 'split_local.py')],
                       input=remote.stdout, check=False)
raise SystemExit(split.returncode)
