"""CAS-align Investing's public canary with its registered maintenance entry."""
import json
import runpy

request = runpy.run_path('/usr/local/sbin/doxagent-site-login-admin')['_request']
scope = next(s for s in request('GET', '/v2/scopes', timeout=15)
             if s['scope_id'] == 'source:investing_ticker_news')
url = 'https://www.investing.com/equities/intel-corp-news'
if scope['verification_plan']['crawler']['url'] != url:
    assert scope['revision'] == 1, 'scope changed; review before applying'
    revised = {**scope, 'verification_plan': {**scope['verification_plan'],
        'crawler': {'url': url, 'recipe_parameters': {'ticker': 'INTC'}}, 'body_url': None}}
    scope = request('POST', '/v2/scopes:apply',
                    {'spec': revised, 'expected_revision': scope['revision']}, timeout=15)
print(json.dumps(scope))
