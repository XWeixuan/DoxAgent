# D2/D3 v2.1 下游消费者实施与验收

日期：2026-10-10。依据同目录 `d2_d3_v21_downstream_consumer_implementation_plan_20261010.md`，执行 T1–T10。范围为代码、编排、合同、读投影、API/前端和离线验证；没有部署、活动版本切换、远端 DDL、真实模型调用或真实交易验收。未编辑 prompt/skill，工作区其他会话已有修改保留。

## 1. 实现对照

| 项目 | 实际落地 |
|---|---|
| T1 新旧合同 | `consumer_contracts.py` 统一解码原生 D2 v2.1、Policy v3、projection v5；旧 Policy/投影保持独立分支。原生全文保留双向 Condition、层级、calibration、transmission、ref。 |
| T2 正式候选 | `publication_v21.py` 显式校验 staged 正文、投影、coverage、Markdown 四文件及 manifest/hash；保存 ArtifactRef/PublishedDocument/Bundle，同版幂等 `publish_candidate`，不抢 current。大正文走配置的 PublishedDocumentStorage，并保留本地校验正文供离线读投影。 |
| T3 方向状态 | `policy_consumption.py` 管理 Policy×direction 的代次、消费依据、迁移和授权。共享冻结策略恢复 True。READY 与方向消费、WATCH 删除共用 output journal 事务；保留旧 ledger。 |
| T4 W2/W3 | 原生 R1 严格三字段，R2 完整正文+资格。消费状态不覆盖真实 HIT/RELATED。新 W3 显式选 Condition 或不交易，取固定 Condition 的方向；拒绝交易不丢合法 Delta。 |
| T5 MAINTAIN | feed v2 持续带未重新授权的消费记录。完整同 ID upsert + strict rearm_requests 才接纳；绑定本轮看见的消费 ID/代次，正文不变也非 NOOP；非法对象局部拒绝。 |
| T6 活动接入 | 初始化/调度在活动 CAS 后、ACK 前应用授权；维护在 CAS 后、processed 前应用。journal 中 applied receipt/head 与代次变化同事务，崩溃恢复幂等。 |
| T7 WATCH | 冻结 W2 后跟进集合（含空集）；来源语义日 D/D+1，D+2 boundary 过期。不续期、CANCEL 优先、取消有来源 CAS；READY 同事务取消且迟到 SET 不复活。保留 W1/W2/R3/标签，跟进走复核模式。 |
| T8 CLOSED/冻结包 | 候选保存 Condition 内容身份+使用代次；只在有效释放 READY 时消费。新 execution revision 3 保存合同/schema/hash，旧包原义保留；原生 v5 不隐式套用旧包。v2.1 内部重跑冻结 owner workspace、host state、阶段/任务参数，只运行选中节点，不隐式发布。 |
| T9 下游观察 | O4 获得固定全文文件；D2 原生 Shell/Unit 内容、Policy 双向条件、原始来源、方向资格、HIT 统计、WATCH 证据和导出贯通 API/读投影/前端。未知消费证据不伪造“未消费”。新 Policy 默认 ALL；不编造旧 Gap 链接。 |
| T10 验收 | 新方向/授权/WATCH/发布/恢复回归，原运行编排和 API 回归，前端单测、构建、双宽浏览器检查及隔离 shadow rebuild。根 changelog 追加本轮记录。 |

## 2. 执行细节与修正

- 方向规则的 Condition revision 只依赖该 Condition 的 decision、trigger_layer、criterion、calibration。兄弟条件改稿不使当前候选失效；选中规则变化使候选失效。消费身份不依赖这些文本，改稿仍不解冻。
- 输出前在事务外取得当前规则/活动 revision，journal 事务内核对 admission head。发生 head 竞态时刷新一次；仍未接入则重试冻结结果，不把临时状态写成不可恢复的终态意图。使用代次始终取 case/candidate 原冻结值，不升级旧候选。
- W3 研究结果在应用副作用前持久化；输出接入暂未完成时恢复该结果，不重跑研究。Delta 幂等，不因交易重试重复新增。
- 从旧冻结 ledger 迁移只采用实际交易证据证明的方向；证据缺口记录 UNRESOLVED，不猜冻结双向，也不猜未消费。相关 Policy 暂不具备执行资格；其他 Policy 不被整体阻塞。
- 规则失效/旧消费证据不明的技术拒绝状态纳入公共合同、非成交结果和统计过滤，避免算作已输出交易或挡住后续合法释放。
- 读投影 capture/backfill 增加 direction/consumption/admission/WATCH 各 namespace；已有旧 trigger 升级时，仅重开 runtime_values 回填游标，保留全部源账本。
- 外部存储 D2/D3 正文在本地研究库旁 `published-contents/<sha256>` 留存不可变校验副本，读投影校验 hash/size 后读取。该目录是正式研究正文的一部分，迁移/备份研究库时应一并保留；已存在且没有本地副本的外部历史正文仍使用既有显式回填边界，不假装可读。
- Case 详情不再用专家分支的“不交易”覆盖实际 Policy 交易，显式展示选中 Policy/Condition 和执行理由；导出同时保留 WATCH 与最终选择。
- Windows 文件映射导致 Node 覆盖写 schema 失败，前后端生成脚本改用临时文件+rename；生成后的 schema 摘要与公共 types 一致。
- 初始化任务 work 与心跳同时完成时优先结算已完成 work，避免在完成后因旧 lease 心跳产生伪失败。

