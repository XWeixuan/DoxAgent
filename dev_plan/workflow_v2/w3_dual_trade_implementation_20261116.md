# W3 双任务与统一模型开发记录

2026-11-16。本轮为本地开发，没有切换生产配置、收费模型调用或生产发布。工作区已有其他对话的提示词、回测和服务修改，均保留；未进行包含这些修改的混合提交。

## 已落地

- 新进入 W3 的 Case 统一冻结 `gpt-6-luna / max`。旧已冻结 profile、原 Thread/Job 保留，未批量重跑。公共准备不再常态预热 Sol。
- Trade 开 fast；Operations 关 fast，其优先级高于显式回测 tier 覆盖。
- `single` 默认不变。首次路由冻结 `w3_trade_mode`；已存在的旧 Effect 缺字段时恢复为 single。之后切换配置不影响在途。
- 双模式在一个原 Trade Effect 内并发运行 A/B：独立 record、workspace、Thread、Worker Job；共用冻结上下文和最终 prompt/Schema，实际请求字节一致。SDK `task_phase` 仍为 `trade_decision`。
- 两份结果各自校验/保存；B 失败只恢复 B，A 不重跑。两个合法结果齐全才聚合；技术失败不作为 NO_TRADE。保留现有租约、Future 续接、原 Job 查询及 Provider Pause 行为。
- 九种方向组合按方案处理。LONG/SHORT 对冲为明确标记来源的编排 NO_TRADE，原结果不改写；只有一个最终业务提交和一个 Operations Effect。
- Operations 跟随正统 Thread；未选中 record 在 Trade 业务提交后释放 owner，沿既有生命周期回收。
- ACK 前保存分支原始输出、结构化结果、时间、Job/Thread、有效请求参数/输入 hash、已有 telemetry 的工具事件及 usage。无需复制完整工作区或保留双份常驻进程。
- `inspect-case` 可读取两分支 stage；Read/API 在既有 `w3_state.receipt.trade_consensus` 提供有界摘要，Agent Schema 与 wire Schema 不扩张。
- 完整输入及 decision-only 回测支持 `trade_mode`，加入运行指纹；结果文件分别保留两分支与最终结果。旧 v4 不支持双模式，明确拒绝而非运行后才失败。

## 使用方式

在正确 Runtime 数据库配置下执行：

```powershell
python -m doxagent.persistent_runtime_v2.cli w3-trade-mode show
python -m doxagent.persistent_runtime_v2.cli w3-trade-mode set --mode dual_consensus
python -m doxagent.persistent_runtime_v2.cli w3-trade-mode set --mode single
```

上述 set 只影响之后首次进入 W3 的 Case，不取消在途任务。本轮没有执行 set。

回测 `run` 参数新增 `--trade-mode single|dual_consensus`；API `ReplayRunner` 和 `DecisionReplayRunner` 使用同名 `trade_mode` 参数。resume 从原 manifest 恢复，不能静默换模式。

## 验证与边界

回归证据在 `eval/w3_dual_trade_20261116/`。新增测试覆盖九种方向、开关冻结、唯一交易/运营、真实 Runner 的两份相同 prompt/不同 workspace、并行启动、单份失败恢复、聚合保存后提交失败恢复和审计摘要保留。相关多组回归有重叠，不累加为独立样本。

新增专项 19 项；最终核心组合 100 项通过，含 fast 显式覆盖的专项/SDK 组合 44 项通过。较早的扩展组合分别 152、121 项通过（与最终组合重叠）。152 项组合单列排除此前已在原始基线复现的旧回测时效断言，不放宽其时效校验。静态未定义名称检查与本轮独立改动文件的 whitespace 检查通过。并行开发工作区整体存在其他文件的既有改动，不宣称整仓清洁。

未进行生产流量、峰值内存/I/O、真实额度成本与非 SDK P95 验收，因此不宣称服务器足以持续运行双模式，或 P95≤5秒已获生产验证。双模式等待较慢模型任务的代价仍然存在；上线后须按方案先 single 基线、再短窗 dual，默认保持 single。

提示词目录与资产路径的并行开发仍由相应对话负责。本轮未改提示词、Agent 输出契约或激活 v6 Bundle。双模式兼容统一方向契约 v5/v6，保留各自既有 Operations 职责。
