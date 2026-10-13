import importlib.util
import inspect
import json
import os
import socket
import urllib.request

result = {"ibapi_installed": importlib.util.find_spec("ibapi") is not None}
try:
    from doxagent.settings import DoxAgentSettings
    s = DoxAgentSettings()
    result["ibkr"] = {"host": s.ibkr_tws_host, "port": s.ibkr_tws_port,
                      "client_id": s.ibkr_tws_client_id}
    try:
        with socket.create_connection((s.ibkr_tws_host, s.ibkr_tws_port), timeout=4):
            result["ibkr"]["tcp_reachable"] = True
    except Exception as exc:
        result["ibkr"]["tcp_reachable"] = False
        result["ibkr"]["tcp_error_type"] = type(exc).__name__
except Exception as exc:
    result["settings_error_type"] = type(exc).__name__
token = os.getenv("DOXAGENT_CODEX_WORKER_BEARER_TOKEN", "")
for endpoint in ("capabilities", "readiness"):
    try:
        request = urllib.request.Request("http://127.0.0.1:8791/v1/" + endpoint,
                                         headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=35) as response:
            value = json.load(response)
        result[endpoint] = {key: value[key] for key in (
            "ready", "provider_probe", "provider_verified", "authenticated", "models", "sdk"
        ) if key in value}
    except Exception as exc:
        result[endpoint] = {"error_type": type(exc).__name__, "status": getattr(exc, "code", None)}
print(json.dumps(result))
