"""Host-owned builds, component releases and retryable resource retirement.

Use the same reconcile-resources command after deploy and from the host timer.
State contains image identities and ownership, never production credentials.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path('/var/lib/doxagent/releases')
REPOSITORIES = {'backend':'doxagent-v2','site':'doxagent-site-access',
                'supervisor':'doxagent-chrome-supervisor','web':'doxagent-v2-web'}


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


@contextlib.contextmanager
def lock(root):
    import fcntl
    root.mkdir(parents=True,exist_ok=True)
    with (root/'release.lock').open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def inspect_images():
    ids = run(['docker','image','ls','-aq','--no-trunc']).splitlines()
    return json.loads(run(['docker','image','inspect',*sorted(set(ids))])) if ids else []


def inspect_containers():
    ids = run(['docker','ps','-aq','--no-trunc']).splitlines()
    return json.loads(run(['docker','inspect',*ids])) if ids else []


def image_index(images):
    return {name:image['Id'] for image in images for name in
            [image['Id'],*(image.get('RepoTags') or []),*(image.get('RepoDigests') or [])]}


def protected_images(state, images, containers, *, now=None):
    now = now or time.time(); index = image_index(images)
    protected = {c['Image'] for c in containers}
    for component in state.get('components',{}).values():
        # An old but still sole valid rollback never expires by calendar alone.
        for key in ('current','previous'):
            if component.get(key): protected.add(index.get(component[key]['image'],component[key]['image']))
    for deps in state.get('dependencies',{}).values():
        protected.add(index.get(deps['image'],deps['image']))
    for hold in state.get('holds',[]):
        if hold.get('until') is None or hold['until'] > now:
            protected.add(index.get(hold['image'],hold['image']))
    for candidate in state.get('candidates',[]):
        if candidate['expires'] > now:
            protected.add(index.get(candidate['image'],candidate['image']))
    # Guardian/template images can be dormant. Resolve actual live environment,
    # including structured templates, without persisting or printing its values.
    tags = {tag:image['Id'] for image in images for tag in image.get('RepoTags') or []}
    for container in containers:
        environment = '\n'.join(container['Config'].get('Env') or [])
        for tag, identity in tags.items():
            if tag in environment: protected.add(identity)
    for configured in state.get('configured_images',[]):
        if configured in index: protected.add(index[configured])
    return protected


def promote(state, component, image, *, commit, schema=None, at=None):
    at = at or time.time(); entry = state.setdefault('components',{}).setdefault(component,{})
    current = entry.get('current')
    if current and current['image'] == image:
        return
    if current:
        entry['previous'] = current
    entry['current'] = dict(image=image,commit=commit,accepted_at=at,schema=schema)
    state['candidates'] = [c for c in state.get('candidates',[]) if c['image'] != image]


def reconcile(root, state):
    if state.get('format') != 'doxagent.component-releases.1' or not state.get('components'):
        raise ValueError('bootstrap the audited component rollback inventory before retirement')
    images, containers = inspect_images(), inspect_containers()
    before = shutil.disk_usage('/').free
    report = dict(at=time.time(),available_before=before,retired=[],errors=[])
    processes=run(['ps','-eo','args'])
    import re
    if any(re.match(r'^(?:\S*/)?docker\s+(?:build\s|buildx\s+build\s)',p.strip()) for p in processes.splitlines()):
        state['cleanup_pending']=True;atomic(root/'state.json',state)
        return {**report,'deferred':'active host build'}
    # Re-resolve Compose on every pass. Failure is recorded; live container and
    # persisted component references still remain protected during a retry.
    for command in state.get('compose_commands',[]):
        try:
            configuration = json.loads(run([*command,'config','--format','json']))
            state['configured_images'] = sorted({s['image'] for s in configuration['services'].values() if s.get('image')})
        except subprocess.CalledProcessError as exc:
            report['errors'].append('compose reference refresh failed: '+str(exc.returncode))
            state['cleanup_pending'] = True; atomic(root/'state.json',state)
            return report
    protected = protected_images(state,images,containers)
    for image in images:
        tags = image.get('RepoTags') or []
        owned = [t for t in tags if t.split(':',1)[0].startswith('doxagent-')]
        if image['Id'] in protected or not owned:
            continue
        # Another owner (for example Guardian) may have just built its next
        # agent. Bootstrap explicitly takes over older known inventory; new
        # unregistered candidates get the same finite 48h failure window.
        if image['Id'] not in state.get('owned_images',[]) and image.get('Created'):
            created=datetime.fromisoformat(image['Created'].replace('Z','+00:00')).timestamp()
            if time.time()-created < 48*3600:continue
        for tag in owned:
            try:
                subprocess.run(['docker','image','rm',tag],check=True,capture_output=True,text=True)
                report['retired'].append(dict(tag=tag,image=image['Id']))
            except subprocess.CalledProcessError as exc:
                report['errors'].append(dict(tag=tag,error=exc.stderr.strip()))
    # The release lock prevents our own builds racing this GC. Docker itself
    # protects other active builders' records and all shared image layers.
    try:
        subprocess.run(['docker','builder','prune','--force','--all','--filter','until=48h',
                        '--max-used-space','6GB','--reserved-space','0'],check=True,capture_output=True,text=True)
        # Low space may require reclaimable younger records as well.
        if shutil.disk_usage('/').free < 20*1024**3:
            subprocess.run(['docker','builder','prune','--force','--all','--max-used-space','6GB',
                            '--reserved-space','0','--min-free-space','20GB'],
                           check=True,capture_output=True,text=True)
    except subprocess.CalledProcessError as exc:
        report['errors'].append(dict(stage='build-cache',error=exc.stderr.strip()))
    report['available_after'] = shutil.disk_usage('/').free
    state['cleanup_pending'] = bool(report['errors']); state['last_reconcile'] = report
    atomic(root/'state.json',state); atomic(root/'last-reconcile.json',report)
    return report


def guarded_build(args, *, cwd):
    import signal
    if shutil.disk_usage('/').free < 512*1024**2:
        raise OSError('build has less than 512 MiB writable reserve')
    process=subprocess.Popen(args,cwd=cwd,start_new_session=True)
    try:
        while process.poll() is None:
            if shutil.disk_usage('/').free < 512*1024**2:
                os.killpg(process.pid,signal.SIGTERM)
                process.wait(timeout=30)
                raise OSError('build cancelled before consuming the production write reserve')
            time.sleep(.5)
        if process.returncode:raise subprocess.CalledProcessError(process.returncode,args)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=30)
        raise


def build(repo, commit, root, kinds, *, sdk='doxagent-ibapi:approved-7d361219',
          chrome='doxagent-chrome:approved-cc38be0a', resume_dependencies=False):
    commit = run(['git','-C',str(repo),'rev-parse',commit+'^{commit}'])
    state = json.loads((root/'state.json').read_text()) if (root/'state.json').exists() else {}
    receipt = dict(commit=commit,images={},dependencies={},started=time.time())
    allowed = ['Dockerfile.v2','.dockerignore','deploy','src','prompts','pyproject.toml',
               'uv.lock','README.md','dev_plan/workflow_v2/api_contract',
               'scripts/ops/migrate_source_access_scopes.py','frontend/v2']
    staging_root=root/'staging';staging_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=commit[:12]+'-',dir=staging_root) as temporary:
        context=Path(temporary)
        archive=subprocess.Popen(['git','-C',str(repo),'archive',commit,*allowed],stdout=subprocess.PIPE)
        try:
            subprocess.run(['tar','-xf','-','-C',str(context)],stdin=archive.stdout,check=True)
        finally:
            archive.stdout.close()
        if archive.wait()!=0:raise ValueError('committed build context unavailable')
        source_sha=hashlib.sha256(run(['git','-C',str(repo),'rev-parse',commit+'^{tree}']).encode()).hexdigest()
        index=image_index(inspect_images())
        if sdk not in index or chrome not in index:raise ValueError('approved SDK/Chrome baseline unavailable')
        dependency_inputs={n:hashlib.sha256((context/n).read_bytes()).hexdigest() for n in ('pyproject.toml','uv.lock','README.md')}
        catalog_hash=hashlib.sha256()
        for path in sorted((context/'src/cdecr/catalogs').rglob('*')):
            if path.is_file():
                catalog_hash.update(path.relative_to(context/'src').as_posix().encode())
                catalog_hash.update(hashlib.file_digest(path.open('rb'),'sha256').digest())
        dependency_inputs['cdecr_catalogs']=catalog_hash.hexdigest()
        required=set()
        if 'backend' in kinds:required.add('backend')
        if set(kinds)&{'site','supervisor'}:required.update(('backend','site'))
        if 'supervisor' in kinds:required.add('supervisor')
        dependencies={}
        base_tag='ghcr.io/astral-sh/uv:0.8.14-python3.11-bookworm-slim'
        base_pin=state.get('python_base_reference')
        if not base_pin:
            import re
            metadata=run(['docker','buildx','imagetools','inspect',base_tag])
            match=re.search(r'^Digest:\s+(sha256:[0-9a-f]{64})',metadata,re.M)
            if not match:raise ValueError('could not resolve Python dependency base digest')
            base_pin=base_tag+'@'+match[1]
            state['python_base_reference']=base_pin
            atomic(root/'state.json',state)
        for kind,dockerfile,target,base in (
            ('backend','Dockerfile.v2','backend-deps',None),
            ('site','deploy/Dockerfile.site-access','site-deps','backend'),
            ('supervisor','deploy/Dockerfile.chrome-supervisor','supervisor-deps','site'),
        ):
            if kind not in required:continue
            text=(context/dockerfile).read_text()
            # Everything before the app-stage boundary is a stable dependency.
            boundary={'backend':'FROM backend-deps AS backend','site':'FROM site-deps AS site',
                      'supervisor':'FROM supervisor-deps AS supervisor'}[kind]
            inputs=dict(manifests=dependency_inputs,dockerfile=text.split(boundary)[0],
                        sdk=index[sdk],chrome=index[chrome],python_base=base_pin,base=dependencies.get(base))
            key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]
            tag='doxagent-deps:'+kind+'-'+key
            args=['docker','build','-f',dockerfile,'--target',target,'-t',tag,
                  '--label','doxagent.owner=doxagent','--label','doxagent.role=dependencies']
            if base:args+=['--build-arg','BASE_IMAGE='+dependencies[base]['tag']]
            if kind=='backend':args+=['--build-arg','IBKR_SDK_SOURCE_IMAGE='+sdk]
            if kind=='supervisor':args+=['--build-arg','CHROME_RUNTIME_IMAGE='+chrome]
            if kind=='backend':args+=['--build-arg','PYTHON_BASE_IMAGE='+base_pin]
            previous=state.get('dependencies',{}).get(kind)
            known=image_index(inspect_images())
            if not previous or previous.get('key')!=key or known.get(tag)!=previous['image']:
                minimum=(1 if resume_dependencies else 4)*1024**3
                if kind=='backend' and shutil.disk_usage('/').free < minimum:
                    raise OSError('insufficient dependency staging space; fresh build needs 4 GiB, explicit cached resume needs 1 GiB')
                guarded_build([*args,'.'],cwd=context)
            image=json.loads(run(['docker','image','inspect',tag]))[0]
            dependencies[kind]=dict(image=image['Id'],tag=tag,layers=image['RootFS']['Layers'],key=key)
            subprocess.run(['docker','tag',image['Id'],'doxagent-deps:'+kind],check=True)
            state.setdefault('dependencies',{})[kind]=dependencies[kind]
            atomic(root/'state.json',state)
        receipt['dependencies']=dependencies
        state.setdefault('dependencies',{}).update(dependencies)
        atomic(root/'state.json',state)
        # Source files come only from the selected commit. Move stable catalogs
        # out of the app COPY so a Python edit does not add another 185 MiB layer.
        catalog=context/'src/cdecr/catalogs'
        catalog.rename(context/'catalog-input')
        for kind in kinds:
            tag=REPOSITORIES[kind]+':storage-'+commit[:12]
            if kind=='web':
                args=['docker','build','-f','frontend/v2/Dockerfile','-t',tag,
                      '--build-arg','DOXAGENT_BUILD_COMMIT='+commit,'.']
            else:
                args=['docker','build','-f','deploy/Dockerfile.source-release','--target',kind,
                      '-t',tag,'--build-arg','DOXAGENT_BUILD_COMMIT='+commit,
                      '--build-arg','DOXAGENT_SOURCE_SHA256='+source_sha]
                for dep,arg in (('backend','BACKEND_BASE'),('site','SITE_BASE'),('supervisor','SUPERVISOR_BASE')):
                    selected=dependencies.get(dep) or state['dependencies'].get(dep) or dependencies['backend']
                    args+=['--build-arg',arg+'='+selected['tag']]
                args+=['.']
            guarded_build(args,cwd=context)
            image=json.loads(run(['docker','image','inspect',tag]))[0]
            deps=dependencies.get(kind)
            if deps and image['RootFS']['Layers'][:len(deps['layers'])] != deps['layers']:
                raise ValueError('application did not inherit the exact stable dependency layers')
            receipt['images'][kind]=dict(tag=tag,image=image['Id'],layers=image['RootFS']['Layers'])
            if image['Id'] not in state.setdefault('owned_images',[]):state['owned_images'].append(image['Id'])
            state.setdefault('candidates',[]).append(dict(image=image['Id'],expires=time.time()+48*3600))
            atomic(root/'state.json',state)
            atomic(root/(commit+'.build.json'),receipt)
    receipt.update(finished=time.time(),source_tree=source_sha)
    atomic(root/(commit+'.build.json'),receipt)
    return receipt


def deploy(root, state, manifest):
    """Manifest supplies the actual Compose command, per-service candidate and checks."""
    base_command=manifest['compose_command'];commit=manifest['commit']
    if base_command[:2] != ['docker','compose']:raise ValueError('deploy requires Docker Compose')
    override=root/'compose.current.json'
    configuration=json.loads(override.read_text()) if override.exists() else {'services':{}}
    command=[*base_command,'-f',str(override)]
    state['compose_commands']=[command]
    for component, candidate in manifest['components'].items():
        identity=run(['docker','image','inspect','--format','{{.Id}}',candidate['image']])
        state.setdefault('candidates',[]).append(dict(image=identity,expires=time.time()+48*3600))
        atomic(root/'state.json',state)
        previous=configuration['services'].get(component)
        if previous is None:
            old=state.get('components',{}).get(component,{}).get('current')
            previous={'image':old['image']} if old else None
        new={k:v for k,v in candidate.items() if k in ('environment','command','entrypoint')}
        new['image']=identity;configuration['services'][component]=new
        atomic(override,configuration)
        try:
            subprocess.run([*command,'up','-d','--no-deps','--no-build',component],check=True)
            started=time.monotonic();deadline=started+candidate.get('health_seconds',90)
            while True:
                container=run([*command,'ps','-q',component])
                if container:
                    info=json.loads(run(['docker','inspect',container]))[0]
                    if info['Image'] != identity:raise ValueError('Compose selected a different image')
                    health=info['State'].get('Health',{}).get('Status')
                    if info['State']['Running'] and (health=='healthy' or health is None and time.monotonic()-started>=5):break
                if time.monotonic()>deadline:raise TimeoutError('candidate health failed: '+component)
                time.sleep(2)
            for check in candidate.get('checks',[]):subprocess.run(check,check=True)
        except BaseException:
            if previous is not None:
                configuration['services'][component]=previous;atomic(override,configuration)
                subprocess.run([*command,'up','-d','--no-deps','--no-build',component],check=True)
            state['cleanup_pending']=True;atomic(root/'state.json',state)
            raise
        promote(state,component,identity,commit=commit,schema=candidate.get('schema'))
        state['components'][component]['current']['compose']=new
        # This write is the recovery boundary: a crash before cleanup retains
        # both accepted versions and the timer can complete retirement later.
        atomic(root/'state.json',state)
    return reconcile(root,state)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('build','deploy','reconcile-resources','bootstrap'))
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--commit')
    parser.add_argument('--kind',action='append',choices=tuple(REPOSITORIES))
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--resume-dependencies',action='store_true',
                        help='resume a completed/cancelled dependency build from BuildKit cache; the write reserve still applies')
    args=parser.parse_args()
    with lock(args.root):
        path=args.root/'state.json'
        state=json.loads(path.read_text()) if path.exists() else {}
        if args.command=='build':
            if not args.commit:parser.error('build requires an exact commit')
            result=build(args.repo,args.commit,args.root,args.kind or ['backend','site','supervisor'],resume_dependencies=args.resume_dependencies)
        elif args.command=='bootstrap':
            if state:raise ValueError('release state already exists')
            state=json.loads(args.manifest.read_text())
            atomic(path,state);result={'bootstrapped':True}
        elif args.command=='deploy':
            result=deploy(args.root,state,json.loads(args.manifest.read_text()))
        else:result=reconcile(args.root,state)
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
