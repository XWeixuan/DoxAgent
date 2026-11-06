"""Only bounded, structured control-stage log lines; never exception text/URLs."""
import json
import subprocess

r = subprocess.run(["docker", "logs", "--since", "2026-11-06T18:45:38Z", "--tail", "2500",
                    "doxagent-v2-v2-site-access-1"], capture_output=True, text=True)
lines = [line[:1500] for line in (r.stdout + r.stderr).splitlines()
         if ("CDP attach " in line and any(k in line for k in ("reuters-backup", "digitimes-de-1")))
         or "identity_operation_failed" in line]
print(json.dumps({"lines": lines[-30:]}, indent=2))
