import subprocess,difflib,json
from pathlib import Path
r=Path('/home/ubuntu/doxagent')
base=subprocess.check_output(['git','-C',str(r),'show','HEAD:changelog']).decode('utf8').splitlines()
work=(r/'changelog').read_text().splitlines()
ops=difflib.SequenceMatcher(a=base,b=work,autojunk=False).get_opcodes()
print(json.dumps({'base_lines':len(base),'work_lines':len(work),'changes':[(tag,b-a,d-c) for tag,a,b,c,d in ops if tag!='equal']}))
