from __future__ import annotations

from pathlib import Path
import subprocess

keys = {
    "STOCKTWITS_RAPIDAPI_KEY",
    "STOCKTWITS_RAPIDAPI_FALLBACK_KEY",
    "STOCKTWITS_RAPIDAPI_FALLBACK_KEYS",
    "STOCKTWITS_RAPIDAPI_HOST",
    "STOCKTWITS_RAPIDAPI_BASE_URL",
}

def fp(value: str) -> str:
    if len(value) > 8:
        return value[:4] + "..." + value[-4:]
    if value:
        return "<short-set>"
    return "<empty>"

p = Path("/root/doxagent/.env")
if not p.exists():
    print("NO_REMOTE_ENV")
else:
    seen = False
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in keys:
            seen = True
            print(f"{key}={fp(value)}")
    if not seen:
        print("NO_STOCKTWITS_RAPIDAPI_ENV_KEYS")

code = (
    "from doxagent.monitoring.service import MonitoringBusService; "
    "svc=MonitoringBusService.from_settings(); "
    "source=svc.repository.get_source('stocktwits_messages'); "
    "print('source_mode', source.config.get('mode') if source else None); "
    "print('endpoint_kind', source.endpoint_kind.value if source else None); "
    "print('enabled', source.enabled if source else None); "
    "print('is_durable', source.config.get('mode') == 'durable_polling' if source else None)"
)
try:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "monitoring-poller", "python", "-c", code],
        cwd="/root/doxagent",
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    print("CONTAINER_CHECK_RC", result.returncode)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print("STDERR", result.stderr.strip()[:1000])
except Exception as exc:
    print("CONTAINER_CHECK_ERROR", type(exc).__name__, str(exc)[:500])
