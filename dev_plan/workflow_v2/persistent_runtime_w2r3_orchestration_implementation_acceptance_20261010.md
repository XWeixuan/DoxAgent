# 持久化运行 W2-R3 编排调整：实现与验证记录

日期：2026-10-10。状态：**本地实现及定向验收完成；未部署。**

依据：[可执行修改方案](persistent_runtime_w2r3_orchestration_implementation_plan_20261010.md)。未修改业务提示词；工作区中用户已有的提示词改动和其他任务工件均保留。

## 1. 已完成的行为

- W2-R2 使用新严格合同 `W2PolicyAssessmentResult`：`policy_hit`、`policy_related` 必填且仅接受 JSON boolean；HIT/RELATED 分别保存在 `hit_condition_ids`、`matched_condition_ids`。两者可同时为 true，归因可空，不增加互斥、全覆盖等业务阻塞校验。
- 原始 NEW 且 HIT 或 RELATED 时执行 W2-R3。业务输入只有 `w1_r2_reason`；结构化输出只有 `condition_results[{condition_ids,is_new}]`。限定引用区分不同 Policy 的同名条件，未知引用不授权路由，不引发语义重试。
- W1-R2→W1-R3、W2-R2→W2-R3 向提供方发送父响应 ID。新 W1-R3 payload 为 `{}`，缓存键也为空；R1/R2 保持原显式上下文请求方式。
- 父响应明确失效时，在现有调用预算内重放本 Case 冻结的父输入和成功输出；不重跑父轮。保存恢复原因、父 turn、冻结输入定位及 attempt 的上下文模式，进程重启后沿用恢复模式。
- 保存三种一轮标签：`FIRST_ROUND_TRADE`、`POLICY_RELATED_EVENT`、`SUSPECTED_DUPLICATE_HIT`，不覆盖原始 W1 verdict。正常、混合及 LOW 分支按方案矩阵执行；一轮不再直接创建交易记录或 BADCASE。
- W3 接收原始判定、完整 HIT/RELATED 结果、R3 结果与一轮标签。其旧 `original_w2` 继续保留 HIT 视图。W3 最终 OLD+HIT 仅归档，不新写 BADCASE。
- 统一 `POLICY_AUTO_FREEZE_ENABLED=False`，运行端、W3 缓存上下文、实时/CLOSED 输出及读投影遵循同一策略。历史 ledger 保留；Case/intent、日期、实时与 CLOSED、专家事实状态去重继续生效。
- 新 Case/执行包显式 revision=2，旧冻结包缺省 revision=1。旧 R2 wire model 保持不变；未落地的旧一轮交易/BADCASE 效果转 W3，已落地输出和 CLOSED 候选保留。
- 前端图移除 BADCASE 和一轮直达交易意图边，增加上下两个只通往 W3 的标签节点；三个标签均可筛选。明细和导出补 HIT/RELATED、W2-R3 状态/条件结果及 W1-R3 事实查看。RELATED 条件明确标为“相关条件”。
- 候选来源过滤在服务端分页前执行，并参与游标作用域；Policy 相关证据及导出仍限定 Case 固定版本。热路径包含 W2-R3，排除 W1-R3。

## 2. 修改位置

| 范围 | 文件 |
|---|---|
| 数据合同与路由 | `src/doxagent/persistent_runtime_v2/schema.py`、`router.py` |
| 编排、重试、旧效果恢复 | `persistent_runtime_v2/service.py`、`repository.py` |
| 提供方传输 | `persistent_runtime_v2/transport.py` |
| Prompt 装载及冻结包 | `persistent_runtime_v2/prompts.py`、`execution_bundle.py`、`factory.py`；只改装载代码 |
| W3 入口与冻结策略 | `persistent_runtime_v2/w3.py`、`trade_output.py`、新增 `policy_consumption.py` |
| API | `src/doxagent/api_v2/{runtime,case_detail,case_evidence}.py` |
| 投影、图、查询与耗时 | `src/doxagent/v2_read/{projectors,lifecycle,graph,repository,runtime}.py` |
| 公共合同与生成物 | `DOXAGENT_V2_API_CONTRACT.md`、`api_contract/doxagent-v2-api.types.ts`、后端/前端 wire schema、route contract |
| 前端 | `frontend/v2/src/pages/{runtime,runtime-case}.tsx`、`src/core/runtime-case-export.ts` |
| 验证 | 相关原有测试；新增 `tests/test_runtime_w2r3_orchestration.py`、`tests/v2_backend/test_w2r3_reads.py`、`frontend/v2/tests/browser/runtime-w2r3.spec.ts` |

本次无需数据库表迁移；新增内容使用既有 JSON、turn、冻结输入和效果存储。未新增执行器、消息通道、生命周期或全局锁。

## 3. 后端验证

相关回归组合 **121 passed**：

