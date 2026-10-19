"""Narrow release through the canonical ledger; keep all business browsers alive."""
import hashlib, importlib.util, json, os, subprocess, sys, time
from pathlib import Path

repo = Path('/home/ubuntu/doxagent'); root = Path('/var/lib/doxagent/releases')
commit = sys.argv[1]
os.environ.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=str(repo))
def run(args, timeout=120):
    p = subprocess.run(args, cwd=repo, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(str(args[:3]) + ': ' + p.stderr[-1200:])
    return p.stdout.strip()
def inventory():
    ids = run(['docker','ps','-aq','--filter','label=com.docker.compose.project=doxagent-v2']).split()
    return {c['Config']['Labels']['com.docker.compose.service']: c
            for c in json.loads(run(['docker','inspect',*ids]))}
spec = importlib.util.spec_from_file_location('release', repo/'scripts/ops/storage_release.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with m.lock(root):
    assert run(['git','rev-parse','HEAD']) == commit
    before = inventory(); old = before['v2-projector']
    assert old['Image'] == 'sha256:027a61ab96371d91e62fef034500884062bd02fee3a44bc359349551d80268a7'
    audit = root/('read-freshness-'+commit[:12]); audit.mkdir(exist_ok=False)
    canonical = root/'compose.current.json'; cfg = json.loads(canonical.read_text())
    state = json.loads((root/'state.json').read_text())
    m.atomic(audit/'compose.before.private.json', cfg); os.chmod(audit/'compose.before.private.json', 0o600)
    tag = 'doxagent-projector:read-freshness-'+commit[:12]
    run(['docker','build','-f','deploy/Dockerfile.read-freshness-repair',
         '--build-arg','APPROVED_BASE='+old['Image'],'--build-arg','REPAIR_COMMIT='+commit,'-t',tag,'.'], timeout=600)
    image = run(['docker','image','inspect','--format','{{.Id}}',tag])
    # Explicit, durable rollback ownership before replacing the active container.
    state.setdefault('owned_images', []).append(image)
    state.setdefault('candidates', []).append({'image':image,'expires':time.time()+48*3600})
    m.atomic(root/'state.json',state)
    service = {**cfg['services']['v2-projector'],'image':image}
    service['environment'] = {**service.get('environment',{}),'DOXAGENT_BUILD_COMMIT':commit}
    cfg['services']['v2-projector'] = service
    command = [*state['compose_base_command'],'-f',str(canonical)]
    try:
        run(['docker','stop','--time','30',old['Id']])
        m.atomic(canonical,cfg)
        run([*command,'up','-d','--no-deps','--no-build','v2-projector'])
        time.sleep(5)
        after = inventory(); new = after['v2-projector']
        assert new['Image']==image and new['State']['Running'] and not new['RestartCount']
        for name in before:
            if name!='v2-projector':
                assert after[name]['Id']==before[name]['Id'], name
        hashes = {}
        for name in ('cli','health','lifecycle','outbox','projector','projectors','repository'):
            path = '/app/src/doxagent/v2_read/'+name+'.py'
            expected = hashlib.sha256((repo/'src/doxagent/v2_read'/Path(path).name).read_bytes()).hexdigest()
            actual = run(['docker','exec',new['Id'],'python','-c',
                          f"import hashlib;print(hashlib.sha256(open({path!r},'rb').read()).hexdigest())"])
            assert actual==expected; hashes[name]=actual
        m.promote(state,'v2-projector',image,commit=commit)
        state['components']['v2-projector']['current']['compose']=service
        m.atomic(root/'state.json',state)
        # Host GUI update only: no Site Access/Supervisor/Chrome restart.
        run(['install','-o','root','-g','root','-m','0755',str(repo/'deploy/site-login-ui.py'),
             '/usr/local/bin/doxagent-site-login-ui'])
        result=dict(commit=commit,image=image,hashes=hashes,other_containers_unchanged=True,
                    preserved_read_generation=True,finished=time.time())
        m.atomic(audit/'release.json',result); print(json.dumps(result))
    except BaseException:
        m.atomic(canonical,json.loads((audit/'compose.before.private.json').read_text()))
        run([*command,'up','-d','--no-deps','--no-build','v2-projector'])
        raise
