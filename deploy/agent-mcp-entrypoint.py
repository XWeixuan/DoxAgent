"""Pass the tunnel credential through environment only, never CLI/config literals."""

import os
import sys
from pathlib import Path

path = os.environ.get("DOXAGENT_TUNNEL_KEY_FILE", "/run/secrets/agent_tunnel_key")
if Path(path).is_file():
    os.environ["CONTROL_PLANE_API_KEY"] = Path(path).read_text(encoding="utf-8").strip()
os.execvp("tunnel-client", ["tunnel-client", *sys.argv[1:]])
