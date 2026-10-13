"""Close only captured, expired business allocation markers; preserve other tabs."""
import json
import time
import urllib.request
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
snapshot = json.loads(r'''{"captured_at":1791832507.0888677,"targets":[{"port":9222,"id":"4A68AD74B77365DC59C095DE69A69B64","url":"about:blank#doxagent-edfaafd63b744334b53c243859bac367"},{"port":9222,"id":"7597562B839DFBF5AE9518A9FFFD2767","url":"about:blank#doxagent-7837bfa9ce2a4c6cad0b63787e0b82b9"},{"port":9222,"id":"A4D394370BB41B166C50535F78FEC9FD","url":"about:blank#doxagent-7647d32baee844f2bcfaf5c908cab480"},{"port":9226,"id":"62E48830FEE71AEC235101D1C8A29C7F","url":"about:blank#doxagent-8dbddb34da70416bb2e558fad28a99ff"},{"port":9226,"id":"37ADE9E258FA46D7B7BEA9FCD46A134C","url":"about:blank#doxagent-c6048c738dbe4683a3c1ed344671f895"},{"port":9227,"id":"8EA968FE80CB06EDDB3E85D8116EC92B","url":"about:blank#doxagent-bfb43cbceab740ceb8aa9227ad8a7925"},{"port":9227,"id":"F4F68572C56700071596287A49CDA01C","url":"about:blank#doxagent-16494ed26a9745a49fe12263e457ad98"},{"port":9227,"id":"C692B1353497DA0A91F15BFCF962E31C","url":"about:blank#doxagent-e56f7a6f509947aa9e6bae6f0ab0c7d6"},{"port":9227,"id":"19B615FA15ADF6F6395E8F33D4D39825","url":"about:blank#doxagent-e4914a32631449d0b84a0f77accd4311"},{"port":9227,"id":"202F76ABF752C288E73DA346CA60B32A","url":"about:blank#doxagent-cd8c6cdbf62c49869ad9e0c55ba22d15"},{"port":9227,"id":"6B299FBE8FCD626DC47381AD014FD4B2","url":"about:blank#doxagent-d5b670eaa5504af78f1b316cca94798e"}]}''')
assert time.time() - snapshot['captured_at'] > 60
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
settings = DoxAgentSettings()
headers = {'Authorization': 'Bearer ' + read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)}
request = urllib.request.Request('http://127.0.0.1:8011/v1/profiles', headers=headers)
profiles = json.load(opener.open(request, timeout=10))
protected = {'dowjones-main', 'digitimes-nl-1', 'public-markets-nl', 'yahoo-main'}
assert not any(p.get('maintenance_session_id') for p in profiles if p['profile_id'] in protected)
closed = []
for target in snapshot['targets']:
    base = 'http://127.0.0.1:' + str(target['port'])
    current = json.load(opener.open(base + '/json/list', timeout=5))
    match = next((p for p in current if p['id'] == target['id'] and p['url'] == target['url']), None)
    if match is None:
        continue
    assert target['url'].startswith('about:blank#doxagent-')
    opener.open(base + '/json/close/' + target['id'], timeout=5).read()
    closed.append(target['id'])
print(json.dumps({'closed_expired_business_allocations': closed, 'other_pages_preserved': True}), flush=True)

