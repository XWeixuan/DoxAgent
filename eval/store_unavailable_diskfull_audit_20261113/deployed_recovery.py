import subprocess,json,io,tarfile,hashlib
out={}
for name in ['cli.py','projector.py','repository.py']:
 p=subprocess.run(['docker','cp','doxagent-v2-v2-projector-1:/app/src/doxagent/v2_read/'+name,'-'],capture_output=True,timeout=15)
 if p.returncode:out[name]={'error':p.stderr.decode()};continue
 t=tarfile.open(fileobj=io.BytesIO(p.stdout));b=t.extractfile(t.getmembers()[0]).read();lines=b.decode().splitlines()
 out[name]={'sha256':hashlib.sha256(b).hexdigest(),'excerpts':[{'line':i+1,'text':'\n'.join(lines[max(0,i-2):i+6])} for i,l in enumerate(lines) if ('except sqlite3.OperationalError' in l or 'except (ValueError' in l or 'db.commit()' in l)]}
for key,cmd in [('timers',['systemctl','list-timers','--all','--no-pager']),('units',['systemctl','list-unit-files','--no-pager'])]:
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
 out[key]=[l for l in p.stdout.splitlines() if any(w in l.lower() for w in ['dox','storage','projection'])]
print(json.dumps(out))
