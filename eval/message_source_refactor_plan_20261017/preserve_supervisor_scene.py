"""Pin the running supervisor while its newly retained native Challenge is live."""

import json
import subprocess
from pathlib import Path

root = Path("/var/lib/doxagent/source-isolation-release-20261018")
manifest = json.loads((root / "build.json").read_text())
current = json.loads(
    subprocess.check_output(["docker", "inspect", "doxagent-v2-v2-chrome-supervisor-1"])
)[0]
desired = manifest["images"]["supervisor"]
if desired["tag"] != current["Config"]["Image"]:
    manifest["prepared_supervisor_followup"] = desired
    manifest["images"]["supervisor"] = {
        "tag": current["Config"]["Image"],
        "image": current["Image"],
        "source_commit": current["Config"]["Labels"]["org.opencontainers.image.revision"],
        "pin_reason": "preserve newly retained Reuters fallback Challenge original target",
    }
    (root / "build.json").write_text(json.dumps(manifest, indent=2))
print(
    json.dumps(
        {
            "pinned": manifest["images"]["supervisor"]["tag"],
            "prepared": manifest.get("prepared_supervisor_followup", {}).get("tag"),
        }
    )
)
