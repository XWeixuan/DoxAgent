# V2 ticker 初始化鲁棒性修复落地记录

日期：2026-09-10
范围：本地代码；对应 `ticker_initialization_resilience_audit_20260910.md`。未同步、部署、重启或操作远端测试。

## 实施原则与结果

审计列出的 A—G 各组修复已落实到接入、恢复、局部隔离、发布和启动路径；原本正确的完整性约束继续保留。父工作流继续以 SUCCEEDED / FAILED 表达执行结果，PARTIAL、DEGRADED、NOOP、未解析引用等作为产物质量或局部交付状态，不新增父级质量 gate。

修复并非无条件把异常变成成功：可证明的主体继续交接；无法证明的条目隔离；没有任何可用主体、身份错配、不可变输入被改写、租约失效、真实控制账本无法提交以及完全无 monitoring 能力仍会失败。保留已有脏工作区修改，未提交 Git。

## 逐项落地

| 审计项 | 实际处理 | 主要实现 |
|---|---|---|
| A01 | durable return 写 codec_version；旧回执先兼容读取，额外字段在接入副本中移除；不改原回执。D1/D2 可用精确 ArtifactRef 对账恢复，核对 run/hash；无法恢复仅拒绝该子节点，不重置历史预算。 | ticker_initialization/substeps.py、repository.py、invocation.py |
| A02 | 父/子失败 checkpoint 写 code、scope、retryable、摘要；真正的格式恢复落在各节点；重试仍有界，LeaseLost 不降级。 | codex_runtime/recovery.py、ticker_initialization/service.py、substeps.py |
| A03 | START_THREAD 独立超时，上限 120 秒且不超过节点 timeout；与 RUN_TURN 区分；可重试异常短暂退避。D1/D2/D3 在 worker 失败后利用真实输出/稳定工作区成果，不伪造 worker job 成功。 | codex_worker/jobs.py、各文档 runner |
| A04 | attempt 错误摘要按 UTF-8 字节限长；超大事件 payload 保存摘要与 hash。D1 完整校验错误写 attempt audit，诊断文件的传输/IO 失败记录日志，不推翻业务成果；控制账本写入仍必须成功。 | codex_runtime/schema.py、recovery.py、codex_document1/node_runner.py |
| B01 | final report 优先，缺失才用 draft；两者差异、progress 未完成、candidate 文件差异只记诊断；两份主体均为空才拒绝。原文件不覆盖。 | codex_document1/attempt_bundle.py |
| B02 | NodeOutput 先拆解主体、逐条读取各数组，隔离坏行，多余 metadata 不丢整篇报告；严格 canonical model 保留。 | codex_document1/recovery.py、node_runner.py |
| B03 | 未解析引用保留位置、attempt 身份及 resolved=false；跨节点继续使用受控 lineage 重绑定；汇总层显式允许未解析引用，避免后置 gate 再次阻断。晋级异常可建立未解析条目，不虚构来源。 | node_runner.py、upstream_rebinder.py、observations/promotion.py、codex_global_research/orchestrator.py |
| B04 | Global Research 按 C1/C3/C5 可读主体判定，不按任意 failed child 判定；C4 enrichment 缺失复用本轮 pre-scan，允许明确关系缺口。 | codex_global_research/orchestrator.py |
| B05 | assemble / publish 独立 durable 子回执；同 hash artifact 和既有 aggregate manifest 复用；发布重试不重做研究。V2 初始化继续使用本地 SQLite/worker artifact 发布路径，沿用前置凭证与本地存储配置检查。 | codex_document1/orchestrator.py、ticker_initialization/substeps.py、internal_adapter.py |
| C01 | processor 少量缺项先认领已完成记录，其余写 document_gaps 内容寻址记录；健康消息进入真实 epoch。全缺或全失败不冒充 NOOP；未知身份、PREBUILT 与原生 FINALIZED 条件保留。 | cdecr_integration/workflow_runner.py |
| C02 | 控制器拥有阶段、路径与冻结 base；work/survey/map 不再作为全包格式前置 gate；最终 canonical bundle 仍校验真实身份。JSONL 逐行解析，坏行与冲突重复项隔离。 | codex_event_library/remote_runner.py、event_library/bundle_io.py |
| C03 | coverage 由 validator 的实际结果覆盖模型展示计数，取消已经可发布后因回显计数不一致而失败。 | codex_event_library/remote_runner.py |
| C04 | 反复计算受影响事件及依赖闭包；移除本轮不一致的修改及配套行，保留健康事件与既有 base，把相关 delta 留 Pending。不能定位或不能形成一致子集仍拒绝。 | event_library/validator.py |
| C05 | Reference 冗余 decision ledger 从 canonical decision 与冻结时钟重建；缺日期账本记缺口，不确定或冲突日期关联对象隔离。绝不猜日期、importance 或关系方向。 | event_library/validator.py、bundle_io.py |
| C06 | 删除导入成功后仅因展示 validation status 变化而抛错的逻辑；真实导入器的版本、身份与发布约束保留。 | codex_event_library/remote_runner.py |
| D01 | candidate/review 分支失败在剩余输入可用时降级，支持局部 ValueError 与节点执行错误；存储、租约及受保护路径问题不被普通业务容错吞掉。 | codex_document2/orchestrator.py |
| D02 | 原 D1 thread 不再是评审输入必需品；D2 使用新线程并显式提供完整上下文，避免旧 MCP attempt scope。 | codex_document2/runner.py、orchestrator.py |
| D03 | synthesis/finalization 无有效响应时，从实际候选/provisional 生成降级 seeds；保留原问题、边界、命题，未知 horizon 明示 UNRESOLVED，不编造投资判断。 | codex_document2/recovery.py、runner.py |
| D04 | O1 继续按 shell 隔离；D2 publication state 与 warnings 传到父 quality_annotations。 | codex_document2/orchestrator.py、ticker_initialization/research_adapter.py |
| E01 | 保留 D3 原有逐记录恢复、确定性发现、advisory review 和局部未决政策。 | codex_document3 既有恢复/校验路径 |
| E02 | failed worker 在预算内尝试后，若工作区存在阶段成果，交回恢复流程；最终仍由 orchestrator 校验冻结输入和规范化产物。Compile/Review 失败且已落盘的定向测试验证可发布 PARTIAL。 | codex_document3/runner.py |
| E03 | 保留 ticker/run/base/hash、冻结输入及受保护路径完整性校验；沿用已有普通 scratch 非阻塞处理，不无条件接受 Agent 改写 runtime-owned publication。 | codex_document3 既有边界校验 |
| F01 | 明确空 PolicySet 生成程序 NOOP 配置，跳过无需求 DELIVER；已有基础 binding 可继续。完全无可用 binding 仍不声称完整初始化成功。 | codex_monitoring_o4/schema.py、orchestrator.py |
| F02 | CONFIGURE 逐 need 接入；坏项/冲突重复项隔离，漏 policy 记 omission，未知 policy 引用剔除，不发明来源。 | codex_monitoring_o4/recovery.py、orchestrator.py |
| F03 | PlanFinalizer 按 need 处理；局部 source/evidence 错误记 omission，健康 need 保留。真实 mutation、crawler 发布与能力权限约束保留。 | codex_monitoring_o4/policy.py |
| F04 | checkpoint 从上次合法状态与本次合法项合并；单项错误保留原状态或 REPLAN_REQUIRED；final settlement 不再因先前 checkpoint 小错被整体拒绝。 | codex_monitoring_o4/policy.py、runner.py、recovery.py |
| F05 | 未终态/缺失/无耗尽证明的 FAILED 明确结算为 REPLAN_REQUIRED，不伪造 crawler 成功或候选耗尽。外层耗尽后的 continuation 置 HELD，防止父级降级完成后继续偷跑。 | codex_monitoring_o4/orchestrator.py、ticker_initialization/o4_adapter.py |
| F06 | 安装前逐 binding 检查，隔离缺失/禁用源与共享定义冲突；可用的既有 binding 保留，共享 source 不覆盖。无可用 binding 仍拒绝完整监测启动。 | ticker_initialization/configuration.py、o4_adapter.py |
| G01 | W3 readiness 在总启动预算内退避复查；consumer 持久化目标 revision 的接纳错误，ACK 超时报告实际失败原因；不触发研究重跑。 | ticker_initialization/activation_adapter.py、consumers.py、repository.py |
| G02 | 保留真实 Bus/Runtime ACK、目标 revision、实际 Index/Projection 与文档可用性检查，以及控制面停止 admission 的约束。 | ticker_initialization/activation_adapter.py、consumers.py |

