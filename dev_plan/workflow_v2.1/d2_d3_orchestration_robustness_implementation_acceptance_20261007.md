# D2 编排鲁棒性与 D2/D3 索引实施验收

日期：2026-10-07。依据 `d2_d3_orchestration_robustness_repair_plan_20261007.md`，本轮为本地代码实施与离线验收。

## 落地结果

| 修复包 | 实际行为 |
|---|---|
| A：交付权威 | 正式 D2、Pilot 共用 acceptance；优先当前 attempt 的业务文件，其次旧式 SDK 完整回复，再局部恢复或可靠上游回退。SDK FAILED 不再自动丢弃已写好的文件。v2.1 SDK 使用小型 completion_path/status 回执；完整业务 schema 保留在 input。v2 保持旧 SDK schema。 |
| B：接纳与恢复 | 重复对象稳定合并；历史 Late Addition 保留首次发现阶段；Finalization 累积旧处置；错误 Value 局部隔离或沿用同身份有效上游值。缺决策、非法 MERGE、未闭合方向保存在诊断中，不能伪造 DEEPEN、closure 或数值。O1 成功快照损坏/缺失沿用可靠上游，不为内容问题重跑研究；O0 坏快照隔离后重新准备该分支，不能终止健康分支。 |
| B：Discovery | 保持单节点、单研究 Turn；MCP 与本地 CLI 共用真实冻结服务。无工具提交时控制器回收真实 Scan，或生成明确带降级诊断的空 Scan；未在冻结时绑定的 Selection 保留原件并降级为 pending，不制造假 SHA。 |
| C：工具启动 | D2 的 Data/Discovery MCP 均非 required；每轮明确重绑定，非 Discovery 请求包含完整 transport 定义并禁用 commit server。保留 deny_all 与单 commit 工具 approve，其他 workflow 的工具策略不扩改。 |
| D：上下文访问 | D2 正式/Pilot、D3 v2 与 v2.1 owner workspace 共用派生索引。全文、原 context 保留；顶层字段、报告章节、Unit/子集合、累计记录可定位，正文与目录分页，默认页上限 6,000 字符。标准库 reader 和 PowerShell 页文件读取入口均显式 UTF-8。 |
| E：可选资产 | Narrative/Event 各 15 秒总预算，并行读取；最多一次瞬时重试，HTTP 调用带剩余预算内的实际 timeout。Event 同步读取移出事件循环。Pilot 忽略旧负缓存，仅复用身份/时点/摘要有效的 AVAILABLE；接入 Published Event Library Reader，支持 pinned version。 |

Pilot 研究接纳与独立 review 分离：review 自动重试一次，仍失败则记录 review_unavailable，已接纳研究可供下游使用，不重做研究；输出 digest 防修改仍保留。

坏报告的校验只决定是否使用该域正文：不可读/摘要不符的域隔离，其他域继续；全局 run/ticker 身份、lease、能力权限与未授权写入仍不能冒充合法提交。

## 工件与读取合同

- 模型业务原件：当前 attempt 的 `output/completion.json`，已有文件不由规范化原地覆盖。
- Pilot 接纳快照：`output/accepted.json`；诊断随 `output/orchestration_diagnostics.json` 传给下游。
- 正式接纳快照：既有 `artifacts/document2/turns/` 工件；派生恢复位于 `artifacts/document2/accepted/`。
- 接纳元数据：attempt `audit/acceptance.json` 与正式 `artifacts/document2/acceptance/<短摘要>.json`；记录 source、原文摘要、修复/隔离/fallback。SDK 原回复另存 audit。
- Discovery CLI：`python -m doxagent.workflows.codex_document2.discovery_checkpoint commit --scan-file <文件> --run-id <run> --attempt-id <attempt>`；Pilot 再带 `--pilot-case-id`。与 MCP 一样，重复提交同正文幂等，冲突不能覆盖真实冻结。
- 索引入口：task 的 context_reading；`read_context.py list --group reports|units|records --page 1`、`read --pointer <JSON Pointer> --page 1`、`read --asset <id> --section <id> --page 1`。响应包括 page/total_pages/has_more。

