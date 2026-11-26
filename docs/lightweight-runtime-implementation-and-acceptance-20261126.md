# 轻量运行实施与本地验收记录

日期：2026-11-26。对应方案：`docs/lightweight-runtime-implementation-plan-20261126.md`。

状态：已完成本地代码、契约、前端和兼容迁移开发；未部署、未应用生产迁移、未切换任何真实 ticker，未调用真实模型或交易接口做验收。扩大回归仍有失败，详见下文，不能视为生产验收通过。

开发基线为本地 HEAD `434711945a93df0f520ea871087c38df14cc94b0` 加已有工作区修改。保留原有交易执行、初始化修复、W3 回测等未提交工作；未提交 Git commit。原方案保留为设计依据，本文件记录实际交付。

## 1. 已交付行为

- `running_mode` 为 ticker 激活 revision 的持久化配置：`FULL` / `LIGHTWEIGHT`。旧数据缺省 FULL，未知值拒绝；Case、Trade、维护、Sweep 按冻结版本执行。同一 Runtime、Worker、执行 bundle 和资源池服务两种 ticker。
- 轻量初始化保留原 D1 必要子流程、CDECR、O2；增加确定性的 `monitoring.prepare`，复用现有消息配置快照、默认绑定和校验。未生成 D2/D3，不执行完整 O4；沿用 activation prepare/commit、Bus/Runtime 真实 ACK 协议。
- 轻量 W1 R1/R2：NEW normal/low 均进入 W3，OLD normal/low 均归档；不调用 W2，不构造 W2 结果、alignment、Policy 标签或空 PolicySet。技术失败沿用已有重试和回执身份。
- W3 沿用共享 v6 Trade/Operations、输出 schema 和双分支配置。轻量实际研究范围仅 D1，缓存身份与 FULL 分开；共享模板和 ready 库存不分叉。轻量必须具备共享 v6 执行资产，不继承旧 ticker 的 v5 入口。FULL 原有入口选择不变。
- 所有 `prompts/persistent_runtime_v2/w3` 文件保持原文。Trade 输入沿用既有字段白名单，只移除不适用的 W2/Policy/D2；不注入 mode、能力标记或新业务判断。Operations/Selection/O3 研究输入同样剥离仅用于内部持久化的模式元数据。
- WATCH 继续解析、留审计，但轻量提交为 `IGNORED_LIGHTWEIGHT`，不写入实际 WATCH 状态、不影响路由；PIN、事实候选和交易输出保留。缺少 W2/Policy 不导致轻量提交失败。
- 轻量日常维护仅 O2，保留事件库发布、可见性、消费回执、事实记忆和同模式恢复；不执行 O3/Policy 维护。发布 CAS 增加模式一致性，历史成功回执不重新授权过时激活头。
- 保留 CLOSED/Sweep/Selection 和原交易执行。Sweep 复用原两阶段波次，内部沿用部分旧 W2 计数器名称，但轻量第二阶段不产生 W2 Turn/结果。Selection 不将内部 mode 传给模型，释放前检查候选模式。
- Overview、新建表单、初始化进度、Runtime 图及 Case 显示运行方式。轻量 W2/当前 Policy 返回明确的不适用资源状态；历史 FULL Case、旧 Policy 文档仍按原版本读取。FULL 普通 DTO 不强制增加新的模式字段。

## 2. 工作包与实际代码落点

以下路径均相对仓库根目录；函数名用于直接定位。