## 恢复与诊断语义

- 修复副本、canonical 输出和 receipt 迁移视图与原始响应分开；兼容解析不补造必填业务事实。
- D1 ingestion / turn_summary 记录本次隔离与校验；超大控制事件保留有界摘要与 hash，原 worker/attempt 文件仍是详细追踪入口。
- O2 的局部错误以 quarantine warning 和 Pending delta 留痕；健康事件被独立验证后才可发布，不是单纯把 ERROR 改成 WARNING。
- O4 REPLAN_REQUIRED 表示本轮未交付，需要后续重规划；已有真实可用 binding 可以支持本轮整体初始化。HELD continuation 不自动执行。
- 兼容恢复有明确支持范围，不能修复任意损坏的历史数据。无法恢复的回执只影响最小执行节点，历史预算仍保留。

## 必要验证

未跑全量测试、真实模型、远端 MU 或完整 ticker 初始化。完成 31 个不同的定向用例：主轮 29 项通过，随后补充 2 项引用汇总与 D3 failed-worker 成果恢复通过；最后 D1 发布恢复与引用汇总再次定向确认。

主轮范围：`tests/test_initialization_resilience.py`（当时 9 项）、`tests/test_ticker_initialization_substeps.py`、`tests/test_ticker_initialization_o4_adapter.py`、`tests/test_event_library_time_reference_repair.py`，以及 D3 正常初始化发布单例。

重点覆盖：D1 draft/progress/metadata 恢复；发布异常不重做研究；中文诊断字节预算；旧 receipt 兼容与缺必填字段拒绝；O2 坏行/局部关系隔离、日期与 Reference 规则；D2 provisional 命题保留；O4 空计划、身份错配、局部延期和断点恢复；D3 成功与 worker 失败后发布。保留 3 条现有依赖弃用/实验性提示。

新增集中回归文件：`tests/test_initialization_resilience.py`。原有两处“局部错误必须全失败”的测试已更新为新的隔离语义。改动 Python 文件执行 Ruff 静态检查和语法解析。

以上是本地恢复逻辑验证，不代表远端 MU 已完成验收。按要求暂不同步远端。
