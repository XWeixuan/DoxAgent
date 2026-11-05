# Optional W3 Harness artifact

Build from the repository root:

```text
uv run python scripts/build_w3_codex_runtime.py
```

The fixed Codex 0.159.3 patch is built in Linux and exported here with its manifest
and upstream license. Debug symbols are archived under `symbols/` and excluded
from the runtime Docker build context. Generated binaries are not committed. `Dockerfile.v2` copies
this directory to `/opt/doxagent/codex-runtime`; without a generated artifact the
SDK continues using its bundled binary.

After mechanism and research-quality acceptance, set both in the service env file:

```text
DOXAGENT_CODEX_RUNTIME_BIN=/opt/doxagent/codex-runtime/codex
DOXAGENT_W3_TIME_AWARENESS_ENABLED=true
```

The feature defaults to off. It applies only to new W3 Trade Decision requests.
Disabling it does not cancel in-flight turns. Deployment and natural-flow acceptance
are separate from the local build; do not replay production Cases for validation.

The runtime directory must include the same-version `codex-code-mode-host`
executable beside `codex`. Adding the SDK helper directory to PATH alone does not
satisfy this sibling lookup. The build now exports both executables and records
the companion SHA256. Before research evaluation, verify a real code-mode call
and W3 MCP read; direct `exec_command` fixture calls do not cover this dependency.

For rollback, disable the feature and keep the patched binary's history filter.
Do not switch existing Threads containing reminder records back to an unpatched
binary: that binary cannot exclude the records from Operations input. Retire those
Threads through the existing lifecycle before reverting the binary itself.