## 3. 实际验证

后端主组合 **164 passed in 220.27s**；O4/Runtime Scheduler 适配器补测 **11 passed in 31.27s**；大正文 D3/外部 D2 正文读取补测 **2 passed in 29.70s**（大正文场景与主组合重复），原有 D2 外部发布存储回归 **1 passed in 21.28s**。全部最终执行通过。主组合只有已有 OpenTelemetry 导入弃用提示。

主组合实际命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_runtime_policy_direction_usage.py tests/test_document3_v21_publication.py tests/test_runtime_w2r3_orchestration.py tests/test_persistent_runtime_v2.py tests/test_persistent_runtime_w3.py tests/test_runtime_orchestration_execution.py tests/test_runtime_orchestration_recovery.py tests/test_ticker_initialization_activation.py tests/test_ticker_initialization_internal_rerun.py tests/test_codex_document3_v21_maintenance.py tests/v2_backend/test_wire.py tests/v2_backend/test_formal.py tests/v2_backend/test_case_evidence.py tests/v2_backend/test_policy_consumption_coverage.py tests/v2_backend/test_trade_outcomes.py tests/v2_backend/test_w2r3_reads.py tests/v2_backend/test_direction_usage_reads.py tests/v2_backend/test_read_maintenance.py -q --tb=short
```

新增核心模块及新增回归完整 Ruff 通过；既有 API/读投影/适配模块 Ruff 排除原有 E501 长行规则后通过，未以全库 lint 通过表述。`git diff --check` 通过。前后端 schema 生成通过；一次大组合在进程已加载旧 schema 后重生成文件，出现摘要缓存不一致，后续 fresh process 主组合通过。早期失败记录单独留存，不混充最终通过证据。测试都使用隔离 SQLite、fake Worker/Responses/Agent；不是 MU 真实研究测试。

新增回归覆盖：同方向并发只消费一次、反方向保留、事务回滚无消费、消费 ID/代次 CAS、重复授权不加代次、完整 upsert 与 staged/activation 区别、无新 Event 的持续维护输入、旧方向迁移缺口、R1/R2 实际 payload、拒绝交易保留 Delta、OLD+RELATED WATCH、冻结结果重试、原生内存路径、执行包不匹配、CLOSED 规则与代次失效、兄弟条件兼容、四文件发布/损坏校验/大正文、本机真实 v2.1 owner 内部重跑、真实 SourceOutbox CDC、shadow 重建/verify/隔离 alias 切换以及全部最终 HIT 独立统计。

前端已执行：

- `pnpm build`：schema/typecheck/Vite build 通过；保留已有 bundle size 提示，不把它作为本轮失败。
- `pnpm test`：12 文件、58 项通过。
- `pnpm exec playwright test tests/browser/downstream-v21.spec.ts tests/browser/runtime-w2r3.spec.ts`：12 项通过，1262/1559px；覆盖 D2 层级索引与四块内容、双向 Policy/原始 ref、既有 R3、WATCH 原因/期限/接纳回执、显式 Policy 交易选择，无横向溢出。
- 实际查看 Policy 与 WATCH 截图。浏览器为合成组件 fixture，不冒充正式 API 数据验收；正式 API 的原生/历史读取另有后端测试。

## 4. 留存与上线边界

本轮离线验收记录和截图保存到同目录 `downstream_consumer_validation_20261010/`。配套业务合同说明见 `d2_d3_v21_downstream_prompt_skill_handoff_20261010.md`。

尚未执行：提示词行为验收、隔离 MU 新合同全链真实模型测试、真实 READY→券商成交测试、远端 shadow 切换、生产部署。上线应携带本轮后端/读投影/API/前端和配套的新执行资产；原生 v5 需要 revision 3 合同包。旧在途任务使用原冻结包，回退不能清空已发生的消费/授权记录或靠旧镜像忽略新字段。
