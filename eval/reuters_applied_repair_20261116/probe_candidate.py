import subprocess,json
v=json.loads(subprocess.check_output(['docker','inspect','doxagent-reuters-backup2-egress']))[0];ip=next(iter(v['NetworkSettings']['Networks'].values()))['IPAddress']
r=subprocess.run(['curl','--silent','--show-error','--max-time','20','--proxy',f'http://{ip}:18086','https://api.ipify.org?format=json'],capture_output=True,text=True)
print(json.dumps({'exit':r.returncode,'response':r.stdout[:200],'error':r.stderr[:400],'running':v['State']['Running'],'restarts':v['RestartCount']}))
