"""Scope-only Site Access replacement, without recreating Supervisor/Chrome identities."""
import hashlib, importlib.util, json, os, sqlite3, subprocess, sys, time
from pathlib import Path

repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases');commit=sys.argv[1]
os.environ.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0=str(repo))
def run(args,timeout=180):
    p=subprocess.run(args,cwd=repo,text=True,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError(str(args[:3])+': '+p.stderr[-1200:])
    return p.stdout.strip()
def inventory():
    ids=run(['docker','ps','-aq','--filter','label=com.docker.compose.project=doxagent-v2']).split()
    return {c['Config']['Labels']['com.docker.compose.service']:c for c in json.loads(run(['docker','inspect',*ids]))}
spec=importlib.util.spec_from_file_location('release',repo/'scripts/ops/storage_release.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with m.lock(root):
    assert run(['git','rev-parse','HEAD'])==commit
    before=inventory();site=before['v2-site-access'];supervisor=before['v2-chrome-supervisor']
    assert site['Image']=='sha256:b1f877bf46bda7594133ea5c532cc2a1d9217f231334970fe6eae5d2b238f5d6'
    volume=next(v['Source'] for v in site['Mounts'] if v['Destination']=='/site-data')
    d=sqlite3.connect('file:'+volume+'/registry/site_strategy.sqlite3?mode=ro',uri=True)
    assert not d.execute("SELECT 1 FROM maintenance_sessions WHERE state='OPEN' LIMIT 1").fetchone(),'active maintenance session: do not interrupt'
    assert not d.execute("SELECT 1 FROM maintenance_operations WHERE state IN ('QUEUED','RUNNING') LIMIT 1").fetchone(),'maintenance operation active'
    assert not d.execute("SELECT 1 FROM challenge_tasks WHERE superseded_by IS NULL AND state NOT IN ('TARGET_LOST','RESOLVED','CANCELLED') LIMIT 1").fetchone(),'live challenge target: preserve before replacement'
    d.close()
    audit=root/('publisher-scope-'+commit[:12]);audit.mkdir(exist_ok=False)
    state=json.loads((root/'state.json').read_text());canonical=root/'compose.current.json';cfg=json.loads(canonical.read_text())
    m.atomic(audit/'compose.before.private.json',cfg);os.chmod(audit/'compose.before.private.json',0o600)
    alias='doxagent-site-base:b1f877bf46bd';tag='doxagent-site-access:publisher-scope-'+commit[:12]
    run(['docker','tag',site['Image'],alias])
    run(['docker','build','-f','deploy/Dockerfile.site-scope-repair','--build-arg','APPROVED_BASE='+alias,
         '--build-arg','REPAIR_COMMIT='+commit,'-t',tag,'.'],timeout=600)
    image=run(['docker','image','inspect','--format','{{.Id}}',tag])
    state.setdefault('owned_images',[]).append(image)
    state.setdefault('candidates',[]).append({'image':image,'expires':time.time()+48*3600})
    m.atomic(root/'state.json',state)
    candidate={**cfg['services']['v2-site-access'],'image':image}
    candidate['environment']={**candidate.get('environment',{}),'DOXAGENT_BUILD_COMMIT':commit}
    cfg['services']['v2-site-access']=candidate
    command=[*state['compose_base_command'],'-f',str(canonical)]
    try:
        run(['docker','stop','--time','-1',site['Id']],timeout=180)
        m.atomic(canonical,cfg)
        run([*command,'up','-d','--no-deps','--no-build','v2-site-access'])
        for _ in range(60):
            c=inventory()['v2-site-access']
            if c['State']['Running'] and c['State'].get('Health',{}).get('Status')=='healthy':break
            time.sleep(2)
        else:raise RuntimeError('Site Access readiness did not recover')
        after=inventory()
        assert c['Image']==image and after['v2-chrome-supervisor']['Id']==supervisor['Id']
        for name in before:
            if name!='v2-site-access':assert before[name]['Id']==after[name]['Id'],name
        assert os.readlink(f"/proc/{c['State']['Pid']}/ns/net")==os.readlink(f"/proc/{supervisor['State']['Pid']}/ns/net")
        expected=hashlib.sha256((repo/'src/doxagent/site_strategy/scope.py').read_bytes()).hexdigest()
        actual=run(['docker','exec',c['Id'],'python','-c',"import hashlib;print(hashlib.sha256(open('/app/src/doxagent/site_strategy/scope.py','rb').read()).hexdigest())"])
        assert actual==expected
        m.promote(state,'v2-site-access',image,commit=commit)
        state['components']['v2-site-access']['current']['compose']=candidate
        m.atomic(root/'state.json',state)
        receipt=dict(commit=commit,image=image,scope_sha256=expected,supervisor_unchanged=True,
                     other_containers_unchanged=True,no_cleanup=True,finished=time.time())
        m.atomic(audit/'release.json',receipt);print(json.dumps(receipt))
    except BaseException:
        m.atomic(canonical,json.loads((audit/'compose.before.private.json').read_text()))
        subprocess.run([*command,'up','-d','--no-deps','--no-build','v2-site-access'],cwd=repo,check=True)
        raise
