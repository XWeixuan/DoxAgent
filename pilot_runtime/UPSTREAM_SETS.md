# Pilot manual upstream sets

Create one directory per reusable input set under `D:\DoxAgentPilot\upstream_sets`.
Paste UTF-8 Markdown reports or complete C4 `NodeOutput` JSON files using only
these fixed names. The same C4 file is intentionally reusable across all C4
turns and downstream nodes:

| Target node | Recognized files |
| --- | --- |
| `c1` | `c4_finalization.json.json` |
| `c3` | `c4_finalization.json.json` |
| `c4_pre_scan` | `c4_finalization.json.json` |
| `c4_enrichment` | `c4_finalization.json.json`, `c1.md`, `c3.md` |
| `c4_finalization` | `c4_finalization.json.json` |
| `o4_a` | `c4_finalization.json.json`, `c1.md`, `c3.md` |

Example:

```powershell
python D:\DoxAgentPilot\runtime\prepare_case.py `
  --source-run <run-id> `
  --node o4_a `
  --case-id <new-case-id> `
  --profile quality `
  --upstream-dir D:\DoxAgentPilot\upstream_sets\<set-id>
```

New cases receive a 10-year Data MCP capability by default. To rotate an
existing case without changing its sealed inputs, run
`python D:\DoxAgentPilot\runtime\refresh_capability.py --case <case-root>`.

Rules:

- Paste upstream material before generating the case. Never edit a generated
  case's `attempts/<attempt-id>/input/` directory.
- Markdown files are contextual reports. C4 JSON files must validate as the
  current `NodeOutput` schema.
- Old `【cite:O#】` aliases are downgraded during import. The downstream node
  must reverify any fact it uses through evidence available to its own attempt.
- Only files recognized for the selected node are injected. Other files in the
  shared set remain available for later downstream cases but are not exposed.
- Empty, non-UTF-8, invalid JSON, or files larger than 2 MiB are rejected.
- Imported contents and hashes become immutable attempt inputs and are included
  in `input_sha256`, `bundle.json`, and `case_manifest.json`.
