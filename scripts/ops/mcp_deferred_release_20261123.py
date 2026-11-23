"""Exact-file MCP/API overlays through the canonical component release owner."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path('/var/lib/doxagent/releases')
REPO = '/home/ubuntu/doxagent'
FILES = {'v2-api': ['src/doxagent/api_v2/agent_read_auth.py'],
         'v2-agent-mcp-tunnel': ['src/doxagent/agent_mcp/client.py',
                                'src/doxagent/agent_mcp/service.py']}


def git(commit, path):
    return subprocess.check_output(['sudo', '-u', 'ubuntu', 'git', '-C', REPO,
                                    'show', commit+':'+path])


def actual(container, path):
    raw = subprocess.check_output(['docker', 'cp', container+':/app/'+path, '-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        return tar.extractfile(tar.getmembers()[0]).read()


def main():
    os.umask(0o077)
    commit = sys.argv[1]
    spec = importlib.util.spec_from_file_location('release', ROOT/'storage_release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    with release.lock(ROOT):
        state = json.loads((ROOT/'state.json').read_bytes())
        config = json.loads((ROOT/'compose.current.json').read_bytes())
        directory = ROOT/('mcp-deferred-'+commit[:12])
        directory.mkdir(exist_ok=True)
        manifest = dict(commit=commit, compose_command=state['compose_base_command'], components={})
        receipt = dict(commit=commit, components={})
        for role, paths in FILES.items():
            if '--tunnel-only' in sys.argv and role != 'v2-agent-mcp-tunnel':
                continue
            container = 'doxagent-v2-'+role+'-1'
            info = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
            assert info['Image'] == state['components'][role]['current']['image']
            context = directory/role
            hashes = {}
            for path in paths:
                assert actual(container, path) == git(commit+'^', path), 'baseline drift: '+path
                target = context/path
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = git(commit, path)
                target.write_bytes(raw)
                hashes['/app/'+path] = hashlib.sha256(raw).hexdigest()
            check = ('import hashlib; expected='+repr(hashes)+
                     '; assert all(hashlib.sha256(open(p,"rb").read()).hexdigest()==h '
                     'for p,h in expected.items())')
            base = 'doxagent-mcp-recovery:base-'+info['Image'][7:19]
            subprocess.run(['docker', 'tag', info['Image'], base], check=True)
            recipe = ['FROM '+base, 'USER root']
            recipe += ['COPY --chmod=0644 '+path+' /app/'+path for path in paths]
            recipe += ['RUN python -m compileall -q --invalidation-mode checked-hash '+
                       ' '.join('/app/'+path for path in paths),
                      'USER '+(info['Config'].get('User') or 'root'),
                      'RUN python -c '+shlex.quote(check)]
            (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
            tag = 'doxagent-mcp-recovery:'+commit[:12]+'-'+role
            subprocess.run(['docker', 'build', '-t', tag, str(context)], check=True)
            image = subprocess.check_output(['docker', 'image', 'inspect', '--format',
                                             '{{.Id}}', tag], text=True).strip()
            candidate = copy.deepcopy(config['services'][role])
            candidate.update(image=image, checks=[['docker', 'exec', container, 'python', '-c', check]])
            manifest['components'][role] = candidate
            state.setdefault('owned_images', []).append(image)
            receipt['components'][role] = dict(baseline=info['Image'], image=image, hashes=hashes)
        for name in ('state.json', 'compose.current.json'):
            (directory/(name+'.before.private')).write_bytes((ROOT/name).read_bytes())
        release.atomic(ROOT/'state.json', state)
        release.atomic(directory/'manifest.private.json', manifest)
        release.reconcile = lambda *_: {'deferred': 'normal host timer owns resource retirement'}
        release.deploy(ROOT, state, manifest)
        release.atomic(directory/'receipt.json', receipt)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
