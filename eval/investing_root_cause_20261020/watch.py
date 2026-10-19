"""Finite 20-minute natural-poll observation; no website requests or state changes."""
import subprocess
import sys
import time

since = sys.argv[1]
samples = int(sys.argv[2]) if len(sys.argv) > 2 else 21
assert 1 <= samples <= 61
for index in range(samples):
    p = subprocess.run(['python3', '/tmp/investing-acceptance.py', since],
                       text=True, capture_output=True, timeout=25)
    print(p.stdout.strip() if p.returncode == 0 else '{"observation_error":true}', flush=True)
    if index < samples - 1:
        time.sleep(60)
