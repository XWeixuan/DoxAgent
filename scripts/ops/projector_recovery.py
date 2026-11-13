"""Host-only recovery of a desired-running Projector after ENOSPC startup failure.
Pause before maintenance: touch /var/lib/doxagent/projector-recovery/paused
Resume: remove that marker. Never starts any other role or restarts a running one.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path('/var/lib/doxagent/projector-recovery')
CONTAINER = 'doxagent-v2-v2-projector-1'


def eligible(info, current_image, *, enabled, paused, free, last_attempt, now):
    return (enabled and not paused and info['Image'] == current_image
            and info['State']['Status'] == 'exited'
            and 'no space left on device' in info['State'].get('Error', '').lower()
            and free >= 2 * 1024**3 and now - last_attempt >= 300)


def main():
    import fcntl
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT/'lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if not (ROOT/'enabled').exists() or (ROOT/'paused').exists():
            return
        state = json.loads(Path('/var/lib/doxagent/releases/state.json').read_bytes())
        info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER], timeout=10))[0]
        last = float((ROOT/'last-attempt').read_text()) if (ROOT/'last-attempt').exists() else 0
        now = time.time()
        if eligible(info, state['components']['v2-projector']['current']['image'],
                    enabled=True, paused=False, free=shutil.disk_usage('/').free,
                    last_attempt=last, now=now):
            (ROOT/'last-attempt').write_text(str(now))
            subprocess.run(['docker', 'start', CONTAINER], check=True, timeout=30)
            print('Started desired Projector after recovered ENOSPC startup failure', flush=True)
        elif not info['State']['Running']:
            print('Projector stopped; recovery conditions unmet: '+info['State']['Status'], flush=True)


if __name__ == '__main__':
    main()
