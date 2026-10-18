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
                'supervisor':'doxagent-chrome-supervisor','web':'doxagent-v2-web',
                'guardian':'doxagent-initialization-guardian','repair-agent':'doxagent-initialization-repair-agent'}


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


def template_references(container, images):
    index=image_index(images);references=set()
    def visit(value):
        if isinstance(value,str) and value in index:references.add(index[value])
        elif isinstance(value,dict):
            for item in value.values():visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    for entry in container.get('Config',{}).get('Env') or []:
        key,_,value=entry.partition('=')
        # Compose interpolation metadata is inherited by every service but is
        # not a runtime image dependency. Only actual creator/template inputs pin.
        if 'TEMPLATE' not in key and key not in {'AGENT','DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE'}:continue
        try:visit(json.loads(value))
        except ValueError:visit(value)
    return references


def record_live_references(state, images, containers):
    for container in containers:
        component = (container.get('Config',{}).get('Labels') or {}).get('com.docker.compose.service')
        current = state.get('components',{}).get(component,{}).get('current')
        if not current or current['image'] != container['Image']:continue
        current['references'] = sorted(template_references(container,images))


def record_repair_references(state, containers):
    import sqlite3
    references=set()
    for container in containers:
        if (container.get('Config',{}).get('Labels') or {}).get('com.docker.compose.service')!='initialization-guardian':continue
        command=container['Config'].get('Cmd') or []
        if '--initialization-db' not in command:continue
        target=command[command.index('--initialization-db')+1]
        mount=next((m for m in container.get('Mounts',[]) if target.startswith(m['Destination'].rstrip('/')+'/')),None)
        if mount is None:raise ValueError('Guardian repair DB mount could not be resolved')
        path=Path(mount['Source'])/target[len(mount['Destination']):].lstrip('/')
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            for image, in db.execute("SELECT json_extract(payload,'$.source_image_id') FROM initialization_repair_incidents WHERE status IN ('ACTIVE','HUMAN_REQUIRED') UNION SELECT json_extract(r.payload,'$.image_id') FROM initialization_repair_rounds r JOIN initialization_repair_incidents i ON i.id=r.incident_id WHERE i.status IN ('ACTIVE','HUMAN_REQUIRED')"):
                if image:references.add(image)
    state['repair_images']=sorted(references)


def capacity_snapshot(root, containers, *, now=None):
    """One small host-owned daily sample; du counts hardlinks once per volume."""
    if os.name != 'posix':return
    now=now or time.time();directory=root/'capacity';directory.mkdir(exist_ok=True)
    destination=directory/(datetime.fromtimestamp(now).strftime('%Y-%m-%d')+'.json')
    if not destination.exists():
        mounts={m['Source'] for c in containers for m in c.get('Mounts',[]) if m.get('Type')=='volume'}
        targets=sorted(mounts | {str(root),'/var/lib/containerd'})
        samples=[]
        for target in targets:
            if not Path(target).exists():continue
            try:result=subprocess.run(['du','-x','-B1','-d1',target],capture_output=True,text=True,timeout=240)
            except subprocess.TimeoutExpired as exc:
                from types import SimpleNamespace
                result=SimpleNamespace(stdout=(exc.stdout or b'').decode() if isinstance(exc.stdout,bytes) else exc.stdout or '',stderr='incomplete: daily scan time budget reached')
            samples.append(dict(path=target,entries=[dict(bytes=int(row.split('\t',1)[0]),path=row.split('\t',1)[1]) for row in result.stdout.splitlines() if '\t' in row],error=result.stderr[:500] or None))
        atomic(destination,dict(at=now,root=dict(zip(('total','used','free'),shutil.disk_usage('/'))),volumes=samples))
    for path in directory.glob('????-??-??.json'):
        if now-path.stat().st_mtime>30*86400:path.unlink()


