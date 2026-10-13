import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
root = Path(__file__).parent
paths = []
for folder in ('src','prompts','frontend/v2','dev_plan/workflow_v2/api_contract'):
    for path in (repo / folder).rglob('*'):
        relative = path.relative_to(repo)
        if (path.is_file() and not path.is_symlink()
                and not set(relative.parts) & {'node_modules','__pycache__','.pytest_cache','test-results','artifacts','.font-download','dist'}
                and path.suffix not in {'.pyc','.pyo'} and not path.name.startswith('.env')):
            paths.append(path)
paths += [repo / name for name in ('pyproject.toml','uv.lock','README.md')]
files = {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
catalogs = {name:sha for name,sha in files.items() if name.startswith('src/cdecr/catalogs/')}
base_catalogs = json.loads((root/'base-catalog-hashes.json').read_text(encoding='utf-8-sig'))
(root/'catalog-hashes.json').write_text(json.dumps(catalogs),encoding='utf-8')
source_hash = hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
dockerfile = '''FROM doxagent-v2:rklb-v21-20261012
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY prompts ./prompts
COPY dev_plan/workflow_v2/api_contract ./dev_plan/workflow_v2/api_contract
COPY ops/catalog-hashes.json /app/ops/catalog-hashes.json
RUN python -c "import json,hashlib,pathlib; d=json.load(open('/app/ops/catalog-hashes.json')); assert all(hashlib.sha256(pathlib.Path('/app',p).read_bytes()).hexdigest()==h for p,h in d.items()), 'base catalog differs from release'"
RUN uv sync --frozen --no-group dev --inexact && python -c "import ibapi,openai_codex; import doxagent.research_adoption"
LABEL doxagent.v21.source.sha256="'''+source_hash+'"\n'
(root / 'Dockerfile.backend').write_text(dockerfile,encoding='utf-8')
(root / 'Dockerfile.web').write_text('FROM doxagent-v2-web:runtime-title-search-20261009\nCOPY frontend/v2/dist /usr/share/nginx/html\nCOPY frontend/v2/deploy/nginx.conf /etc/nginx/conf.d/default.conf\n',encoding='utf-8')
with tarfile.open(root / 'release-context.tar.gz','w:gz') as archive:
    for path in paths:
        name = path.relative_to(repo).as_posix()
        if name in catalogs and base_catalogs.get(name) == catalogs[name]:
            continue
        archive.add(path,arcname=path.relative_to(repo).as_posix())
    archive.add(root / 'Dockerfile.backend',arcname='Dockerfile.backend')
    archive.add(root / 'Dockerfile.web',arcname='Dockerfile.web')
    for path in (repo/'frontend/v2/dist').rglob('*'):
        if path.is_file():
            archive.add(path,arcname=path.relative_to(repo).as_posix())
    for name in ('adoption-manifest.json','intc-repair-evidence.json','catalog-hashes.json'):
        archive.add(root / name,arcname='ops/'+name)
manifest = dict(source_sha256=source_hash,files=files,
                git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
                backend_image='doxagent-v2:v21-production-20261012-r1',
                frontend_image='doxagent-v2-web:v21-production-20261012-r1')
(root/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({'files':len(files),'source_sha256':source_hash,'archive_bytes':(root/'release-context.tar.gz').stat().st_size}))
