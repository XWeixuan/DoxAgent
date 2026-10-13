from pathlib import Path

def edit(name, old, new, count=1):
    path = Path(name)
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise ValueError(f'Anchor missing: {name}: {old[:80]}')
    path.write_text(text.replace(old, new, count), encoding='utf-8')

edit('src/doxagent/persistent_runtime_v2/message_pin.py',
     'snapshot.get("source_name") or resolve_source_name(url=value.get("url"))',
     'snapshot.get("source_name") or (resolve_source_name(url=value.get("url"))\n+            if value.get("member_count", 1) <= 1 else None)')
edit('src/doxagent/persistent_runtime_v2/worker_receipts.py',
     '"requested_model": dispatched_request.model,',
     '"requested_model": dispatched_request.model,\n+                "task_phase": request.task_phase,')
edit('src/doxagent/v2_read/runtime.py', '"round": "AGENT",',
     '"round": "AGENT",\n+            "phase": value.get("task_phase"),')
edit('dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts',
     'export interface MessageSummary {',
     'export interface MessageSummary {\n+  source_name?: string | null;')
edit('dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts',
     'export interface CaseSummary {',
     'export interface CaseSummary {\n+  source_name?: string | null;\n+  w3_operations_status?: string | null;')
edit('dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts',
     'export interface ModelAttempt {',
     'export interface ModelAttempt {\n+  phase?: "w3_template" | "trade_decision" | "post_decision_operations" | null;')
edit('dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts',
     'export interface W3Detail {',
     '''export interface W3TradeDecision {
  decision: "NO_TRADE" | "POLICY_TRADE" | "EXPERT_TRADE";
  reason: string;
  policy_trade: {policy_id: Id; condition_id: Id} | null;
  expert_trade: {direction: "LONG" | "SHORT"; prior_expectation: string; expectation_delta: string} | null;
}
export interface W3Detail {
  decision?: W3TradeDecision | null;
  operations_result?: JsonObject | null;
  operations_status?: string | null;
  operations_error?: string | null;''')
edit('dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts',
     'export interface CaseDetail {',
     'export interface CaseDetail {\n+  w3_state?: {watch: JsonObject | null; pins: JsonObject | null; receipt: JsonObject | null};')
edit('src/doxagent/v2_read/projectors.py',
     'final = value.get("w3_result")',
     'final = value.get("w3_operations_result") or value.get("w3_result")\n+        decision = value.get("w3_trade_decision")')
edit('src/doxagent/v2_read/projectors.py',
     '"source": self.source_label(source),',
     '"source": self.source_label(source),\n+            "source_name": source["snapshot"].get("source_name"),\n+            "w3_operations_status": value.get("w3_operations_status"),')
edit('src/doxagent/v2_read/projectors.py',
     '"w3_status": "RESOLVED" if final else "PENDING" if pending else None,',
     '"w3_status": "RESOLVED" if final or decision else "PENDING" if pending else None,')
edit('src/doxagent/v2_read/projectors.py',
     'result = (value.get("w3_result") or {}).get(name)',
     'result = (value.get("w3_operations_result") or value.get("w3_result") or {}).get(name)')
edit('src/doxagent/v2_read/projectors.py',
     'records.append(\n            {"kind": "case_reasoning",',
     'if decision:\n            reasons["w3_expert_trade"] = self.store.put_content(ticker, decision["reason"])\n        records.append(\n            {"kind": "case_reasoning",')
edit('src/doxagent/v2_read/projectors.py',
     'source=self.source_label(message),',
     'source=self.source_label(message),\n+            source_name=message.get("source_name"),')
edit('src/doxagent/api_v2/case_detail.py',
     '["version_pin", "w2_round1", "w2_final", "w3_result"],',
     '["version_pin", "w2_round1", "w2_final", "w3_result", "w3_operations_result", "w3_trade_decision"],')
edit('src/doxagent/api_v2/case_detail.py',
     'if policy_id not in referenced:',
     '''referenced.update(((case.get("w3_operations_result") or {}).get("policy") or {}).get("policy_ids", []))
        selected = (case.get("w3_trade_decision") or {}).get("policy_trade")
        if selected:
            referenced.add(selected["policy_id"])
        if policy_id not in referenced:''')
edit('src/doxagent/api_v2/case_detail.py',
     '"w3_result",\n                "frozen_inputs.policy_watch_followup",',
     '"w3_result",\n                "w3_trade_decision",\n                "w3_operations_result",\n                "w3_operations_status",\n                "w3_operations_error",\n                "frozen_inputs.policy_watch_followup",')
edit('src/doxagent/api_v2/case_detail.py',
     'with store.connect() as db:\n            counts = {',
     '''if case.get("w3_trade_decision"):
            decision = case["w3_trade_decision"]
            ops = case.get("w3_operations_result") or {}
            expert = decision.get("expert_trade") or {}
            if third is None:
                route = evidence.fields("native:runtime_v2_w3_cases", case_id, ["mode"])
                third = dict(mode=(route or {}).get("mode", "UNCOVERED_NEW"))
            third.update(
                status="RESOLVED", decision=decision,
                operations_result=case.get("w3_operations_result"),
                operations_status=case.get("w3_operations_status"),
                operations_error=case.get("w3_operations_error"),
                novelty=available(ops["novelty"]["result"]) if ops else missing(),
                references=references(ops["novelty"]) if ops else [],
                policy_hit=available(bool(ops["policy"]["policy_ids"])) if ops else missing(),
                policies=policies(ops["policy"]) if ops else [],
                expert_trade_evaluated=available(decision["decision"] == "EXPERT_TRADE"),
                expert_trade=available(decision["decision"] == "EXPERT_TRADE"),
                direction=available(expert["direction"]) if expert else missing(),
                prior_expectation=available(expert["prior_expectation"]) if expert else missing(),
                expectation_delta=available(expert["expectation_delta"]) if expert else missing(),
                timing=interval("W3"), attempts=page("attempt", "W3"),
                reasoning={k: resource(reasons.get("w3_" + k))
                           for k in ("novelty", "policy", "expert_trade")},
            )
        with store.connect() as db:
            counts = {''')
edit('src/doxagent/api_v2/case_detail.py',
     'return app.state.respond(\n            request,\n            "CaseDetail",',
     '''if case.get("w3_trade_decision"):
            state = store.get("native:runtime_values", ticker, "w3_operations_input:" + case_id, seq)
            state = state or store.get("native:runtime_values", ticker, "w3_global_input:" + case_id, seq) or {}
            value["w3_state"] = dict(
                watch=state.get("watch"), pins=state.get("pins"),
                receipt=store.get("native:runtime_values", ticker, "w3_operations_applied:" + case_id, seq),
            )
            value["policy_watch"]["actions"] = (case.get("w3_operations_result") or {}).get("watch_actions", [])
        return app.state.respond(
            request,
            "CaseDetail",''')
edit('src/doxagent/v2_read/outbox.py',
     "'policy_watch','policy_watch_applied','policy_watch_validation')",
     "'policy_watch','policy_watch_applied','policy_watch_validation',"
     "'message_pins','w3_operations_applied','w3_global_input','w3_operations_input')")
edit('src/doxagent/v2_read/outbox.py',
     '"policy_watch_validation",',
     '"policy_watch_validation",\n                    "message_pins",\n                    "w3_operations_applied",\n                    "w3_global_input",\n                    "w3_operations_input",')
