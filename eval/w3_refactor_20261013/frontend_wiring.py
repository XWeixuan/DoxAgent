exec(__import__('pathlib').Path('eval/w3_refactor_20261013/finish.py').read_text(encoding='utf-8-sig').split("edit('src/")[0])
edit('frontend/v2/src/pages/runtime-case.tsx',
     '{d.summary.source.name} ·', '{d.summary.source_name ?? d.summary.source.name} ·')
edit('frontend/v2/src/pages/runtime-case.tsx',
     '{d.policy_execution ? "Policy 执行判定" : "专家交易判定"}',
     '{d.w3.data.decision ? "交易决策" : d.policy_execution ? "Policy 执行判定" : "专家交易判定"}')
edit('frontend/v2/src/pages/runtime-case.tsx',
     '{d.policy_execution ? (',
     '''{d.w3.data.decision ? (
                    <>
                      {d.w3.data.decision.decision}
                      {d.w3.data.decision.policy_trade && ` · ${d.w3.data.decision.policy_trade.policy_id}/${d.w3.data.decision.policy_trade.condition_id}`}
                      {d.w3.data.decision.expert_trade && ` · ${d.w3.data.decision.expert_trade.direction}`}
                      {" · "}{d.w3.data.decision.reason}
                    </>
                  ) : d.policy_execution ? (''')
edit('frontend/v2/src/pages/runtime-case.tsx',
     '<dt>原有预期</dt>',
     '''{d.w3.data.decision && <>
                  <dt>后置操作</dt>
                  <dd>{d.w3.data.operations_status ?? "PENDING"}{d.w3.data.operations_error && ` · ${d.w3.data.operations_error}`}</dd>
                </>}
                <dt>原有预期</dt>''')
edit('frontend/v2/src/pages/runtime-case.tsx',
     '{d.candidate_count.state === "AVAILABLE" &&',
     '''{d.w3_state && <section>
            <h3>WATCH / PIN</h3>
            <dl>
              <dt>本阶段可见状态</dt>
              <dd><pre>{JSON.stringify({watch: d.w3_state.watch, pins: d.w3_state.pins}, null, 2)}</pre></dd>
              <dt>动作接纳结果</dt>
              <dd><pre>{JSON.stringify(d.w3_state.receipt, null, 2)}</pre></dd>
            </dl>
          </section>}
          {d.candidate_count.state === "AVAILABLE" &&''')
edit('frontend/v2/src/pages/runtime-case.tsx',
     '{a.node_id} · {a.round}',
     '{a.node_id} · {a.phase === "trade_decision" ? "交易决策" : a.phase === "post_decision_operations" ? "后置操作" : a.round}')
edit('frontend/v2/src/pages/message-bus.tsx', '{m.source.name}', '{m.source_name ?? m.source.name}')
edit('frontend/v2/src/pages/runtime.tsx', '{c.source.name}', '{c.source_name ?? c.source.name}', 2)
edit('frontend/v2/src/core/runtime-case-export.ts',
     '研判模式: third?.mode ?? null,',
     '研判模式: third?.mode ?? null,\n      交易决策: third?.decision ?? null,\n      后置操作状态: third?.operations_status ?? null,\n      后置操作错误: third?.operations_error ?? null,\n      后置操作结果: third?.operations_result ?? null,\n      WATCH_PIN: detail.w3_state ?? null,')
edit('frontend/v2/src/core/runtime-case-export.ts', '来源: s.source.name,',
     '来源: s.source_name ?? s.source.name,')
