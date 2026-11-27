"""Use the existing locked immutable-overlay release owner; preserve other roles."""
import hashlib
import json
import subprocess
import sys

import dynamic_ownership_release as release

release.ROLES = ["v2-api", "v2-message-collector", "v2-message-bus", "v2-content-enrichment", "v2-o4", "v2-web"]
release.FILES = ["src/doxagent/" + path for path in (
    "api_v2/bus.py", "api_v2/wire_schema.json", "v2_control/bindings.py",
    "message_bus_v2/schema.py", "message_bus_v2/service.py", "message_bus_v2/live.py",
    "message_bus_v2/live_repository.py", "message_bus_v2/distribution.py",
    "message_bus_v2/relevance.py", "message_bus_v2/extra_relevance.py",
    "ticker_initialization/configuration.py",
    "message_bus_v2/biopharma_parsers.py",
)]
release.COPY_ROOTS = ("src/doxagent",)


if __name__ == "__main__":
    commit = sys.argv[sys.argv.index("--commit") + 1]
    # All overwritten pre-existing modules must still equal the surveyed live
    # baseline or this release's bytes. Do not overwrite a concurrent hotfix.
    expected = {}
    for path in release.FILES:
        exists = release.run(["sudo", "-u", "ubuntu", "git", "-C", str(release.REPO),
                              "ls-tree", "--name-only", "0e194c7fb", "--", path])
        expected[path] = hashlib.sha256(release.git("0e194c7fb", path)).hexdigest() if exists else None
    selected = {p: hashlib.sha256(release.git(commit, p)).hexdigest() for p in release.FILES}
    code = ("import hashlib,json;from pathlib import Path;paths=" + repr(release.FILES) +
            ";print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() "
            "if Path('/app',p).is_file() else None for p in paths}))")
    for role in release.ROLES:
        if role == "v2-web":
            continue
        actual = json.loads(release.run(["docker", "exec", "doxagent-v2-"+role+"-1", "python", "-c", code]))
        reviewed = {p:hashlib.sha256(release.git("7856e0d41",p)).hexdigest()
                    for p in release.FILES if p.endswith(("/live.py", "/biopharma_parsers.py"))}
        differences = [p for p in release.FILES if actual[p] not in {expected[p], selected[p], reviewed.get(p)}]
        if differences:
            raise RuntimeError("live baseline changed: " + role + " " + repr(differences))
    release.main()
