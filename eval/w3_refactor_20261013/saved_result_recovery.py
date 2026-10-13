from pathlib import Path
p = Path('src/doxagent/persistent_runtime_v2/w3_execution.py')
s = p.read_text(encoding='utf-8')
start = s.index('        membership = freeze_history(', s.index('    def trade('))
end = s.index('        if stage["result_json"] is None:\n', start)
block = s[start:end]
s = s[:start] + '        if stage["result_json"] is None:\n' + ''.join('    ' + l if l.strip() else l for l in block.splitlines(keepends=True)) + s[end + len('        if stage["result_json"] is None:\n'):]
s = s.replace('            decision = W3TradeDecision.model_validate(stage["result_json"])',
              '            decision = W3TradeDecision.model_validate(stage["result_json"])\n            record = self.registry.owned(case.case_id) or {"thread_id": stage["thread_id"]}', 1)
s = s.replace('        context = self.agent.prepare_case(case)\n        state =', '        state =', 1)
ops = s.index('    def operations(')
i = s.index('        if stage["result_json"] is None:\n            result, job', ops)
s = s[:i] + s[i:].replace('        if stage["result_json"] is None:\n            result, job', '        if stage["result_json"] is None:\n            context = self.agent.prepare_case(case)\n            result, job', 1)
p.write_text(s, encoding='utf-8')
