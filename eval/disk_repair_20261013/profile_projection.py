"""Profile one normal, writer-fenced tick, then always restore the normal projector."""
import json
import subprocess
from pathlib import Path

name='doxagent-v2-v2-projector-1'
config=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
command=config['Config']['Cmd']
assert command[:3]==['python','-m','doxagent.v2_read.cli'],command
subprocess.run(['docker','stop','-t','60',name],check=True)
try:
    run=['docker','run','--rm','--name','disk-repair-projection-profile',
         '--network','none','--volumes-from',name]
    for value in config['Config']['Env']:
        run+=['-e',value]
    run += [config['Image'],'python','-m','cProfile','-o','/data/read/disk-repair-profile.stats',
            *command[1:],'--once']
    subprocess.run(run,check=True,timeout=120)
finally:
    subprocess.run(['docker','start',name],check=True)
result=subprocess.check_output(['docker','run','--rm','--network','none','--volumes-from',name,
    config['Image'],'python','-c',
    'import pstats; pstats.Stats("/data/read/disk-repair-profile.stats").sort_stats("cumtime").print_stats(45)'],text=True)
Path('/home/ubuntu/disk-repair-20261013/projection-profile.txt').write_text(result)
print(result)
