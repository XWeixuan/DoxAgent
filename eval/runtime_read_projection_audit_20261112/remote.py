"""Execute diagnostic input over SSH; preserve stdout/stderr locally."""
import argparse
import subprocess
from pathlib import Path

base = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("script")
parser.add_argument("output")
parser.add_argument("--host", action="store_true")
parser.add_argument("--timeout", type=int, default=120)
args = parser.parse_args()
remote = "sudo -n python3 -" if args.host else "sudo -n docker exec -i doxagent-v2-v2-projector-1 python -"
result = subprocess.run(
    ["ssh", "-C", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "doxagent-sg", remote],
    input=(base / args.script).read_bytes(), capture_output=True, timeout=args.timeout,
)
(base / args.output).write_bytes(result.stdout)
(base / (args.output + ".stderr")).write_bytes(result.stderr)
print(f"exit={result.returncode}; bytes={len(result.stdout)}; output={base / args.output}")
print(result.stderr.decode(errors="replace")[-1500:])
raise SystemExit(result.returncode)
