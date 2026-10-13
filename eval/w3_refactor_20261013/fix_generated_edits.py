from pathlib import Path
files = ['src/doxagent/persistent_runtime_v2/message_pin.py', 'src/doxagent/persistent_runtime_v2/worker_receipts.py', 'src/doxagent/v2_read/runtime.py', 'src/doxagent/v2_read/projectors.py', 'src/doxagent/v2_read/outbox.py', 'src/doxagent/api_v2/case_detail.py', 'src/doxagent/workflows/codex_document3/publication_v21.py', 'dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts', 'frontend/v2/src/core/runtime-case-export.ts']
for name in files:
    p = Path(name)
    s = p.read_text(encoding='utf-8')
    p.write_text(''.join(l[1:] if l.startswith('+') else l for l in s.splitlines(keepends=True)), encoding='utf-8')
