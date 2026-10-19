"""Ten-second sampling of the actual single Projector writer."""
import json, subprocess
name='doxagent-v2-v2-projector-1'
state=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
top=subprocess.check_output(['docker','top',name,'-eo','pid,args'],text=True).splitlines()
pid=next(line.split()[0] for line in top if 'python -m doxagent.v2_read' in line and 'docker-init' not in line)
p=subprocess.run(['docker','run','--rm','--pid=host','--cap-add=SYS_PTRACE','--security-opt','seccomp=unconfined',
    '--entrypoint','uv',state['Image'],'tool','run','--from','py-spy','py-spy','record',
    '--pid',pid,'--duration','10','--format','raw','--output','/dev/stdout'],text=True,capture_output=True,timeout=55)
print(p.stdout);print(p.stderr[-400:]);assert p.returncode==0
