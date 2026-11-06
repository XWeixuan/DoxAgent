import io,tarfile,subprocess
from pathlib import Path
root=Path('eval/rss_http_race_20261107');src=Path('src')
with tarfile.open(root/'overlay.tgz','w:gz') as t:
 data=b'from pkgutil import extend_path\n__path__=extend_path(__path__,__name__)\n';info=tarfile.TarInfo('doxagent/__init__.py');info.size=len(data);t.addfile(info,io.BytesIO(data))
 for p in (src/'doxagent/message_bus_v2').rglob('*.py'):t.add(p,arcname=str(p.relative_to(src)).replace('\\','/'))
subprocess.run(['scp',str(root/'overlay.tgz'),'doxagent-sg:/tmp/rss-http-race-overlay.tgz'],check=True)
subprocess.run(['ssh','doxagent-sg','sudo -n docker cp /tmp/rss-http-race-overlay.tgz doxagent-v2-v2-message-collector-1:/tmp/rss-http-race-overlay.tgz && sudo -n docker exec doxagent-v2-v2-message-collector-1 sh -c "mkdir -p /data/diagnostics/rss-http-race-20261107/overlay && tar -xzf /tmp/rss-http-race-overlay.tgz -C /data/diagnostics/rss-http-race-20261107/overlay"'],check=True)
