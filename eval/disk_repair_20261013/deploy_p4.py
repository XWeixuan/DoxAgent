"""Narrow P4 rollout after Read cutover; do not execute a production migration."""
import hashlib
import json
import subprocess
from pathlib import Path

WORK=Path('/home/ubuntu/disk-repair-20261013')
READ_IMAGE='doxagent-v2:disk-repair-read-20261013-r9'
GUARD_IMAGE='doxagent-v2:disk-repair-guardian-20261013-r1'


def run(args):
    result=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(result.stdout[-4000:])
    return result.stdout


def inspect(name):
    return json.loads(run(['docker','inspect',name]))[0]


def signature(value):
    return dict(command=value['Config']['Cmd'],
                environment=hashlib.sha256(json.dumps(sorted(value['Config'].get('Env',[]))).encode()).hexdigest(),
                mounts=sorted((m['Destination'],m['Source'],m['RW']) for m in value['Mounts']),
                networks=sorted(value['NetworkSettings']['Networks']))


def main(reuse_built=False):
    cutover=json.loads((WORK/'read-compaction.json').read_text())
    assert cutover['verified'] and cutover['phase']=='switched'
    assert not Path(cutover['retired_path']).exists(), 'Read acceptance/physical retirement must finish first'
    assert inspect('doxagent-v2-initialization-guardian-1')['Image']=='sha256:7d3612194b29db3d8a17dad1ac78aa00f28c1d26f557bb2db5b9067f84d9bf62'
    assert inspect('doxagent-v2-v2-transport-maintenance-1')['Config']['Image'] in ['doxagent-v2:disk-repair-read-20261013-r8',READ_IMAGE]
    context=WORK/'p4-context'
    context.mkdir(exist_ok=True)
    import tarfile
    with tarfile.open(WORK/'p4-context.tar.gz') as archive:
        archive.extractall(context,filter='data')
    (context/'Dockerfile.read').write_text(
        'FROM doxagent-v2:disk-repair-read-20261013-r8\n'
        'COPY src/doxagent/production_v2.py /app/src/doxagent/production_v2.py\n'
        'COPY src/doxagent/v2_read/backup_retention.py /app/src/doxagent/v2_read/backup_retention.py\n'
        'COPY src/doxagent/v2_read/maintenance.py /app/src/doxagent/v2_read/maintenance.py\n'
        'COPY src/doxagent/v2_read/cli.py /app/src/doxagent/v2_read/cli.py\n')
    (context/'Dockerfile.guardian').write_text(
        'FROM doxagent-v2:v21-production-20261012-r1\n'
        'COPY src/doxagent/initialization_repair/containers.py /app/src/doxagent/initialization_repair/containers.py\n'
        'COPY src/doxagent/source_maintenance/containers.py /app/src/doxagent/source_maintenance/containers.py\n')
    for dockerfile,tag in [('Dockerfile.read',READ_IMAGE),('Dockerfile.guardian',GUARD_IMAGE)]:
        if reuse_built:
            run(['docker','image','inspect',tag])
        else:
            print(run(['docker','build','-f',str(context/dockerfile),'-t',tag,str(context)]),flush=True)
    override=WORK/'v2-p4.yaml'
    override.write_text('services:\n  v2-migrate:\n    image: '+READ_IMAGE+'\n'
                        '  v2-transport-maintenance:\n    image: '+READ_IMAGE+'\n'
                        '  initialization-guardian:\n    image: '+GUARD_IMAGE+'\n'
                        '    logging:\n      driver: json-file\n'
                        '      options: {max-size: "20m", max-file: "3"}\n')
    receipt_path=WORK/'p4-deployment.json'
    receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {'services':{},'migration_executed':False}
    for service in ['v2-migrate','v2-transport-maintenance','initialization-guardian']:
        name='doxagent-v2-'+service+'-1'
        found=subprocess.run(['docker','inspect',name],capture_output=True,text=True)
        before=json.loads(found.stdout)[0] if found.returncode==0 else None
        assert before is not None or service=='v2-migrate'
        reference=before or inspect('doxagent-v2-v2-scheduler-1')
        labels=reference['Config']['Labels']
        files=list(dict.fromkeys(labels['com.docker.compose.project.config_files'].split(',')))
        command=['docker','compose','-p','doxagent-v2']
        for envfile in (labels.get('com.docker.compose.project.environment_file') or '/home/ubuntu/doxagent/.env.v2').split(','):
            if envfile:
                command+=['--env-file',envfile]
        if service=='initialization-guardian':
            environment=dict(item.split('=',1) for item in before['Config']['Env'])
            mounts={m['Destination']:m['Source'] for m in before['Mounts']}
            values={
                'DOXAGENT_INITIALIZATION_REPAIR_SOURCE_REPOSITORY':mounts['/repair-source'],
                'DOXAGENT_INITIALIZATION_REPAIR_HOST_ROOT':mounts['/var/lib/doxagent/initialization-repair'],
                'DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER':environment['DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER'],
                'DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE':environment['DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE'],
            }
            envfile=WORK/'guardian-compose.env'
            envfile.write_text(''.join(key+'='+value+'\n' for key,value in values.items()))
            envfile.chmod(0o600)
            command+=['--env-file',str(envfile)]
        for file in files:
            command+=['-f',file]
        baseline=json.loads(run(command+['config','--format','json']))['services'][service]
        if str(override) not in files:
            command+=['-f',str(override)]
        run(command+['config','--quiet'])
        if service=='v2-migrate':
            assert not baseline.get('depends_on')
        action=['create','--no-build',service] if service=='v2-migrate' else ['up','-d','--no-build','--no-deps',service]
        print(run(command+action),flush=True)
        after=inspect(name)
        if before is not None:
            assert signature(before)==signature(after), 'unexpected command/environment/mount/network change: '+service
        else:
            assert after['Config']['Cmd']==baseline['command']
            effective=dict(item.split('=',1) for item in after['Config']['Env'])
            assert all(effective.get(key)==str(value) for key,value in baseline['environment'].items())
            assert any(m['Destination']=='/data' and m['Source']=='/var/lib/docker/volumes/doxagent-v2_v2-data/_data' for m in after['Mounts'])
        if service=='v2-migrate':
            assert not after['State']['Running']
        else:
            assert after['State']['Running']
        entry=dict(before_image=before['Image'] if before else None,after_image=after['Image'],
                                          before_id=before['Id'] if before else None,after_id=after['Id'],
                                          signature_preserved=True,logging=after['HostConfig']['LogConfig'],
                                          files=list(dict.fromkeys(files+[str(override)])))
        if service in receipt['services']:
            entry['before_image']=receipt['services'][service]['before_image']
            entry['before_id']=receipt['services'][service]['before_id']
        receipt['services'][service]=entry
        (WORK/'p4-deployment.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--reuse-built-images',action='store_true')
    main(parser.parse_args().reuse_built_images)
