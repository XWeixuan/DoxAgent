"""Print content shape only; never print subscription URLs or node credentials."""
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import yaml

sources = json.load(open('/opt/doxagent-egress-clash/providers/sources.json'))
for name, url in sources.items():
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    trials = [('original', url)]
    if name == 'SSRDOG':
        query['t'] = 'clash'
        trials.append(('clash_format', urlunsplit(parts._replace(query=urlencode(query)))))
    if name == 'Flower_SS' and 'url' in query:
        trials.append(('origin', query['url']))
    for kind, candidate in trials:
        try:
            with urlopen(Request(candidate, headers={'User-Agent': 'clash.meta'}), timeout=15) as r:
                b = r.read(4_000_000)
                document = yaml.safe_load(b)
                count = len(document.get('proxies', [])) if isinstance(document, dict) else 0
                print(json.dumps({'provider': name, 'kind': kind, 'status': r.status,
                    'bytes': len(b), 'type': type(document).__name__, 'nodes': count}), flush=True)
                if count:
                    sources[name] = candidate
                    break
        except HTTPError as exc:
            print(json.dumps({'provider': name, 'kind': kind, 'http_status': exc.code}), flush=True)
        except Exception as exc:
            print(json.dumps({'provider': name, 'kind': kind, 'error': type(exc).__name__}), flush=True)
# Only replace sources that parsed successfully; same account, no provider switching.
with open('/opt/doxagent-egress-clash/providers/sources.json', 'w') as f:
    json.dump(sources, f)