| 原工作包 | 实际实现 |
|---|---|
| 1 模式/契约 | `src/doxagent/running_mode.py`；`ticker_initialization/schema.py,repository.py`；`persistent_runtime_v2/providers.py,schema.py,projection.py`。条件校验保证 FULL 仍需真实 Policy/W2，轻量不能夹带 D2/W2/Policy。 |
| 2 研究上下文/缓存 | `persistent_runtime_v2/w3.py` 的 `PublishedContextProvider`、`research_pin_key`、`prepare_case`、`_install_context`；`w3_research_assets.py`。读取实际 D1 发布内容，不制造 D2/Policy 文件，沿用原 MCP manifest/工具。 |
| 3 初始化/ACK | `ticker_initialization/catalog.py:default_plan,adapter_factory`；新增 `monitoring_adapter.py`；`activation_adapter.py,consumers.py,runtime_inputs.py,operations.py`；`runtime_scheduler/service.py:admit_activation`。转换 Bus admit 只恢复既有监测状态，不重新安装绑定。 |
| 4 路由 | `persistent_runtime_v2/service.py:_resume_lightweight,_resume_hot`、effect 生成/执行条件；复用原路由输出类型和持久化轮次，不改 FULL router 判断。 |
| 5 W3/Operations | `w3_context.py,w3_v6.py,w3_execution.py,case_outcomes.py`；`schema.py:O3MaintenanceFeed` 的模型边界序列化。原 `policy_watch.py` 应用逻辑无需修改，轻量在宿主提交入口跳过。 |
| 6 O2/恢复 | `maintenance.py`；`ticker_initialization/repository.py:activate_runtime_bundle`；`maintenance_recovery.py:recover_failed`。恢复读取维护 frame 或 Sweep bundle 的冻结模式；无模式历史按原 FULL 解释。 |
| 7 CLOSED/Selection | `coordinator.py:_sweep`、`selection.py`。已有判断结果可按原机制完成确定性落账；旧 FULL 冻结输入不能在切换后重新请求 Selection 模型。 |
| 8 周末转换 | 新增 `v2_control/running_mode_switch.py`；`v2_control/repository.py,service.py`；runtime `operations.py,journal.py,repository.py,w3_execution.py`；初始化 `activation_adapter.py,operations.py`。原 Control RESTART 驱动 activation-only DAG，不新建迁移执行器。 |
| 9 读模型/产品/迁移 | `api_v2` 与 `v2_read` 相关模式/空值分支；`dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`、后端/前端 wire schema；`frontend/v2` 表单/监控行/Runtime/Case；新增 SQL 迁移；本记录与 `changelog`。 |

## 3. 周末转换的事务边界

唯一产品方向是 FULL → LIGHTWEIGHT。采用既有 Control PAUSE + RESTART，检查美东周末且当前语义日已经 CLOSED；周六凌晨仍属周五语义日时拒绝。没有自动定时转换或实时跨模式接续。

静止检查拒绝目标 ticker 的任意 PENDING/RUNNING/HELD task（包括未来 due）、未完成 Case/effect、占用中的 Case thread、待选择/已选未释放候选、可恢复的失败维护/Sweep、未结算 Worker 请求，以及尚需 FULL Policy 反馈的日常记录。事实候选可保留供正常 O2 消费。

进行中的转换操作在现有数据库事务中约束 task admission/claim、effect claim 和新增 W3 模型分发；既已发出的结果落账与 ACK 可以收尾。屏障只作用于目标 ticker 的转换操作，不把普通 FULL PAUSE 改成全面冻结。未修改订单事件处理、持仓退出、ExitRunner 或交易恢复准入。

激活只复用现有 D1、Event Library、monitoring configuration 引用，不重跑研究、不更换初始化身份、不重建消息配置。提交前再次检查休市/静止边界；沿用 base-revision CAS、Control epoch、激活指针、Bus/Runtime ACK 和原失败补偿。两个 ACK 与初始化 run 成功均满足才 settle。

转换失败保留暂停，关闭候选初始化/准入；成功后沿既有 Control 恢复运行，保留 `monitor_mode`、原 `mode_effective_at`、消息游标、PIN、事实记忆、历史 Case、账户权限、订单与持仓。恢复入口不允许把旧 FULL 失败业务重新挂到轻量输入，不支持 LIGHTWEIGHT → FULL 产品转换；内部失败补偿继续使用原 revision 回滚协议。

没有增加 Runtime/Worker/线程池/常驻模式镜像，也没有批量重写历史数据或新增轮询周期。静止核查只在人工转换时读取任务和回执元数据；恢复模式核查复用现有恢复扫描。尚未进行生产 RSS/IO/延迟对比，不宣称已实测资源开销或总模型成本下降。