D3 v2.1 索引在 owner read_mapping 完成后生成，引用 owner 的本地实际输入；D3 v2 派生输入纳入冻结 manifest，同时运行边界保护源文件。索引生成/派生存储失败仅产生 warning，全文仍可使用。

已有源 attempt 的 Pilot 导出保持整个 input 原字节；新导航放在 audit。Windows 下索引写入与读取保留原换行符，避免 CRLF 转换污染冻结摘要；短摘要路径也避免额外拉长深目录。

## 离线验证

综合组合测试及修复后的分组复验，**185 个不同离线用例通过**，不是重复运行次数；Ruff 与涉及文件的 diff check 通过。仅出现已有 OpenTelemetry 依赖弃用 warning。验收覆盖：

1. 正式文件优先、SDK 失败回收、技术回执与旧回复兼容。
2. 历史 STATE Late Addition 重交、首次 provenance、累计 Finalization、恢复与局部坏 Value。
3. 真实 CLI 冻结/SHA、无 checkpoint 降级、pending、单节点五阶段与同 Shell thread。
4. 非 required、精确审批、非 Discovery 显式禁用与完整 transport。
5. 中文/emoji/CRLF/代码围栏/超长行、目录和正文分页重组、JSON Pointer、源引用、索引失败。
6. D3 owner 实际读取、v2/v2.1 正常编排与恢复、冻结 manifest。
7. 可选读取总预算、瞬时恢复、实际 HTTP timeout、旧负缓存恢复、有效缓存摘要和 pinned Event Reader。
8. Pilot review 故障不重做研究、Coordinator 按接纳快照推进、源 input 原字节保护。

MU Realization g1 的冻结 context/原始 completion 已离线回放：无需修改原件即可接纳，canonical_shell 不变，历史 STATE provenance 保留。没有重跑该模型研究。

分组证据：

| 验证组 | 结果与复验说明 |
|---|---|
| D2 正式/Pilot/共享索引初始组合 | 124 项中 115 通过，9 项问题全部集中在 v2.1/Discovery 两文件；之后修复并复验这些文件。其余组内未失败项目保留验收结果。 |
| D2 v2.1 + Discovery 两文件复验 | 64 项中先有 62 通过；完整 Pilot 的两项最后发现长临时文件名与无时点测试夹具的问题，已修复并在下组通过。旧冻结 input 的原字节检查也通过。 |
| 最后故障/SDK/Pilot 复验 | 28 项全部通过，包括完整 Pilot 的两种 Narrative 分支、有效 Event 缓存、14 项 SDK 驱动测试与 11 项接纳/可选资产坏例。 |
| 共享索引 | 3 项通过，包括 UTF-8/CRLF、源正文精确重组、分页目录、章节、特殊 Pointer 和索引失败保留原输入。 |
| D3 v2/v2.1 | 58 项通过；补强 owner 索引实际读取断言后单项复验通过；派生输入纳入 manifest 后两项 v2 恢复复验通过。 |
| 坏域报告 | 单项正式流程复验通过：坏 C1 正文隔离、不发 C1 Candidate 研究，C3/C5 保留，研究链发布 PARTIAL。 |

组合与复验日志保存在 `d2_d3_robustness_validation_20261007/`；中间日志保留开发期间失败，终检依据为上述修复后的复验结果。当前十个验收测试文件共收集 185 项，新增用例已包含在计数中。

`mu_realization_g1_replay.json` 保存本次真实坏例回放摘要：source=file、canonical_shell_unchanged=true、provenance=[STATE]、diagnostics=[]，并记录原 context/completion 的 SHA-256。原 Pilot 资产未修改。

## 验收边界

本轮未编辑研究 prompt/internal skill，未改变消费者、前端、发布业务 schema、版本开关或默认模型；保留 O1 同 Shell thread 与单节点 Open Discovery。其他已有工作区修改保留。

离线验收证明交付、接纳、降级、索引访问与恢复合同。未启动真实模型 Pilot、远端部署或修改安装中的 runtime；真实模型对小回执、正常冻结和索引入口的使用效果仍按原方案留作后续实模验收。