```powershell
uv run pytest tests/test_persistent_runtime_v2.py tests/test_persistent_runtime_w3.py tests/test_runtime_orchestration_foundation.py tests/test_runtime_orchestration_execution.py tests/test_runtime_orchestration_recovery.py tests/test_runtime_w2r3_orchestration.py tests/v2_backend/test_wire.py tests/v2_backend/test_policy_consumption_coverage.py tests/v2_backend/test_case_evidence.py tests/v2_backend/test_formal.py tests/v2_backend/test_projection.py tests/v2_backend/test_trade_outcomes.py tests/v2_backend/test_w2r3_reads.py -q --tb=short
```

随后补入“同一 Policy 不同 Case 可输出且同 Case 幂等”和“W2-R3 纳入、W1-R3 排除热路径”两个场景；追加组合 **23 passed**，其中 21 项与上组重合，因此合计 **123 个不同测试场景通过**。

```powershell
uv run pytest tests/test_runtime_w2r3_orchestration.py tests/v2_backend/test_case_evidence.py -q --tb=short
```

重点覆盖：完整 NEW 正常矩阵与 LOW 优先级、跨 Policy 同名 C1、严格布尔与空结果、实际请求开关、父失效恢复及重启、不重跑 R2、R3 成功后重算不重复调用、有限失败预算与后续 Case 隔离、旧冻结 schema、旧效果转 W3、旧 CLOSED 候选保留、实时与 CLOSED 不消费 Policy、固定水位明细、标签筛选和候选游标隔离。W1-R3 测试直接渲染实际请求，覆盖空 payload 与缓存键的一致性。

## 4. 前端与静态验证

- `pnpm --dir frontend/v2 test`：**12 个测试文件、57 passed**。
- `pnpm --dir frontend/v2 typecheck`：通过。
- `pnpm --dir frontend/v2 exec vite build`：通过，保留现有大 chunk 提示。
- 后端合同生成成功；前端 schema 已生成，实际 SHA 与当前公共 TypeScript 合同一致。
- 本机多次运行 `pnpm build` 的 schema 覆盖写入步骤遇到 Node 文件打开 `UNKNOWN`；已有一次整条 build 成功。最终版本通过已生成且 SHA 一致的 schema、单独 typecheck 与 Vite build 验证。未为此扩改生成器或依赖。
- 核心改动 Python 文件及新测试/合成探针 Ruff 检查通过。

浏览器验收 **6 passed**：

```powershell
pnpm --dir frontend/v2 exec playwright test tests/browser/runtime-trade.spec.ts tests/browser/runtime-w2r3.spec.ts
```

实际启动 Chromium，在 **1262px、1559px** 验证图布局、节点入口、选择导出、R3 条件与聚合展示、W1-R3 展开和候选、无页面横向溢出，以及跳过/无召回不伪装为 OLD。已查看图及明细截图；图内两个新增节点位于 W3 上下，边不穿过无关卡片。

截图副本见 [验证工件目录](../../eval/persistent_runtime_v2/w2r3_orchestration_20261010/)。

## 5. 当前百炼 API 实测

[探针](../../eval/persistent_runtime_v2/w2r3_orchestration_20261010/probe.py)与[脱敏回执](../../eval/persistent_runtime_v2/w2r3_orchestration_20261010/receipts.json)。只发送合成 ALPHA 文章、Policy 和条件，不读取生产新闻、不写生产 Runtime 状态，不调用 W3 Worker。

使用当前配置的百炼接口和 `qwen3.8-flash`，共 **10 次实际请求**，验证通过：

1. W1-R2→W1-R3：空 `{}` 输入仍可继承父轮文章和新增事实边界，抽取 qualification fact。
2. W2-R2→W2-R3：新增输入只有 W1 reason，HIT 新事实 TRUE、RELATED 新事实 TRUE、旧事实 FALSE 均符合预期，返回正确的限定 Condition 引用。
3. 无效父 ID 返回可识别的 `provider_previous_response_unavailable`。
4. 父输入/输出作为 user/assistant 消息显式重放后仍可结构化完成。

这证明提供方继承与恢复请求方式可执行；不是生产业务提示词的事实判断准确率验收，也不是远端端到端运行验收。

## 6. 上线边界

- 本轮未部署、未切生产读模型、未处理生产在途任务，未提交 Git commit。
- 上线前按既有流程发布用户调整后的提示词及 revision=2 执行包；历史包保持原 revision。新 R2/R3 提示词需符合已实现合同。
- 使用既有读模型 shadow rebuild / switch 流程重建历史图贡献及 Policy effectiveness；不清空消费 ledger、不覆盖已产交易或 CLOSED 候选。重投影代码已覆盖旧边撤回，但本轮未在远端执行重建。
- **后续 W3 契约尚未实施：当前最终 NEW+HIT 仍按 Policy 方向落地交易，不能宣称 W3 已具备独立交易否决权。** 本阶段已经把一轮命中全部接到 W3，后续可使用当前传入的完整证据调整该契约。

根目录 `changelog` 已追加本轮变更和验证摘要。