## 4. 操作步骤

### 4.1 新轻量 ticker

看板选择研究运行方式“轻量运行”与“强制初始化”；交易/监测模式独立选择。“复用当前文档”继承当前激活模式，不能借此变更模式。

原初始化 CLI 同样支持目标参数，例如：

```powershell
doxagent-ticker-init submit --ticker EXAMPLE --running-mode LIGHTWEIGHT --research-cutoff-at 2026-11-28T12:00:00Z
```

示例不代表已提交任务。使用环境既有 Worker、Guardian/Control 与消费者，不需要另外启动轻量进程。源凭证、监测词、自定义来源必需参数仍按原配置检查。

### 4.2 现有 FULL ticker

1. 在 FULL 下完成已准入 Case、Operations、O2/O3、Sweep、Selection 及相关恢复，查看终态与回执。不要通过取消任务、强制过期候选或改数据库绕过检查。
2. 在美东周末 CLOSED 窗口使用看板/原 V2 Control 的 PAUSE，读取最新 Control revision。runtime CLI 的旧 `pause` 命令只是 journal pause，不能代替此步骤。
3. 在目标环境中提交以下命令；替换 ticker、数字 revision 和幂等键：

```powershell
python -m doxagent.persistent_runtime_v2.cli switch-to-lightweight `
  --ticker EXAMPLE `
  --expected-control-revision 12 `
  --idempotency-key weekend-light-example-20261128 `
  --reason "周末人工转换轻量运行"
```

4. 查看返回的 Control operation；重复同键只恢复原操作。`MODE_SWITCH_BUSY:<identity>` 表示仍有必须在 FULL 下收尾的工作；恢复原模式处理后，用最新 revision 和新幂等键重新提交。FAILED 操作不能靠相同键创建新 run。
5. 核对 activation revision 为 LIGHTWEIGHT、D1/O2/config 引用保持、无 D2/Policy 引用、run SUCCEEDED、Bus/Runtime ACK 完整，以及原 monitor_mode、订单/持仓与消息游标保留。提交命令成功只代表操作被接受，不能替代这些终态核对。

不需平仓。转换命令没有重置交易权限、订单、持仓或退出管理的步骤。

## 5. 本地验证结果与证据

各组有重叠，不相加为唯一测试总数。测试使用本地 fixture/隔离 SQLite，不等同生产模型、Bus 或 Worker 的网络验收。

| 检查 | 结果与证据 |
|---|---|
| 最终轻量专项 + Runtime 恢复 | **42 passed**；`artifacts/lightweight-recovery-final-20261126.xml`、同名 `.log`。覆盖路由置信度/实时和休市、重放、W3 输入、D1-only 缓存、WATCH/PIN/事实、O2 不调 O3、旧模式恢复拒绝、转换门禁、ACK 等待、失败保留暂停、配置保留。 |
| 模型边界/维护/读模型等补充回归 | **65 passed**；`artifacts/lightweight-metadata-regression-20261126.log`。 |
| 原交易/退出/账户恢复回归 | **76 passed**；`artifacts/lightweight-trade-regression-20261126.log`。没有用本需求改写原交易实现。 |
| 前端单测 | **74 passed / 16 files**；`artifacts/lightweight-frontend-final-20261126.log`。 |
| schema/typecheck/生产构建 | 通过；`artifacts/lightweight-build-final-20261126.log`。保留现有大于 500 kB 的 chunk 提示。 |
| 真实浏览器 fixture | **8 passed**，1262/1559px；`artifacts/lightweight-browser-final-20261126.log`。新轻量表单/Case、FULL v5 成功/失败 Case、Overview 原指标；校验页面异常和轻量横向溢出。截图在 `frontend/v2/test-results/lightweight-*.png`。 |
| Python 静态检查 | 相关包 compileall、新模块与新专项测试 Ruff、`git diff --check` 通过。 |
| 提示词不变 | `git diff --name-only -- prompts/persistent_runtime_v2/w3` 无输出；没有改写冻结 bundle。 |
| PostgreSQL 迁移 | PGlite 0.6.0 嵌入式 PostgreSQL 执行原表 DDL + 新迁移；FULL/LIGHT 正常写入，缺 Policy 的 FULL、有 Policy 的 LIGHT、未知模式均拒绝，历史行不变。`artifacts/lightweight-postgres-migration-20261126.json`。临时测试依赖仅在 D: 测试目录，没有加入项目。 |