def protected_images(state, images, containers, *, now=None):
    now = now or time.time(); index = image_index(images)
    protected = {c['Image'] for c in containers}
    protected.update(state.get('repair_images',[]))
    for component in state.get('components',{}).values():
        # An old but still sole valid rollback never expires by calendar alone.
        for key in ('current','previous'):
            if component.get(key):
                protected.add(index.get(component[key]['image'],component[key]['image']))
                protected.update(component[key].get('references',[]))
    for deps in state.get('dependencies',{}).values():
        protected.add(index.get(deps['image'],deps['image']))
    for hold in state.get('holds',[]):
        if hold.get('component') and hold.get('release_image') not in {v.get('image') for k,v in state.get('components',{}).get(hold['component'],{}).items() if k in ('current','previous')}:
            continue
        if hold.get('until') is None or hold['until'] > now:
            protected.add(index.get(hold['image'],hold['image']))
    for candidate in state.get('candidates',[]):
        if candidate['expires'] > now:
            protected.add(index.get(candidate['image'],candidate['image']))
    # Guardian/template images can be dormant. Resolve actual live environment,
    # including structured templates, without persisting or printing its values.
    for container in containers:
        protected.update(template_references(container,images))
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
    record_live_references(state,images,containers)
    record_repair_references(state,containers)
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
        known=image['Id'] in state.get('owned_images',[])
        if image['Id'] in protected or not owned and not (known and not tags):
            continue
        # Another owner (for example Guardian) may have just built its next
        # agent. Bootstrap explicitly takes over older known inventory; new
        # unregistered candidates get the same finite 48h failure window.
        if image['Id'] not in state.get('owned_images',[]) and image.get('Created'):
            created=datetime.fromisoformat(image['Created'].replace('Z','+00:00')).timestamp()
            if time.time()-created < 48*3600:continue
        for tag in owned or [image['Id']]:
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
    staging=root/'staging'
    if staging.exists():
        for path in staging.iterdir():
            if path.is_dir() and not path.is_symlink() and re.fullmatch('[0-9a-f]{12}-.+',path.name) and time.time()-path.stat().st_mtime>48*3600:
                shutil.rmtree(path)
    try:capacity_snapshot(root,containers)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:report['capacity_error']=str(exc)[:250]
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


