import json
import re
import urllib.request
from pathlib import Path

base = 'https://agent.doxatlas.com'
html = urllib.request.urlopen(base + '/overview', timeout=15).read().decode()
paths = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
results = []
for path in paths:
    request = urllib.request.Request(base + path, method='HEAD')
    with urllib.request.urlopen(request, timeout=15) as response:
        results.append(dict(path=path, status=response.status))
assert paths and all(row['status'] == 200 for row in results)
Path(__file__).with_name('http-results.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results))
