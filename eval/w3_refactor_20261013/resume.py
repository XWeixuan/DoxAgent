from pathlib import Path
s = Path('eval/w3_refactor_20261013/finish.py').read_text(encoding='utf-8-sig')
s = s.replace('export interface MessageSummary {', 'export interface MessageSummary extends MessageKey {')
exec(s[:s.index("edit('src/")] + s[s.index("edit('dev_plan/"):])