def verify_dependency_image(tag, kind, manifests):
    info=json.loads(run(['docker','image','inspect',tag]))[0]
    labels=info.get('Config',{}).get('Labels') or {}
    if labels.get('doxagent.role')!='dependencies' or labels.get('doxagent.owner')!='doxagent':
        return False
    if shutil.disk_usage('/').free < info.get('Size',0)+512*1024**2:
        raise OSError('recovering dependency image needs space for its first runtime unpack')
    code=("import hashlib,importlib.metadata as m,json,pathlib,subprocess;"
          "root=pathlib.Path('/app');assert not (root/'src/doxagent').exists();"
          "assert m.version('openai-codex')=='0.159.3';"
          "assert m.version('openai-codex-cli-bin')=='0.159.3';"
          "assert m.version('playwright')=='1.63.0';"
          "assert m.version('protobuf')=='5.29.5';import ibapi;"
          "print(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() "
          "for n in ('pyproject.toml','uv.lock')}))")
    observed=json.loads(run(['docker','run','--rm','--network','none','--entrypoint','python',tag,'-c',code]))
    return observed=={k:manifests[k] for k in ('pyproject.toml','uv.lock')}


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
        # Guardian's existing source-identity contract hashes the full committed
        # archive, independently of the smaller application build context.
        archive=subprocess.Popen(['git','-C',str(repo),'archive','--format=tar',commit],stdout=subprocess.PIPE)
        try:source_sha=hashlib.file_digest(archive.stdout,'sha256').hexdigest()
        finally:archive.stdout.close()
        if archive.wait():raise ValueError('committed source identity unavailable')
        index=image_index(inspect_images())
        if sdk not in index or chrome not in index:raise ValueError('approved SDK/Chrome baseline unavailable')
        dependency_inputs={n:hashlib.sha256((context/n).read_bytes()).hexdigest() for n in ('pyproject.toml','uv.lock')}
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
        if set(kinds)&{'guardian','repair-agent'}:required.add('backend')
        required.update(set(kinds)&{'guardian','repair-agent'})
        dependencies={}
        import re
        base_tag=re.search(r'^ARG PYTHON_BASE_IMAGE=(.+)$',(context/'Dockerfile.v2').read_text(),re.M)[1].strip()
        base_pin=state.get('base_pins',{}).get(base_tag)
        if not base_pin and str(state.get('python_base_reference','')).startswith(base_tag+'@'):base_pin=state['python_base_reference']
        if not base_pin:
            import re
            metadata=run(['docker','buildx','imagetools','inspect',base_tag])
            match=re.search(r'^Digest:\s+(sha256:[0-9a-f]{64})',metadata,re.M)
            if not match:raise ValueError('could not resolve Python dependency base digest')
            base_pin=base_tag+'@'+match[1]
            state['python_base_reference']=base_pin
            state.setdefault('base_pins',{})[base_tag]=base_pin
            atomic(root/'state.json',state)
        cli_pin=None
        if 'guardian' in required:
            cli_tag=re.search(r'^ARG DOCKER_CLI_IMAGE=(.+)$',(context/'deploy/Dockerfile.initialization-guardian').read_text(),re.M)[1].strip()
            cli_pin=state.get('base_pins',{}).get(cli_tag)
            if not cli_pin:
                match=re.search(r'^Digest:\s+(sha256:[0-9a-f]{64})',run(['docker','buildx','imagetools','inspect',cli_tag]),re.M)
                if not match:raise ValueError('Docker CLI dependency digest unavailable')
                cli_pin=cli_tag+'@'+match[1];state.setdefault('base_pins',{})[cli_tag]=cli_pin;atomic(root/'state.json',state)
        for kind,dockerfile,target,base in (
            ('backend','Dockerfile.v2','backend-deps',None),
            ('site','deploy/Dockerfile.site-access','site-deps','backend'),
            ('supervisor','deploy/Dockerfile.chrome-supervisor','supervisor-deps','site'),
            ('guardian','deploy/Dockerfile.initialization-guardian','guardian-deps','backend'),
            ('repair-agent','deploy/Dockerfile.initialization-repair-agent','repair-agent-deps','backend'),
        ):
            if kind not in required:continue
            text=(context/dockerfile).read_text()
            # Everything before the app-stage boundary is a stable dependency.
            boundary={'backend':'FROM backend-deps AS backend','site':'FROM site-deps AS site',
                      'supervisor':'FROM supervisor-deps AS supervisor','guardian':'FROM guardian-deps AS guardian',
                      'repair-agent':'FROM repair-agent-deps AS repair-agent'}[kind]
            if boundary+'\n' not in text:raise ValueError('commit does not declare stable dependency stages: '+dockerfile)
            inputs=dict(manifests=dependency_inputs,dockerfile=text.split(boundary+'\n',1)[0],
                        python_base=base_pin,base=dependencies.get(base))
            if kind=='backend':inputs['sdk']=index[sdk]
            if kind=='supervisor':inputs['chrome']=index[chrome]
            if kind=='guardian':inputs['docker_cli']=cli_pin
            key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]
            tag='doxagent-deps:'+kind+'-'+key
            args=['docker','build','-f',dockerfile,'--target',target,'-t',tag,
                  '--label','doxagent.owner=doxagent','--label','doxagent.role=dependencies']
            if base:args+=['--build-arg',('DOXAGENT_BASE_IMAGE=' if kind in {'guardian','repair-agent'} else 'BASE_IMAGE=')+dependencies[base]['tag']]
            if kind=='backend':args+=['--build-arg','IBKR_SDK_SOURCE_IMAGE='+sdk]
            if kind=='supervisor':args+=['--build-arg','CHROME_RUNTIME_IMAGE='+chrome]
            if kind=='backend':args+=['--build-arg','PYTHON_BASE_IMAGE='+base_pin]
            if kind=='guardian':args+=['--build-arg','DOCKER_CLI_IMAGE='+cli_pin]
            previous=state.get('dependencies',{}).get(kind)
            known=image_index(inspect_images())
            recorded=previous and previous.get('key')==key and known.get(tag)==previous['image']
            recovered=not recorded and tag in known and verify_dependency_image(tag,kind,dependency_inputs)
            if not recorded and not recovered:
                minimum=(1 if resume_dependencies else 8)*1024**3
                if kind=='backend' and shutil.disk_usage('/').free < minimum:
                    raise OSError('insufficient dependency staging space; fresh build needs 8 GiB, explicit cached resume needs 1 GiB')
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
                for dep,arg in (('backend','BACKEND_BASE'),('site','SITE_BASE'),('supervisor','SUPERVISOR_BASE'),('guardian','GUARDIAN_BASE'),('repair-agent','REPAIR_AGENT_BASE')):
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
    state['compose_base_command']=base_command
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
        record_live_references(state,inspect_images(),inspect_containers())
        state['components'][component]['current']['compose']=new
        # This write is the recovery boundary: a crash before cleanup retains
        # both accepted versions and the timer can complete retirement later.
        atomic(root/'state.json',state)
    return reconcile(root,state)


