"""Finite natural-poll observation; no new website requests or control actions."""
import subprocess
import sys
import time

since=sys.argv[1]
for index in range(21):
    p=subprocess.run(['python3','/tmp/barrons-acceptance.py',since],text=True,capture_output=True,timeout=20)
    if p.returncode:
        print('{"observation_error":true}',flush=True)
    else:
        print(p.stdout.strip(),flush=True)
    if index < 20:
        time.sleep(60)