### 5.1 扩大回归尚未全部通过

`artifacts/lightweight-runtime-final-20261126.xml`：**660 passed、14 failed、1 skipped**。随后修正/增加的恢复边界由上表最终 42 项回归验证。不能将该次扩大回归描述为通过。

已用 `git archive` 提取上述原始 HEAD 的 `src`，在独立 D: 路径运行失败用例，确认导入来自该基线。`artifacts/lightweight-head-baseline-20261126.xml`：12 failed、40 passed。以下 12 个失败在未加入轻量代码的 HEAD 同样复现：

- `test_bindings` 的 advanced_dedup 缺失（1 项）。
- `test_json_subtype_receipts` 的 JSON 列触发器/历史 envelope（5 项）。
- `test_pnl` FIFO、`test_production_capabilities` ExecutionProfile（各 1 项）。
- `test_projection_freshness` 的 Windows SQLite 路径与 ACK 合并（2 项）。
- `test_projection_recovery` Windows SQLite 路径、`test_store_recovery` 缺失 Control fixture（各 1 项）。

另外 2 项不能归为已确认基线失败：

- 杀进程后 SQLite WAL reopen 的 `test_process_killed_between_w1_and_w2_keeps_unknown_dispatch_without_repeating_w1`：扩大回归出现 disk I/O error；原始 HEAD 通过，当前代码独立复测 **2 passed**，见 `artifacts/lightweight-process-recheck-20261126.log`。保留 Windows 进程/存储不稳定风险。
- `test_fact_sync_pages_resume_after_process_restart`：当前代码曾出现额外一次未完成分页；原始 HEAD 单测通过。增加临时观测后当前代码记录 0→16→32→40 facts 并通过，涉及文件无此次 feature 差异，但尚未确定根因；不能以观测通过宣布已修复。没有把临时诊断包装器写入产品代码。

这些失败未通过删断言、强制跳过或扩大本需求范围处理。发布前应在目标操作系统/部署基线复测并确认处置。

## 6. 迁移与上线边界

增量文件：`supabase/migrations/20261125233758_lightweight_terminal_policy_nullable.sql`。迁移由 Supabase CLI 创建，文件时间为 UTC；只让 terminal projection 的 Policy 版本可空，并添加模式/Policy 一致性 `NOT VALID CHECK`。模式沿用 `projection_json`，没有新增模式列、索引、全表回填或历史扫描验证。旧记录缺省 FULL，历史约束验证可按既有数据库维护安排，不是启用轻量的全库迁移任务。

发布必须先核对实际部署基线并应用该迁移，再更新所有相关 API/Read/Runtime/初始化消费者，最后才产生轻量记录。不得只升级 Worker、让旧读取端接收 nullable 轻量数据。出现轻量数据后，不能直接退回不认识 mode/null Policy 的旧镜像。

仍需在隔离环境完成一次真实轻量初始化和一次周末转换故障注入，检查真实双 ACK、D1-only 工具返回、O2 发布消费、原 FULL ticker 自然消息链，以及混跑负载下共享资源/延迟。此次没有开展真实模型回测、生产切换、实盘交易或生产性能测量。

## 7. 开发环境说明

测试期间 C: 可用空间偏低，后续 SQLite/浏览器测试和原始 HEAD 基线放在 `D:\DoxAgent-task-temp\lightweight-runtime-20261126`。一次清理本次生成的 C: 临时 pytest 目录的动作被自动审批策略拒绝，未继续删除，目录保留；该拒绝不涉及代码、数据库迁移或业务操作。
