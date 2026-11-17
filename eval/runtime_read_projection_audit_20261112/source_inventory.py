import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import doxagent.v2_read

root = Path(doxagent.v2_read.__file__).parent
names = ["projector.py", "repository.py", "projectors.py", "formal.py", "outbox.py", "source_lifecycle.py", "cli.py", "maintenance.py", "native_content.py", "content_codec.py", "runtime.py", "graph.py", "decision_metrics.py", "lifecycle.py"]
out = {"at": datetime.now(timezone.utc).isoformat(), "root": str(root), "files": {}}
for name in names:
    path = root / name
    if path.exists():
        raw = path.read_bytes()
        out["files"][name] = {"sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode()}
print(json.dumps(out))
