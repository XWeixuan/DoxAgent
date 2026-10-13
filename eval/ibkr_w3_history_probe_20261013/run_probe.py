"""Execute bounded read-only probes in the existing remote market-data container."""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
here = Path(__file__).resolve().parent
command = "sudo -n docker exec -i doxagent-v2-v2-message-bus-1 /app/.venv/bin/python -u -"
stem = sys.argv[1] if len(sys.argv) > 1 else "raw"
source = (here / "probe_remote.py").read_bytes()
if stem != "raw":
    source = source.split(b'emit({"kind":"environment"')[0] + (here / (stem + "_remote.py")).read_bytes()
with (here / (stem + ".jsonl")).open("wb") as log, (here / (stem + "_stderr.txt")).open("wb") as errors:
    process = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no", "doxagent-sg", command],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
    )
    process.stdin.write(source)
    process.stdin.close()
    for line in process.stdout:
        log.write(line)
        log.flush()
        try:
            value = json.loads(line)
            brief = {k: v for k, v in value.items() if k not in {"bars", "source", "raw_result"}}
            print(json.dumps(brief, ensure_ascii=False), flush=True)
        except ValueError:
            print(line.decode("utf-8", errors="replace"), end="", flush=True)
    code = process.wait()
if code:
    raise SystemExit(f"Remote probe exited {code}; see stderr.txt")
