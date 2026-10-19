"""Read-only owner queue diagnostics; credentials remain inside the root bridge."""
import json
import runpy

request = runpy.run_path('/usr/local/sbin/doxagent-site-login-admin')['_request']
print(json.dumps(request('GET', '/v1/diagnostics/access', timeout=15)))
