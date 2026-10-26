"""Use existing production API container for an exclusive serving-only repair."""
import subprocess

projector='doxagent-v2-v2-projector-1'
subprocess.run(['docker','stop','-t','30',projector],check=True)
try:
    subprocess.run(['docker','exec','doxagent-v2-v2-api-1','python',
        '/tmp/repair-bus-projection.py','--read-db','/data/read/active.json',
        '--bus-db','/data/bus/bus.sqlite3','--apply'],check=True)
finally:
    subprocess.run(['docker','start',projector],check=True)