def prepare_inputs(root, state):
    """Reduce approved donors to their exact consumed trees, preserving bytes/modes."""
    import tarfile
    receipt=[]
    inputs=(('ibapi','doxagent-ibapi:approved-7d361219',
             ['/app/.venv/lib/python3.11/site-packages/ibapi','/app/.venv/lib/python3.11/site-packages/ibapi-10.49.2.dist-info']),
            ('chrome','doxagent-chrome:approved-cc38be0a',['/opt/google/chrome']))
    def manifest(container,path):
        process=subprocess.Popen(['docker','cp',container+':'+path,'-'],stdout=subprocess.PIPE)
        rows=[]
        try:
            with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
                for member in archive:
                    digest=None
                    if member.isfile():
                        with archive.extractfile(member) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    rows.append((member.name,member.type.decode(),member.mode,member.linkname,digest))
        finally:process.stdout.close()
        if process.wait():raise ValueError('approved donor content could not be read')
        return sorted(rows)
    for name,tag,paths in inputs:
        old=json.loads(run(['docker','image','inspect',tag]))[0]
        if (old['Config'].get('Labels') or {}).get('doxagent.role')=='approved-input':continue
        candidate='doxagent-approved-inputs:'+name+'-'+old['Id'][7:19]
        recipe='FROM '+tag+' AS donor\nFROM scratch\n'+''.join('COPY --from=donor '+p+' '+p+'\n' for p in paths)
        with tempfile.TemporaryDirectory(prefix='approved-input-',dir=root) as temporary:
            context=Path(temporary);(context/'Dockerfile').write_text(recipe)
            guarded_build(['docker','build','-t',candidate,'--label','doxagent.owner=doxagent','--label','doxagent.role=approved-input','.'],cwd=context)
        new=json.loads(run(['docker','image','inspect',candidate]))[0];containers=[]
        try:
            for image in (old['Id'],new['Id']):
                containers.append(run(['docker','create','--label','doxagent.owner=doxagent','--label','doxagent.temporary=true',image,'/unexecuted']))
            proof=[]
            for path in paths:
                original=manifest(containers[0],path);selected=manifest(containers[1],path)
                if original!=selected:raise ValueError('minimal approved input differs: '+path)
                proof.append(dict(path=path,entries=len(original),sha256=hashlib.sha256(json.dumps(original).encode()).hexdigest()))
        finally:
            for container in containers:subprocess.run(['docker','rm',container],check=True,capture_output=True)
        item=dict(kind=name,old=old['Id'],image=new['Id'],proof=proof,at=time.time())
        receipt.append(item);atomic(root/('approved-'+name+'.json'),item)
        subprocess.run(['docker','tag',new['Id'],tag],check=True)
        for hold in state.get('holds',[]):
            if hold['image']==old['Id']:hold['image']=new['Id']
        for identity in (old['Id'],new['Id']):
            if identity not in state.setdefault('owned_images',[]):state['owned_images'].append(identity)
        atomic(root/'state.json',state)
    return receipt


def release_components(root, state, repo, commit, components):
    if not components:raise ValueError('release requires explicit --component selections')
    unknown=set(components)-set(state.get('components',{}))
    if unknown:raise ValueError('unregistered components: '+','.join(sorted(unknown)))
    kinds={'v2-site-access':'site','v2-chrome-supervisor':'supervisor','v2-web':'web','initialization-guardian':'guardian'}
    selected={c:kinds.get(c,'backend') for c in components}
    built=build(repo,commit,root,sorted(set(selected.values())))
    state=json.loads((root/'state.json').read_text())
    base=state.get('compose_base_command')
    if not base:
        base=list(state['compose_commands'][0]);override=str(root/'compose.current.json')
        if override in base:
            i=base.index(override)
            if i and base[i-1]=='-f':del base[i-1:i+1]
    manifest=dict(compose_command=base,commit=built['commit'],components={})
    for component,kind in selected.items():
        current=state['components'][component]['current']
        candidate={**current.get('compose',{}),'image':built['images'][kind]['image']}
        candidate['schema']=current.get('schema');manifest['components'][component]=candidate
    atomic(root/(built['commit']+'.release.json'),manifest)
    return deploy(root,state,manifest)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('build','deploy','release','prepare-inputs','reconcile-resources','bootstrap'))
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--commit')
    parser.add_argument('--kind',action='append',choices=tuple(REPOSITORIES))
    parser.add_argument('--component',action='append')
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
        elif args.command=='release':
            if not args.commit:parser.error('release requires an exact commit')
            result=release_components(args.root,state,args.repo,args.commit,args.component)
        elif args.command=='prepare-inputs':result=prepare_inputs(args.root,state)
        elif args.command=='bootstrap':
            if state:raise ValueError('release state already exists')
            state=json.loads(args.manifest.read_text())
            atomic(path,state);result={'bootstrapped':True}
        elif args.command=='deploy':
            result=deploy(args.root,state,json.loads(args.manifest.read_text()))
        else:result=reconcile(args.root,state)
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
