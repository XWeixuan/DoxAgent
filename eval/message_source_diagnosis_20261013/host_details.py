"""Read-only host log/context; deliberately excludes token/session contents."""
import json
import subprocess

def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=40).stdout

logs = run(['docker','logs','--since','12h','doxagent-v2-v2-site-access-1'])
# docker logs writes ASGI exceptions on stderr too.
logs_result = subprocess.run(['docker','logs','--since','12h','doxagent-v2-v2-site-access-1'],capture_output=True,text=True,timeout=40)
lines = (logs_result.stdout + '\n' + logs_result.stderr).splitlines()
blocks = []
for i,line in enumerate(lines):
    if 'Traceback (most recent call last)' in line:
        end = next((j for j in range(i+1, min(i+95,len(lines))) if lines[j].startswith('INFO:') or ('Got connection' in lines[j])), min(i+95,len(lines)))
        blocks.append('\n'.join(lines[i:end]))
out = {'exceptions':blocks[-4:]}
out['runtime_files'] = run(['docker','exec','doxagent-v2-v2-site-access-1','sh','-c','ls -l /run/doxagent-chrome; cat /run/doxagent-chrome/vnc-target'])
out['pressure'] = run(['sh','-c','cat /proc/pressure/io /proc/pressure/memory; vmstat 1 3'])
out['supervisor_cgroup'] = run(['docker','exec','doxagent-v2-v2-chrome-supervisor-1','sh','-c','cat /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max'])
print(json.dumps(out))
