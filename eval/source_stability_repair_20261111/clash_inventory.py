import subprocess,json
v=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0]
print(json.dumps({'mounts':[(x['Source'],x['Destination']) for x in v['Mounts']],'ports':v['NetworkSettings']['Ports']},ensure_ascii=False))
