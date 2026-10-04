# DoxAgent V1 退役与本地项目清理最终方案

审查日期：2026-10-04，Asia/Shanghai。项目根目录：`C:\Users\WEIXUANXIE\Desktop\DoxAgent`。

**结论：彻底移除自建 ReAct V1 编排、执行引擎、旧界面及其专用资产；把仍被 Codex V2 使用的少量权限、消息处理和基础类型从旧实现中剥离。同步清理本地可重建的历史测试产物、缓存和旧导出，不清空运行状态。无需保留一套“备用 V1”，也无需为了退役重构 V2 工作流。**

本文件替代附件 Word 方案作为后续实施依据。附件是待审查的外部材料，其中的执行建议不构成操作授权。本轮只做读取、盘点、静态分析、受限导入检查和方案编写；未执行退役、删除、移动、Git 索引修改、依赖安装、部署、数据库迁移、真实模型调用或交易操作。后续执行本方案必须先取得用户明确许可。本轮未修改业务代码，因此不追加代码变更 changelog；后续重要代码修改必须追加 `changelog`。

## 1 审查基线与可信边界

### 1.1 本地基线不同于附件

| 项目 | 附件扫描 | 本轮本地核对 |
| --- | --- | --- |
| Git 提交 | `7796efa45724da19aabe9939c795e243c20c2a99` | `aa0efcdb997941b33a83927abc165353e2cef290` |
| 分支 | GitHub `main` | `codex/trade-execution-repair` |
| Git 跟踪路径 | 10,299 | 10,363 |
| 路径集合差异 | — | 相对附件新增 65，减少 1 |
| 受审 Python 文件 | 1,017 | `src/tests/scripts/eval/pilot_runtime/examples` 共 1,043 |
| `src` Python 模块 | 678 | 688 |
| AST 语法失败 | 0 | 0 |
| Codex D1 bundle 资源引用 | 21 | 25 条 `resource_sources`，目标均存在 |

新增路径包括 2026-10-02 的 SDK/模型升级、市场新闻、读侧恢复部署文件及验收记录，以及 D2 v2.1 方案。减少的是 `prompts/DoxAgent.lnk`，它已不在当前跟踪集合中，不能再把它写成待删除的现存 Git 文件。

扫描开始时 Git 状态显示 27 个跟踪文件修改、22 个未跟踪条目、3,232 个 `D`。未跟踪条目包括目录级报告，不等同于逐文件数量。**这 3,232 个 `D` 所在测试产物路径同时存在 Windows 访问拒绝，不能解释为“已删除”。** 对这些跟踪路径逐项 stat 得到访问错误；不能确认其物理缺失。执行时只处理已确认的目标，不用 reset/clean 修复这份状态。

本地 D2/D3 v2.1 改动正在进行，涉及 `codex_runtime/schema.py`、`data_runtime/policy.py`、Worker、Pilot、D2/D3 runner、schema、recovery 和新增测试。`.d3_v21_preservation.json` 是当前保留范围的哈希记录，不是一个月前的临时垃圾。清理不能覆盖这些改动，也不能只从 HEAD 重建工作区而丢掉它们。

### 1.2 本地文件盘点的口径

排除 `.git`，不跟随 Junction/符号链接，共读取到 99,656 个物理文件的元数据，逻辑长度合计约 **8,213.42 MiB**。遍历跳过 4,127 个连接/重解析目录，另有 127 项访问或 stat 错误。数字是可读取部分的统计，不是完整磁盘占用，也不是预计可回收空间；硬链接、压缩、外部连接及权限均影响实际占用。

只读取配置中有关存储方式、开关和路径的条目，不导出密钥、token、浏览器 Cookie 或认证文件内容。本轮没有连接远端服务器，没有确认生产容器的当前镜像、挂载、运行任务及真实仓位。下文“生产入口”指本地部署文件表达的装配，不冒充线上实时状态。

### 1.3 已做的检查与未做的检查

已读 Word 正文及表格文本、ZIP 中两份 JSON；对完整跟踪集合与本地可读取目录进行核对；重建 Python AST 导入索引，并人工追踪父包导出、共享类型、权限调用、资源 manifest、脚本入口、Docker COPY、Compose 命令、测试互相导入及本地路径引用。

执行了只导入模块的受限检查：使用本地 `.venv` Python、显式加入当前 `src`、关闭 `.pyc` 写入，阻止网络连接、SQLite 连接和子进程启动，不调用服务构造/worker/migrate。`ticker_initialization`、`production_v2` 可导入；API、Worker、scheduler、control、历史加载器受 `rpds` 导入失败影响；Data MCP 受 `cryptography` 的 `_rust` 导入失败影响。阻止边界没有记录到状态或外部操作尝试。检查过程中仍观察到旧 agents、blackboard、monitoring.service、persistent_runtime.service、gateway.providers、annotations.postgres 被导入。

本地解释器直接运行时还需要显式 `src` 路径，不能假定现有 `.venv` 已正确安装当前项目。这些是清理前基线限制，不是删后验收结果。**本轮未运行 pytest、前端构建或 Docker 启动；方案批准后在独立锁定环境验收，不能以本轮静态审查替代运行回归。**

## 2 对原方案的纠正和补充

| 编号 | 原方案的问题或不足 | 本轮最终处理 |
| --- | --- | --- |
| R01 | 把 `workflows/__init__.py` 描述为顶层加载旧编排 | 本地已是 `_EXPORTS + __getattr__` 惰性导出；导入 Codex 子包不会因此自动加载初始化实现。仍须删除失效旧导出，但它不是当前这条启动耦合的原因 |
| R02 | 只明确保留 monitoring 的 media_enrichment/schema | 补入 **normalizer.py**；`cdecr_integration/historical_loader.py:32,502` 真实调用 `normalize_message`，影响 V2 初始化历史素材 |
| R03 | 父包问题列举不完整 | 补齐 `monitoring/__init__.py`、`runtime_scheduler/__init__.py`、`model_usage/__init__.py`、`models/__init__.py`、`gateway/__init__.py`。只改 service 顶层 import 不够 |
| R04 | 将旧 recorder 与网关契约主要列为迁移对象 | V2 D1/D2 直接构造 `ModelUsageEvent` 写仓库；旧 gateway recorder 随网关退役即可，保留计费仓库/定价/服务。不必为无当前调用者的适配器新造一套共享网关类型 |
| R05 | annotations 仅列为待核实混合部分 | 扫描到的 annotations 源码消费者全部在旧 agents/ReAct 内；可把整组列为随旧引擎退出，而非长期默认保留 |
| R06 | 审计索引把 `src/doxagent/monitoring_viewer.py` 列 K0 | 该文件是旧 viewer 的包装入口，随 monitoring viewer 一起退役 |
| R07 | 未核对独立 StockTwits 旧采集器 | V2 自己的 `StocktwitsMessagesAdapter` 不导入 `doxagent.stocktwits`；旧独立 crawler/仓库/CLI 和脚本可随旧 monitoring 配套退出，V2 adapter 及共用账号配置保留 |
| R08 | 主要清理量按 Git blob 计算 | 补入本地约 1,323.54 MiB 的 `.tmp-uv`、687.31 MiB 的 `.uv-cache`、420.90 MiB 的旧 Brief State 导出、旧 dashboard 安装产物等；不能把 Git 字节量当本地回收量 |
| R09 | 没有识别本地导出 node_modules 的性质 | 当前是指向 Codex 公共依赖目录的 **Junction**；只移除连接，绝不递归删除连接目标。Git 中误提交的 4,462 个 blob 独立退出索引 |
| R10 | 未发现本地解释器依赖 | `.venv/pyvenv.cfg` 的 home 指向 `.uv-python/cpython-3.11-windows-x86_64-none`；后者必须保留，不能按旧日期删除 |
| R11 | `.tmp` 被整体保护但未形成细分清单 | 将可重建测试/缓存、证据归档、真实状态、当前工作四类分开；对健康可重建产物不要求完整生产验收，不因少数状态库而保留所有垃圾 |
| R12 | 评测资源保护不够具体 | W1/W2 dataset manifest 固定引用 `.tmp` 下 O3/O2 输出；必须保存冻结输入并修正复现入口，再归档相关运行目录，不能仅保留 manifest |
| R13 | 当前部署叠加和新增版本保护不足 | 补入 Codex GPT6、市场新闻 overlay、Guardian、Site Access/Chrome，以及未提交 D2/D3 v2.1 测试与 schema 的保护边界 |
| R14 | 默认保留/待细分项较多，缺最终退出目标 | 明确共享模块最终形态、旧入口退出清单、混合测试迁移清单、归档与删除的区别及每批独立验收条件 |

原方案关于“先解耦再删旧执行层”、保留 CDECR 子模块 v1 资源、保留 Codex compatibility、保留 API 契约和迁移历史的方向正确。本方案保留这些判断，但不把“目录曾属于 V1”当作删除依据，也不把“静态可达”都当成必须永久保留的业务能力。

## 3 退役范围和最终工程形态

### 3.1 必须退出的能力

退出旧自建 ReAct/MAF runner、旧 document1/document2/initialization 编排、Blackboard 工作状态和 workflow memory、旧 persistent runtime W1/W2 执行、旧 Dashboard 页面/API、旧 model gateway/tracing、旧 prompt/skill 注入系统，以及这些能力的专用脚本、测试、构建入口和默认启动说明。

不创建新的 legacy 备用目录，不把 V1 代码挪到 `src/archive` 后继续打进 wheel。历史源码已经在 Git 历史中；需要留证据时存档到项目根目录之外，主树只留短索引和必要复现资料。

### 3.2 保留的 V2 能力

保留 `codex_runtime`、`codex_worker`、`data_runtime`、`mcp`、全部 `workflows/codex_*`、`ticker_initialization`、`initialization_repair`、`cdecr_integration`、`event_library`、`message_bus_v2`、`content_enrichment`、`crawler_plane`、`site_strategy`、`persistent_runtime_v2`、`trade_execution`、`v2_control`、`v2_read`、`api_v2`、`observations`、当前工具 providers、水平指标采集、计费和 resource safety/semantic clock/postgres 能力。

保留 Global Research 与 Market Situation Research 的现有 lane 语义；保留当前默认 V2、已实施的 staged v2.1 和历史版本读取能力。清理不得自动激活 D2/D3 v2.1，不修改节点编排、schema 业务定义、reasoning effort、模型路由、并发预算、恢复/lease/幂等协议或交付/交易准入。

Codex Pilot、`pilot_runtime` 和对应测试属于 V2 试点/复现工具，不属于 ReAct 引擎。保留本轮仍有消费者的 Pilot；其兼容文档版本也不等于自建 V1。

### 3.3 少量共享代码的最终归属

以最少迁移完成退役，不增加抽象框架或新服务：

- 角色工具集合放到 `data_runtime` 的独立静态策略模块，当前 `DataToolPolicyRegistry` 继续控制 node/role/ticker 权限。
- 通用 AgentName、ResultStatus、NonEmptyStr/ID 和 AgentPermissions 留在精简后的 `models`；AgentPermissions 与旧 AgentTask/PromptBundle/SkillBundle 脱钩。
- `monitoring` 精简为消息契约、正文补全共用算法、历史消息 normalizer；父包只导出这些保留对象。可沿用原模块路径，避免为更名制造大范围无意义改动。
- `runtime_scheduler` 留下 V2 使用的调度服务、schema 和 repository，移除旧 document provider、旧 HTTP 视图和 V1 分支。保持原持久化状态语义。
- `model_usage` 留下事件 schema、仓库、定价、聚合服务，移除旧网关 recorder 和对应父包导出。
- `horizontal_collection` 留下 collector/compiler/schema/registry/generated catalog；旧 Blackboard manifest adapter `artifacts.py` 退出。

这不是保留旧运行系统。若共享算法所在目录叫 monitoring、models，不要求改名；验收以没有旧执行/装配路径为准。纯契约中因当前消费者或历史数据解释而需要保留的枚举值也不要求“清零 v1/legacy 字符串”。

## 4 V2 依赖的具体拆除方案

### 4.1 调度器及父包

证据：`runtime_scheduler/v2.py:25` 继承 `UnifiedRuntimeSchedulerService`，build 传入空的旧 runtime/monitoring 服务及 `ManagedDocuments`；但 `service.py:13,33,34,47` 仍导入 Blackboard 错误、旧 MonitoringBusService、PersistentRuntimeExecutionService 和旧 document provider。`schema.py:22–27` 仍引入旧 poll/binding、TradingRecord、RuntimeExecutionObservation 和 ExecutionExceptionLog。`runtime_scheduler/__init__.py:3–4` 又导出旧 DashboardStateAPI 与 document provider。

真实 V2 消费者包括 `v2_control/worker.py:12` 的 scheduler repository、`v2_control/service.py:16` 的 TickerRunStatus；因此只保留 `v2.py` 而删除其余文件会同时影响控制服务与 API。

实施动作：

1. 原地精简父包 exports，先消除对 `api.py`、`documents.py`、旧 loop 和旧 DTO 的强制导入。
2. 从 service 中移除 V1 `from_settings` 装配、旧文档初始化/查询、旧消息执行和旧交易视图分支；V2 必须沿用 Message Bus v2、RuntimeCoordinator、admitted activation 和控制状态。
3. 若 V2 确需一个文档 provider 协议，留轻量协议；不为没有实际用途的 ManagedDocuments 另建存储层。消除 Blackboard RunNotFoundError 的无必要依赖。
4. 删除 schema 中无保留消费者的旧快照/交易视图类型；当前 V2 调度状态、枚举序列值、仓库 JSON 读取和已有历史行必须可用，不在退役中改写数据库。
5. 删除 `documents.py`、`api.py`、旧 `cli.py/__main__.py`；`loop.py` 在移走旧 loop 测试后退出，V2 `main` 自己的轮询仍保留。

验收：V2 控制启停、admitted ticker 过滤、恢复、消费提交、故障隔离、调度锁、关闭服务的行为与清理前一致；保留入口不再导入 `persistent_runtime`、Blackboard 或旧 MonitoringBusService。无需为了保留旧 Dashboard 查询而保留整套旧 DTO。

### 4.2 Data MCP 角色权限

证据：`data_runtime/policy.py:18,151–158` 构造策略时真实读取 `agents.config.default_agent_registry()`，取每个角色的 `runtime.allowed_tools`。`agents/__init__.py:11,17,23` 使这条路径额外导入 market_trace、runner 和 MAF runtime。

只提取 C1/C2/C3/O4/C5 仍使用的角色工具集合，不复制整套 AgentRuntimeConfig、Prompt IDs 或 ReAct execution_mode。保留 policy 内原有 C4/O0/O1/O2/O3/W3 组合、node exclusions、Data MCP excluded tools、ticker 规则、能力签名与时间约束。当前未提交的 D2 `O1_OPEN_DISCOVERY` 和 D3 discovery/planning/build/integration 节点同样纳入比较；不能拿附件旧枚举覆盖本地。

验收直接比较迁移前后所有受支持 `(workflow,node,role,ticker)` 的有效 allowlist 和排除项；O3 planning 空工具等特例保持原值。补迁 `tests/test_silicon_analysts_provider.py:5,147` 的旧 registry 断言，并保留 `test_data_mcp_runtime`、O4 操作 MCP 和当前 v2.1 权限测试。**禁止用允许全部 tools、关闭 capability 校验、运行时 fallback 到旧 registry 来消除报错。**

### 4.3 monitoring 的共享代码

保留三个实际共享文件：`schema.py`、`media_enrichment.py`、`normalizer.py`。`content_enrichment` 多文件和 Message Bus v2 dedup 调用正文算法；CDECR 历史加载器调用 normalizer；计费/调度仓库调用 `canonical_json`。

先精简 `monitoring/__init__.py:10,25` 的旧 repository/service 导出，再退出 `cli.py`、`repository.py`、`service.py`、`viewer.py`、`stocktwits_durable.py`、旧 collector（以现存文件为准）以及根级 `monitoring_viewer.py`。不要删除 normalizer 内不同源格式处理函数，它们仍用于历史初始化数据。

保留 `tools/providers/monitoring.py`：其“monitoring.*”工具名实际调用 `MessageBusV2Service`，O4 Operations MCP 也使用这些工具；名称没有 v2 不代表旧实现。既不删除这些工具，也不把 Message Bus 管理写操作引入只读 Data MCP。

验收覆盖历史新闻规范化、正文资格、最终出版商 URL、dedupe、原始消息/标准消息/正文结果边界。退役不调整 HTTP、浏览器、站点策略或 fallback 语义。

### 4.4 父包、模型契约与 prompt/skill 系统

`workflows/__init__.py` 当前惰性，删去旧 `_EXPORTS` 即可，不需要再实施一遍“全包 lazy 化”。`agents`、`monitoring`、`runtime_scheduler`、`model_usage`、`models` 和 `horizontal_collection` 仍须处理当前实存的 eager 导出。

`models/contracts.py:11–12` 为旧 AgentTask 引入 PromptBundle/SkillBundle；`models/__init__.py:75–76` 也直接导出它们。V2 用到的是 AgentPermissions 等基础类型；把这些类型保留在不依赖旧 AgentTask 的轻量文件，精简父包 exports，随后退出旧 contracts 其余部分和旧模型文件中无保留消费者的定义。

扫描未发现 V2 源码直接调用旧 `models.output_schemas`；它的源码消费者是旧 ReAct/output_validation。scheduler 清除旧文档 DTO 后，`models/documents.py`、`blackboard.py`、`agent_outputs.py`、`validation.py`、`output_schemas.py` 可随其旧消费者退出，剩余 current tests 使用的纯类型先迁移；不删 V2 自己的 Codex schema。当前 AgentName/ResultStatus 的序列化值保持一致，不重编码历史工具请求。

共享类型拆完后，`src/doxagent/prompts/` 与 `src/doxagent/skills/` 的旧 registry/assembler/injection/lint 和 bundle 类型可整体退出；不保留形式上能启动 V1 的兼容外壳。其惰性/TYPE_CHECKING 导入不能被误写为全部即时运行依赖。

### 4.5 model_usage 与旧网关

证据：`model_usage/__init__.py:4` → `recorder.py:7` → `gateway.schema` → `gateway/__init__.py` → providers/tracing，是父包造成的旧网关加载。实际 V2 D1 `node_runner.py:533–534`、D2 `runner.py:525–526` 直接写 `ModelUsageEvent`，没有调用旧 recorder。

移除 `ModelUsageRecorder` 父包导出、`recorder.py` 及 gateway-facing 专用测试，保留 `pricing.py/repository.py/schema.py/service.py` 及 Codex 写事件能力。之后删除 `gateway/`、旧 Anthropic/OpenAI/Bailian 网关和 LangSmith 包装；不要误删 V2 的 Bailian HTTP transport、CDECR OpenAI 客户端或当前计费/成本 API。

验收比较同一 fake Codex response 的原始 usage、cached/non-cached input、input/output tokens、invocation 身份、重试/失败状态及成本聚合；不是只验证计费页面返回 200。

### 4.6 horizontal、annotations 与独立 StockTwits

`horizontal_collection/__init__.py:3` 导出旧 `artifacts.py`，该 adapter 只把 manifest 写入 Blackboard。退出该导出和 adapter，保留 V2 collector/compiler、304 项指标 catalog、当前 Manifest schema 和对应生成依据。`test_horizontal_collection_contracts` 只移除 Blackboard adapter 测试，不能整文件删除。

`annotations/` 的当前源码调用者全部在旧 agents/runtime；包括 postgres store。和旧引擎及 `test_evidence_ref_restructuring` 一起退役；若执行前新改动引入 current 消费者，只重审这个具体新增引用。

`stocktwits/` 的源码消费者仅是旧 monitoring 及自身独立 CLI/crawler；V2 adapter 位于 `message_bus_v2/adapters.py:700` 附近，独立实现请求和解析。将旧独立 crawler、`scripts/stocktwits-crawler.cmd/.ps1`、`test_stocktwits_crawler.py` 作为配套退役项；保留 V2 adapter 使用的 RapidAPI/public URL/key 等配置，避免因变量前缀相同而删错。

## 5 源码与部署资产的最终处理清单

表中“删除”均指**批准后按批执行的目标状态**，不是本轮已执行动作。目录下缓存与源码一起处理时，也必须按第 8 节的链接/路径规则展开，不能跟随外部连接。

### 5.1 直接随旧能力退出

| 路径 | 最终处置 | 前置条件 |
| --- | --- | --- |
| `frontend/dashboard/` | 删除源码、独立锁文件、组件及本地安装/构建产物 | 保存必要已批准参照截图；V2 无跨目录引用 |
| `src/doxagent/dashboard_api/` | 删除 11 个旧 API 文件 | 拆完第 6 节 5 份混合测试 |
| `Dockerfile.dashboard` | 删除 | 退出引用它的旧 Compose/文档 |
| `src/doxagent/agents/` | 删除 runner、MAF/ReAct/memory、market_trace；权限迁出后整目录退出 | 完成第 4.2 节 |
| `src/doxagent/adapters/` | 删除旧 financial-services/vibe-trading adapters | 当前数据 tools/providers 保留，旧测试同步退出 |
| `src/doxagent/workflows/document1/`、`document2/`、`initialization/` | 删除自建编排 | 父包导出和混合测试已经清除 |
| `workflows/{global_research,normalizer,storage,checkpoint_repository,schema,output_validation,errors}.py` | 删除旧基础编排 | 保留全部 `codex_*`；确认保留脚本/测试无导入 |
| `src/doxagent/{blackboard,context,workflow_memory,audit,examples}/` | 删除旧状态、记忆、审计和示例 | scheduler/horizontal 已拆；共享基础类型保留 |
| 根 `examples/` | 旧 Blackboard 示例退出 | 相关 README 和 Docker COPY 同步收口 |
| `src/doxagent/persistent_runtime/` | 删除旧消息执行、workers、replay/datasets | scheduler 和 Event Library 混合测试迁完 |
| `src/doxagent/revenue_audit/` | 删除旧收益模拟/回放 | V2 pnl、账本、执行和成本 API 保留 |
| `src/doxagent/annotations/` | 删除旧文本/证据注释 | 旧 agents 与专用证据测试退出 |
| `src/doxagent/gateway/` | 删除旧网关/providers/tracing | model_usage recorder 导出和测试已退出 |
| `prompts/v1/` | 删除旧 ReAct 资源 | 不动 CDECR 内 prompts/v1 和 Codex compatibility |
| `src/doxagent/prompts/`、`skills/` | 删除旧注入和注册系统 | AgentPermissions/基础模型不再导入旧 bundle |
| `src/doxagent/stocktwits/` | 删除旧独立采集器及仓库 | 当前 Message Bus v2 adapter 和配置保留 |
| `src/doxagent/monitoring_viewer.py` | 删除旧包装入口 | 随旧 viewer 退出 |
| `src/doxagent/core/` | 删除无消费者的空 baseline 包 | 当前扫描无源码 import；不新增替代框架 |
| `src/doxagent/{debug_viewer,execution_pool}/` | 清理仅剩 `__pycache__` 的幽灵目录 | 当前无 `.py` 源码，按缓存处理 |

对已没有 current 消费者的 V1 纯契约、专用测试和文档，不要求额外逐个证明线上从未访问。完成共享依赖拆除、测试职责核对和包/入口验收就应退出，不能永远停在“可能还有人用”。

### 5.2 混合目录逐文件收口

| 混合目录 | 保留 | 退出 |
| --- | --- | --- |
| `models/` | current 基础枚举、ID、AgentPermissions；必要历史解释字段 | V1 AgentTask/Result、Blackboard、旧文档/输出 schema、旧 validation 及 bundle 导出，按消费者拆完后退出 |
| `monitoring/` | schema、media_enrichment、normalizer 和轻量父包 | 旧 CLI/service/repository/viewer/stocktwits_durable 等运行实现 |
| `runtime_scheduler/` | `v2.py`、精简 service/repository/schema、必要轻量 exports | 旧 documents/api/cli/__main__/loop、旧查询 DTO、V1 装配与执行分支 |
| `horizontal_collection/` | collector/compiler/registry/schema/generated catalog、当前类型和测试 | Blackboard `artifacts.py` 与父包导出 |
| `model_usage/` | pricing/repository/schema/service 和 current exports | `recorder.py` 与 ModelUsageRecorder 导出 |
| `workflows/__init__.py` | 当前 Codex 父包边界 | 旧 `_EXPORTS`，不保留导入后再失败的遗留名称 |
| `settings.py`、`.env.example` | 当前功能实际使用的字段、当前 aliases/default paths | 只由已退役代码使用的 React/旧网关/旧 UI/独立 polling 字段及校验/副作用 |
| `pyproject.toml`、`uv.lock` | current 依赖、CLI、资源打包和真实数据工具能力 | 已无消费者的 V1 依赖；旧项目 description 改为当前 V2 |

环境变量不能仅按 `DASHBOARD_`、`STOCKTWITS_`、`LANGSMITH_` 等字符串匹配删除。已核实 `api_v2/app.py:78–79`、`production_v2.py` 仍使用 Dashboard 前缀的 Supabase URL/publishable key；StockTwits key 和 URL 仍为 V2 adapter 所用。`settings.py` 的 LangSmith 设置/环境写入和旧 ReAct 参数则应在旧消费者退出后收口。

### 5.3 部署入口需要区分旧装配与 V2 运维

`docker-compose.yml` 虽是旧默认装配，但其当前内容混合了 Message Bus v2、O4、Codex worker 和旧 Dashboard scheduler。不能只因文件名旧就认定里面全是 V1，亦不能保留它继续成为误启动入口。

最终删除旧 `docker-compose.yml`、`docker-compose.ticker-init.yml`、`docker-compose.v2-backend.yml` 和默认指向旧 monitoring CLI 的根 `Dockerfile`。其中仍需使用的独立本地 V2 启动方式，应先用 `Dockerfile.v2` 与 current 路径建立明确的开发入口或文档命令；保留已有本地环境数据，不顺便停服务/删卷。`Dockerfile.codex-worker` 是旧独立 V2 Worker 装配，统一至生产共享镜像或明确开发构建后退出；它不是 ReAct 引擎。

**本次保留：**

- `docker-compose.v2-production.yml`、`deploy/docker-compose.server.yml`、`Dockerfile.v2`、`frontend/v2/Dockerfile`、V2 nginx。
- `deploy/docker-compose.initialization-repair.yml`、修复 agent 镜像和 Guardian/source-worktree 运维能力。
- `deploy/docker-compose.hk.yml`、HK cutover、broker firewall/IBKR、SSH/远程桌面运维文件。
- Site Access、Chrome supervisor、登录维护、egress、sandbox/seccomp/AppArmor、浏览器版本和控制文件。
- 新增 `deploy/Dockerfile.codex-sdk-upgrade`、`docker-compose.codex-gpt6.yml`、`Dockerfile.news-source-update`、`docker-compose.market-news.yml` 及对应验收材料。是否已在线上合并到标准镜像，本轮没有核实，不能据此退役。

Barron's sidecar/control 等过渡文件不是 V1 编排。本轮保留它们的运维能力，不用“当前 base Compose 没引用”证明无人使用。以后统一 Site Access 部署可以另行退出冗余文件；不要求这件事阻塞已经明确的 V1 删除批次。

### 5.4 Python 依赖与打包

旧执行/网关和相关测试删除后，移除直接依赖 `agent-framework-core`、`anthropic`、`langsmith`，用正常 uv 流程重新解析锁文件。若 current SDK 的传递依赖仍带入某个包，区分“删除直接依赖”与“强迫包从环境消失”；不得手工裁剪传递依赖。

保留 `openai`、`openai-codex`、`mcp`、Pydantic、httpx、FastAPI、Playwright、cryptography、jsonschema、正文抽取、压缩、市场数据和 psycopg 等 current 使用的依赖。生产 IBKR 的 `ibapi`/protobuf 是 `Dockerfile.v2` 的官方 TWS 安装步骤提供，不在主 dependency 列表也不能遗漏。

确认 wheel/sdist 内没有旧 V1 实现，但含完整 CDECR catalogs/prompts 和所需 JSON/schema 等资源。仓库文本“不再引用 V1”不足以证明 wheel 没带入旧代码；不要直接运行当前已过时的 `dist/` 包作为最终验收。

## 6 测试和脚本清理的完整边界

### 6.1 旧 API 的 5 份混合测试

8 份 `tests/test_dashboard_*` 专用测试可随旧 API 删除；下列 5 份先拆，不能整文件删除：

| 文件 | 迁移内容 |
| --- | --- |
| `test_codex_document1_workflow.py:21–22` | 旧 create_app、CodexDocument1RunService 的 HTTP 层断言退出；保留 D1 编排/runner/资源/计费断言 |
| `test_codex_document2_workflow.py:46` | 旧 CodexResearchLaneService 接口断言退出；保留 D2 业务和供 v2.1 使用的 fake helpers |
| `test_codex_research_lanes.py:27` | 用 current service/API 覆盖仍有效的 lane 行为，删除旧 HTTP 适配 |
| `test_message_bus_v2.py:13,42` | 移走旧 create_app、persistent runtime 与 DashboardStateAPI 用例，保留 Bus 配置/轮询/发布/消费业务 helpers |
| `test_crawler_plane.py:29,36–38` | 移走旧 API、runtime、DashboardStateAPI 集成，保留 crawler 注册、执行、归档和隔离测试 |

HTTP 迁移只针对 current 仍支持的操作；V1 已退出接口不必在 V2 重建同名路由。`-k` 过滤不是解决方案，顶层 import 仍会在收集时执行。

### 6.2 原方案遗漏的混合测试

| 文件 | 必须保留/迁移的部分 | 可退出的部分 |
| --- | --- | --- |
| `test_phase25_runtime_scheduler.py` | V2 共用调度/控制语义及用于初始化的 fake scheduler/provider | 旧文档、旧 runtime/monitoring、Dashboard/loop 专用用例 |
| `test_horizontal_collection_contracts.py` | 指标 catalog、target registry、共用 schema | Blackboard manifest adapter |
| `test_model_usage.py:10` | 事件持久化/定价/筛选/current Codex 计费 | 旧 ModelGatewayAgentRunner/recorder 的断言 |
| `test_event_library_incremental_consumers.py:23,29` | Event Library、CDECR、Codex D2 的输入/发布/增量消费 | 旧 RuntimeSourceMessage/W1 worker 分支；需 current 等价场景时迁至 Runtime v2 |
| `test_silicon_analysts_provider.py:5,147` | 当前 Data MCP provider、工具契约和有效权限 | 对旧 AgentRegistry 的直接依赖，改为 current policy 断言 |

不能按 `phase`、`document2`、`initialization`、`runtime` 文件名批量删测试。反过来，V1 已退役的 ReAct 行为也不需要强行迁到 Codex。

### 6.3 测试之间的导入必须一起迁移

本地重新发现的链路包括：

- `test_ticker_initialization_runtime_inputs.py:45`、`test_ticker_initialization_startup_integration.py:30` 从 `test_phase25_runtime_scheduler` 导入 helpers。
- `test_codex_document2_v21_orchestration.py:32`、`test_d2_discovery_checkpoint.py:42` 从 D2 workflow 测试导入 helpers。
- `test_codex_document3_v21_orchestration.py:34` 从 D3 workflow 测试导入 helpers；v2.1 maintenance 测试继续引用 v2.1 orchestration。
- `test_closed_cycle_contracts`、`test_message_admission`、`test_runtime_orchestration_bus` 和多个 `tests/v2_backend` 测试从 `test_message_bus_v2` 导入 helpers。

将仍被 current 测试使用的 helpers 移入轻量 `tests/fixtures`，解除它们对旧 API、旧 runner、旧 document provider 的间接依赖。不要为一个 helper 留下整份 1,000 行旧测试，也不要在 helper 中残留导入 V1 的 fixtures。

`tests/fixtures/phase1_contracts.py`、`required_output_schemas.py` 是旧模型夹具；scheduler current helpers 迁完后，没有 current 消费者的部分可以退出。`test_document1_node_contract_matrix`、`test_document2_node_contract_matrix`、`test_document2_canonical_contracts`、`test_initialization_characterization`、`test_phase13_real_workflow` 已含顶层 retired EvidenceRef skip。它们应随旧实现清理，不能将“跳过”当永久兼容方案。

`test_baseline.py:18` 指向本来就缺失的 `dev_plan/PHASE0_BASELINE.md`。更新为 current V2 工程基线，不新增空文件骗过断言；保留包版本、依赖边界等仍有价值的测试。

### 6.4 旧专用测试和脚本退出清单

退役对应实现后，退出 Phase 1–24 中纯旧 contracts/gateway/blackboard/MAF/ReAct/workflow/replay/monitoring/viewer 用例；Phase 25 按上一节拆。退出 `test_react_memory*`、`test_workflow_memory`、`test_workflow_normalizer`、`test_revenue_audit`、旧 output schema/Document3 deblocking/EvidenceRef 测试和独立 StockTwits 测试。确切集合由职责与 import 确认，不对文件名展开通配删除。

退出下列旧入口及配套结果：

- `scripts/monitoring-bus.cmd/.ps1`、`monitoring-viewer.cmd/.ps1`、`stocktwits-crawler.cmd/.ps1`、`validate_react_memory_real_tools.py`。
- `eval/export_brief_state.py`、`run_blackboard_eval_once.py`、`resume_blackboard_run_once.py`、`run_document1_document2_smoke.py`、`run_document2_expectation_units_smoke.py`、`run_document3_smoke.py`、`run_runtime_scheduler_mu_e2e.py`。
- `eval/blackboard_*` rubrics/gates/contracts/records、`runtime_replay_datasets/`、旧 `document2_eval/`、旧 `trajectory_eval/`，将有诊断价值的结果按第 8 节归档，执行脚本退出。

保留 Codex smoke/pilot、CDECR catalog/eval 工具、W3 pilot、current 内容重放、schema 生成、指标 catalog 生成、迁移检查、运维与交易测试工具。`scripts/replay_content_enrichment.py` 引用 media_enrichment 是共享算法，不是理由删除它。`tools/mock` 和当前 fake SDK 也是有效测试基础，不因“mock”退出。

## 7 必须保留的资源与历史

### 7.1 v1 和 legacy 命名的必要资源

保留完整 `src/cdecr/`，包括 `prompts/v1`、`catalogs/v1/units.json`、v2 catalogs。single/cross document、parent occurrence、bulk epoch 等通过 `importlib.resources` 使用这些 prompt；`mention_finalization.py:24` 使用 units。约 185.58 MiB 的 catalogs/v2 是知识库，不是临时缓存。

保留 `prompts/codex_v2/document1/compatibility/legacy_document1`、D1 global_research/market_situation bundle、全部当前 D2/D3/O2/O4 资源；保留 `prompts/persistent_runtime_v2`、`prompts/initialization_repair`、`prompts/research_lanes`。不同子系统的资源版本不会因 ReAct V1 退役而自动失效。

### 7.2 文档中的真实构建和测试输入

| 路径 | 保留理由 |
| --- | --- |
| `dev_plan/workflow_v2/api_contract/` | 前端 TS alias/schema、backend wire schema 和 Docker COPY 输入 |
| `dev_plan/workflow_v2/DOXAGENT_V2_API_CONTRACT.md` | `scripts/generate_v2_schema.cjs:93` 读取路由表；原方案只突出 api_contract 目录，须补保护本文件 |
| `dev_plan/workflow_v2/d1_horizontal_indicators_collection.md` | 指标生成脚本和 304 项 catalog 对照测试 |
| `dev_plan/workflow_v2.1/` 当前合同和方案 | 当前 D2/D3 staged 实现依据；新增 `document3_v2.1_contracts.schema.json` 是 `test_codex_document3_v21_orchestration.py:226` 输入 |
| `dev_plan/CDECR/baselines/mu_2026-06-25_step2_eval_manifest.json` | CDECR CLI 和测试输入，旧日期不能删 |
| `eval/trade_execution/20260928_audit/ledger_redacted.json` | `test_trade_execution_shared_equity.py:379,408` 直接读取 |
| `eval/persistent_runtime_w3/corpus_v1.json` | current W3 pilot 默认 corpus |
| `eval/cdecr_relevance_filter/mu_relevance_30_v1/` | relevance eval、Gold 重审脚本的默认数据集 |
| `tests/fixtures/event_library/mu_v1`、crawler packages 和 current fixtures | 当前 Event Library、初始化和 crawler 测试使用 |

`scripts/generate_v2_schema.cjs` 注释称使用 dashboard 编译器，但实际 require 指向 `frontend/v2/node_modules/typescript`；本地没有这一条 V1 构建依赖。只改失实注释，不迁走 current 工具。

保留 `references/fonts/` 的工程资源，若存在复制后等价的 V2 public 字体也不在本次引入字体设计调整；`frontend/v2/.font-download` 的下载中间件可重建。保留全部 15 份 `supabase/migrations` 历史，不 DROP 表、不擦 migration 历史、不改已执行迁移。

### 7.3 需要退出主树的旧设计材料

旧 ReAct/Blackboard PRD、ReAct Memory/Node Config/Workflow Memory 文档、旧 Dashboard PRD/API 契约、旧收益审计和部署手册退出 current 文档导航；与已退休能力完全对应的正文可移出主树。历史结论留一个短索引，注明 Git 提交或项目外存档位置。

`dev_plan/PRD.md`、`FRONTEND_PRD.md`、`DOCUMENT3_PRD.md`、`WORKFLOW_REVISION_PLAN.md` 等不能仅以旧标题判定内容：保留 current PRD 明确继承的业务要求，删除其旧运行说明或将原稿归档。不把整个 `dev_plan` 删除。当前 V2 PRD、`v2_design.md`、production/backend runbook、消息正文/Site Strategy 运维、当前事故/部署报告及本轮方案保留。

`frontend/v2/AGENTS.md:15` 的“必须访问 V1 真实网页”在彻底退役后无法履行。改为已批准的固定参考截图或现行 V2 设计依据，保留其他用户约束；已有 `dev_plan/v1_frontend_reference` 中经筛选的参照可以承担这一目的，不需要继续运行 V1。删除重复旧验收截图时不同时删掉唯一被批准的参照。

README 改为明确 V2 安装、独立锁文件、生产/开发入口、测试方法、目录说明和历史归档索引；清除 Phase 0–25 当成 current 架构的说明。

## 8 本地临时文件和历史产物清理

### 8.1 日期只是筛选条件

以审查日回退一个日历月，参考截止为 **2026-09-04 00:00，Asia/Shanghai**。对诊断/运行记录使用文件内部时间、manifest、Git 提交和最后写入的综合证据；对目录不能只看目录 mtime。文件复制/解压会刷新 mtime，老数据库也可能仍在配置里使用。

明确的废弃测试 workspace、可重建缓存和 V1 安装产物不必因最后写入恰好晚于截止几分钟而继续保留；最近的真实部署/故障排查则保留到对应工作完成。日期筛选不覆盖有 current 路径引用、认证身份或正式状态的资产。

### 8.2 可重建或明确无业务作用的清理批

| 路径/精确集合 | 本地可读取逻辑大小 | 批次决定 |
| --- | ---: | --- |
| `.tmp-uv/` | 1,323.54 MiB | uv 缓存、构建 staging 和历史 pytest 产物；按子目录展开后清理。访问拒绝项不使用强制提权/改 ACL 掩盖 |
| `.uv-cache/` | 687.31 MiB | 项目级 uv 缓存；无安装/构建占用后清理或定向 prune，不清 Codex/系统的公共缓存 |
| 旧 `frontend/dashboard/node_modules` 与 `dist` | 256.45 + 1.26 MiB，下限 | 随旧 UI 删除，保留 V2 安装依赖 |
| 根 `dist/` | 88.78 MiB | 7 月旧 wheel/sdist 可重建，清理旧分发包 |
| `.mypy_cache`、`.ruff_cache`、`.pytest_cache` | 76.65、0.27 MiB；pytest 大小未确认 | 清理已结束任务的缓存；不可读项仍登记，不声称已不存在 |
| `.test-tmp/`、11 个 `.pytest-codex*` | Git blob 7.41 MiB；本地不可读 | 从版本控制移出测试垃圾；物理删除需可读取/可验证的路径清单，独立于 Git 状态中的 D |
| 项目字面目录 `%SystemDrive%/` 的 3 个缓存数据库 | 0.94 MiB | 清理这个项目内目录，不能展开成真实系统盘 |
| `.tmp/mypy_freshness_final/` | 59.81 MiB | mypy 缓存，无需保留全库 |
| `.tmp/pytest-*`、`.tmp/pytest_*` 的已枚举历史组 | 见附录 | 保存必要通过/失败摘要后清理 pytest 生成的复制库与 workspace，不对其他 `.tmp` 做 glob 删除 |
| `.tmp/cdecr-{full,full-final,p0-1,focused-2,cross-focused,degradation,fix-two,audit-pytest}/` | 共约 184.96 MiB | 内容是历史测试产物；与正式 CDECR registry/预构建区别开，保存必要摘要后清理 |
| `.tmp-cdecr-*`、`.tmp-pytest-*`、`.tmp-parent-v2`、`.tmp-codex` 等根级旧测试目录 | 见附录精确目录 | 目录内 pytest/fake 状态可清；有 live pin/唯一资料的项目先转归档，不一刀切 |
| `src/tests/scripts/deploy/eval/pilot_runtime` 的 `__pycache__`、旧 `.pyc` | 未单独承诺总量 | 删除已结束任务的生成物；保留 Python 源码，不沿目录连接进入第三方环境 |
| `frontend/v2/.font-download/` | 5.18 MiB | 字体已落入 current public 资源后清下载中间件 |

这里没有把 `.venv`、`frontend/v2/node_modules` 或 current `frontend/v2/dist` 纳入默认清理：它们是当前开发/预览环境。若之后选择重建环境，单独做环境更新，不与 V1 退役混合。`.venv` 的现有导入失败也不能通过先删除它来掩盖。

附录列的是候选集合，实施时将允许删除的子目录/文件展开为明确清单。仅作缓存清理的批次只需确认路径、类型、无当前作业占用和可重建性，不以完整生产业务回归为前置门槛。

### 8.3 导出 node_modules 是外部 Junction

本地实测：

```text
exports/mu_enrichment_20260915_support/node_modules
  LinkType = Junction
  Target = C:\Users\WEIXUANXIE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules
```

附件 Git 快照中约 183.83 MiB、4,462 个依赖文件是误跟踪资产；退出 Git 索引与本地文件处理是两件事。本地盘点跳过这个连接，没有把公共目标计入项目大小。

最终处理是：保留必要导出脚本/复现说明；让这些依赖 blob 退出当前版本；本地移除 Junction **本身**。绝不递归删除、移动或备份其 target。若执行时节点变成普通目录，再按实际类型展开；不沿用当前连接假设。公共 target 不属于本次项目清理范围。

`export_csv.mjs:4` 依赖 `@oai/artifact-tool`；归档必须记录可用依赖来源和版本，删除连接后不能称这个历史脚本仍能直接运行。这些 Git blob 退出不会自动缩小 Git 历史，亦不能把 183.83 MiB 计作本地可回收磁盘。

### 8.4 归档后移出主树的历史证据

这些资产可退出主树，但保存证据与单纯删缓存不同。优先记录最少有用的 run/版本/时间、结果摘要、必要样本、复现输入、文件哈希和来源；没有诊断价值的重复日志/中间产物不要求全部保留。

| 路径 | 可读取大小 | 最终处理 |
| --- | ---: | --- |
| `eval/brief_state_exports/` | 42 文件，420.90 MiB | 6 月 V1 Brief State/loop/poll 快照；留 run 索引与代表性坏例，必要原始证据项目外压缩归档，移出工作区 |
| `.tmp/langsmith_audit/` | 81.08 MiB | V1 tracing/raw run 证据随旧工具归档；不留全部重复 response 在 current tree |
| `.tmp/blackboard-status-*`、`.tmp/run_*.remote.json` 和旧 blackboard eval/resume logs | 多份 5–14 MiB 快照 | 归档必要代表性证据，重复快照/空日志清理；显式路径展开，不扫描删除任何同名业务运行目录 |
| `references/external_agent_sources/` | 105.07 MiB | 旧外部框架参考副本，可从来源重新取得；保存 commit/URL 说明后移出项目，字体目录独立保留 |
| 根 `recovery/codex_ephemeral_20260725`、`20260728` | 合计约 2.52 MiB | 历史恢复材料先索引/归档；当前无恢复消费者后移出，不用 recovery 前缀证明可直接删 |
| `.tmp-remote-wip-snapshot-20260708-010624/` | 1.86 MiB | 包含 server WIP patch/bundle，属于可能唯一的未提交源码证据；保存到项目外再移出 |
| `.tmp-deploy-latest.tgz`、`.tmp-remote-compare/` | 约 0.60 + 0.09 MiB | 旧部署快照/比对；已被版本化源码或新基线替代后归档/清理 |
| 旧 `eval/document2_eval`、`runtime_replay_datasets`、`trajectory_eval` 与 V1 文档图集 | 单独统计 | 保留结论/必要坏例，退出旧执行工具；不要放进 current 测试默认路径 |

九月的正文排查导出不是“一个月前的垃圾”，但本来就是可迁出的诊断资产：`exports/body_enrichment_implementation_20260912` 约 150.50 MiB（含大量诊断文件）、`body_enrichment_audit_20260912` 36.01 MiB、`mu_enrichment_20260915_support` 可读取非链接文件约 21.06 MiB，以及 body_delivery/body_trial、free_publishers 等。保存对应 current 修复结论和有价值的 URL/正文/失败样本，原始大文件归档后移出主树。是否当批归档取决于相关诊断已完成，不要求这些文件永久常驻，也不把它们当 V1 删除项。

`.tmp/freshness-release` 的 195.22 MiB 源码复制、`.tmp/codex-gpt6-upgrade` 的 220.15 MiB baseline、`.tmp/exports` 的 526.73 MiB 控制库快照均属九月/十月的部署或审计材料。其保留周期跟随当前验收/回退需求；等正式版本、部署记录和可恢复副本就绪后归档，不混入“一个月前”删除批。

### 8.5 必须保护的本地状态和工具链

| 路径/类型 | 当前证据 | 处理 |
| --- | --- | --- |
| `.uv-python/` | `.venv/pyvenv.cfg` 指向其 CPython 3.11.14 | 保留。不是下载缓存；删除会破坏现有解释器 |
| `.venv/`、V2 node_modules | current 开发依赖；部分 `.pth` 访问拒绝 | 保留；缓存清理前对具体外部/安装引用核实，不能假定虚拟环境完全健康 |
| `.tmp/codex-runtime.sqlite3*` | `.env`、`.env.v2` 都明确配置该路径，即使主库 mtime 很旧 | 保留主库及 WAL/SHM；不能按年龄删 |
| `.tmp/ticker_initialization/control.sqlite3*` | 两份本地环境明确配置 | 保留初始化控制/lease/dispatch/recovery 状态 |
| `.tmp/{message_bus_v2,persistent_runtime_v2,runtime_scheduler,model_usage}.sqlite3*` | current 默认配置路径；本地有实存文件 | 保留；本轮没核实每库实时活动，不声明为空/可删 |
| `.tmp/.control/`、event-library、codex-workspaces、crawler-plane、site-strategy 根 | 控制/冻结/current 默认存储 | 无论是否在本轮可读取集合中出现，均不按 tmp 前缀清空 |
| Reuters Chrome identity/sidecar 等浏览器 profile | 本地两组约 72.86、130.63 MiB | 保留登录身份；生命周期/使用者未确认前不归档或复制活动 Profile |
| `.tmp/{sg-deploy,hk-deploy,ibkr-mcp}`、deploy/secrets、认证根 | 名称清单中含认证/凭据/观察库等文件 | 不把整个目录当纯 staging 删除，不读取/公开凭据内容；按实际资产分拆 |
| `supabase_write_failures`、`supabase_payload_logs`、spool | `postgres.py:22–23` 是正式默认路径 | 未交付/待重试负载先保留，不按旧日期清空 |
| CDECR prebuilt、真实 MU/RKLB registry/runtime/recovery artifacts | 初始化/pin/复现可能使用 | 按 exact run/reference 确认，已废弃副本归档；不与 pytest engine.sqlite3 混同 |
| `.d3_v21_preservation.json` 与未提交 D2/D3 代码/测试/方案 | 2026-10-04 current 工作 | 保留，不移入历史归档 |

本地配置证明路径“仍被配置引用”，不等于证明正在运行；足以排除年龄批删除。正式状态若以后需要维护，应使用子系统自己的保留/GC 机制并单独授权，不在本方案用文件删除替代。

### 8.6 W1/W2 冻结评测输入的特殊处理

`eval/persistent_runtime_v2/w1_w2_mu_future_v1/manifest.json` 固定引用以下本地输入：

1. `.tmp/codex-workspaces-o3-acceptance/d3-mu-real-acceptance-2026090301-sol-high-or-v2/output/final/document3.json` 和 `.md`。
2. `.tmp/o2-mu-r2-deterministic-alignment-20260827-real-01/published/reference_view_agent_v1.md`、`known_event_index_v1.md` 和其 `event-library.sqlite3`。
3. current `prompts/persistent_runtime_v2` 中的 W1/W2 prompt 文件。

保留这个评测集的 `messages.jsonl`、`gold.jsonl`、case matrix、manifest、runner/validator 和上述可复现输入；其 `runs/` 约 37.00 MiB、`cache_ab/` 约 2.12 MiB 可保留代表性验收结果并归档多次运行副本。`future_v1` 是测试集版本，不是旧 ReAct。

若要释放 `.tmp` 的 O2/O3 目录，先把已冻结输入保存到评测专属固定位置或可用项目外归档，生成新的复现映射/manifest，保留原 provenance 与哈希；不要静默改写旧 manifest 的历史事实。再验证离线 runner 能找到输入；不能只留 Gold 和已指向失效绝对路径的 manifest。当前 prompt 的复跑与历史 prompt 的复现也应明确分开。

`scripts/check_ticker_initialization_migration.mjs:5` 则真实导入 `.tmp/ticker-init-sql-check/node_modules/@electric-sql/pglite`。这约 24.26 MiB 的测试依赖可重装，但清理前应把固定版本/安装方法放到明确测试工具链；不把它称为“没有消费者”。无需为了保留迁移检查让整个临时目录永久保留。

### 8.7 归档和安全删除的最小规则

实施批准后，为每批形成精确清单：根目录相对路径、实际类型（文件/目录/Junction）、跟踪状态、当前哈希或内容来源、处理动作、保留/归档去向、引用变更。普通重建缓存不要求逐文件哈希；源文件、唯一证据、冻结输入需要可恢复证据。

PowerShell 中使用 `-LiteralPath`；递归删除/移动前确认最终绝对路径位于批准的项目/存档根，且所有沿途 reparse point 已识别。对 Junction 只处理连接节点，不能进入 target。 `%SystemDrive%` 是本项目的字面目录，不是系统变量。不要使用 `git clean -xfd`、`reset --hard`、全局 `*.db`/`.tmp*`/`v1` 通配删除、Docker volume prune 或 Git 历史重写。

只有部分路径未能读取时，记录这些具体路径和失败原因，继续执行无关的已核实候选；不因此冻结全部清理，也不擅自接管 ACL/删除共享依赖。归档不留在项目根内换个目录名，否则没有达到移出项目的目标。

## 9 执行顺序和每批验收

### 批次 0 固定 current 代码与恢复基线

记录实际 HEAD、branch、index/worktree 差异、未跟踪与忽略资产及 source hash。单独保全 current D2/D3 v2.1 等未提交改动；Git 历史不能恢复 ignored 数据，patch 也不能涵盖全部 untracked 文件。不得把 3,232 个权限相关 D 自动 stage 成删除。

在能保留这些改动的工作区/隔离执行目录准备清理分支，命名 `codex/v1-retirement-cleanup` 或用户指定名称。执行目录必须使用批准的 current 源码快照，不能默认从 remote main 开新树而丢掉 staged 工作。这里的隔离执行目录是实施方法，不能导致 current 未提交代码被重置。

先记录已知基线问题：缺 PHASE0 文档、本地 binary import/`.pth` 访问问题、已有 retired suite skips。准备独立按锁文件安装的测试环境；不修改当前业务环境来“修复审查”。

### 批次 1 清理纯产物并防止回流

先处理确定的误跟踪测试垃圾、系统字面缓存、旧依赖 blobs、旧 wheel/sdist、可重建缓存，以及 `.tmp` 内已经枚举的历史 pytest 产物。相关 Junction 只删连接。

补 `.gitignore`/`.dockerignore`：`**/node_modules/`、`/.test-tmp/`、`/.pytest-codex*/`、`/%SystemDrive%/`、明确的历史导出/测试输出、`.uv-python`（忽略但保留本地）。快捷方式采取精确路径或按确认的工程规则，不无差别移除别处的有效 `.lnk`。

`.env.v2.example` 当前在本地存在但被 `.env.*` 忽略，未跟踪；审查其不含真实凭据后增加精确反向规则并跟踪模板。保留真实 `.env/.env.v2/.env.providers.local` 的忽略状态，不把模板修正扩展为真实凭据提交。已有 tracked 文件不会因 ignore 自动退出索引，需要明确撤销误跟踪。

验收仅确认这批没有触及源码、current 数据/环境或外部连接目标，Git/构建上下文不再包含指定垃圾。**这批无需等待真实模型/交易验收。**

### 批次 2 退出旧前端和旧 API

迁移 5 份混合测试，保存必要 UI 参照，更新 V2 AGENTS 中的旧网页要求；删除旧 frontend/dashboard、dashboard_api、Dockerfile.dashboard 及纯专用测试。

验收：保留测试收集不再要求 dashboard_api；V2 schema/typecheck/test/lint/build 通过；没有 V1 imports/aliases/CSS/DTO。current HTTP 行为测试使用 current 契约，旧路由不会重新引入。错误默认部署入口一并退出或暂时改为明确已退役，不留一个仍可误启动的旧默认服务。

### 批次 3 精简共享调度器

按第 4.1 节处理 service/schema/repository/父包/协议；迁移 Phase25 与初始化的 helpers；删除旧 scheduler API、document adapter、loop/CLI 和 V1 分支。

验收：current scheduler/control、managed initialization、runtime orchestration、消费状态提交、恢复/租约、并发与故障隔离相关测试通过。数据库 schema/JSON 兼容和 current enum values 不变。发现关联问题只回退这批，不清空数据库来消除报错。

### 批次 4 迁出权限并解除共享包导入

按第 4.2–4.6 节迁 role allowlist/基础类型，精简 models/monitoring/horizontal/model_usage exports；移除旧 recorder。逐组落地，对每个改变做相关权限、历史消息、正文、水平指标和计费测试。

验收：current 节点权限完全一致；current 保存/引用/计费能力完整；`agents`、gateway、Blackboard 已不再是 V2 启动依赖。不要新增失效 import 的吞异常 fallback。

### 批次 5 删除旧引擎和专用资产

删除第 5 节的纯旧实现、prompts/v1、旧 tools adapters、旧 tests/scripts/eval entrypoints；轻量共享模块按最终清单保留。删除已没有消费者的旧模型/Bundle 定义和 settings 字段，清旧 exports 和 console/module/script 入口。

验收：在没有 V1 文件的环境里运行保留源码 import/pytest collection、资源加载和实际 targeted suites；检查剩余 references 全部是刻意保留的历史文档/版本，不是启动入口。测试被删除要有职责对应记录，不能靠扩大 skip/忽略目录得到绿灯。

### 批次 6 锁文件、构建入口与历史资料收口

删无 current 消费者的 direct 依赖、生成 uv.lock；统一 production 和开发入口，退出旧/过渡 Dockerfile/Compose；更新 README、配置模板、API/指标生成说明、运维手册、current 导航和归档索引。每份重要代码变更追加 `changelog`，不覆盖其现有内容。

归档第 8.4 节旧资料，保留第 8.6 节冻结输入；新近诊断材料和 current rollout 待其验收/回退工作完成后归档。归档包放项目外，短索引写路径、hash、源版本和复现入口。

### 批次 7 总验收和交付

按第 10 节完成锁定环境、Python/frontend、资源/打包与构建配置验收，记录每批 diff、检查结果、历史基线失败和具体未处理路径。缓存容量按实测报告，不把 Git tree 缩减写成真实磁盘回收。

本地通过后交付 reviewable 改动及最终 manifest；是否推送 GitHub、部署线上、停止旧容器或删除远端数据，分别取得相应授权。即使以后获准部署，本地/服务器的环境路径和服务镜像可能不同，必须以当前运行基线安排逐服务替换，不覆盖外部管理的 env 或登录 Profile。

## 10 验收标准和回退

### 10.1 清理后的必要验收

| 验收面 | 必须得到的结果 | 范围说明 |
| --- | --- | --- |
| Git/current 源码 | V1 引擎、编排、旧界面和入口退出，current 未提交工作保全 | 清理分支 diff 与实际 approved manifest 对应 |
| Python 导入 | V2 API、Worker、scheduler、control、projector、初始化、Data/O4 MCP、历史 loader 可导入 | 不启动 worker、不建真实库、不访问模型/券商；模块入口中的 main 不在 import 时执行 |
| 测试收集 | 保留 suites 不依赖已退休源码/旧混合测试 helpers | offline autouse fixture 在收集之后才执行，必须额外约束 collection/subprocess 的外部访问 |
| 权限 | 所有 current/staged 节点 allowlist 和 exclusion 等价 | 包括 D2 open discovery、D3 v2.1 及 O4 operations/current Data MCP 分离 |
| 业务回归 | 相关 targeted suites 与保留离线 suites 通过 | 区分既有失败、缺环境和本次新失败；不可扩大 skip 来宣称通过 |
| 资源加载 | CDECR catalogs/prompts、Codex bundle 与 API/schema/Gold 全部可定位 | 检查 manifest、动态 import、importlib.resources、绝对 pin，不能只用 AST |
| 前端 | V2 schema/typecheck/test/lint/build 通过，生成差异可解释 | 不改 V2 API/设计；HTTP mock 的成功不代替所有 browser 场景 |
| 构建/打包 | wheel/sdist、V2 Docker COPY/command/resource、Compose 装配有效 | 没有旧代码藏在包中，ibapi/current SDK/current native deps 能加载 |
| 本地资产 | 只处理批准路径，Junction target、状态/身份/冻结输入 intact | 档案可读取、hash/索引完整，原资料确实已退出主树 |
| 工程防回流 | 不再跟踪 node_modules、pytest workspace、系统字面缓存 | 新模板明确 tracked，真实环境/secret 继续 ignored |

重点保留回归集合：

- `tests/v2_backend/`、current API wire/OpenAPI/auth/read/control/stream/cost/pnl。
- Codex D1/D2/D3、research lanes、O2/Event Library/O4、SDK/Worker/observations、Pilot。
- 新增 `test_codex_document2_v21_orchestration`、`test_d2_discovery_checkpoint`、`test_codex_document3_v21_orchestration`、`test_codex_document3_v21_maintenance`。
- ticker initialization、CDECR executor/prebuilt/dispatch、initialization repair、resource budget/recovery。
- Message Bus v2/admission/distribution/terms、crawler、Site Strategy、content enrichment、历史 loader。
- persistent runtime v2/W3/orchestration、trade delivery/recovery/shared equity、计费/工具权限/水平指标。
- `tests/cdecr/` 的离线 contracts/normalization/registry/identity/bulk/evaluation tests。

实现时可使用下列命令作为验收模板，**本轮未执行这些命令**：

```powershell
# 在隔离且按 current 锁文件安装的环境，先收集，再运行保留测试。
uv run pytest --collect-only --offline -q -m "not real_api and not real_db and not cdecr_real_models and not cdecr_real_db and not cdecr_real_step2"
uv run pytest --offline -q -m "not real_api and not real_db and not cdecr_real_models and not cdecr_real_db and not cdecr_real_step2"

# 在 frontend/v2 使用它自己的 pnpm 锁文件和脚本。
pnpm schema
pnpm typecheck
pnpm test
pnpm lint
pnpm build
```

先跑每批相关测试，再在删除旧能力后跑完整保留离线集合。若命令会初始化数据库/发请求，给它传入隔离测试路径并检查入口；不要直接运行带真实 `.env` 的 smoke/eval/ops 脚本。`scripts/audit_v2_backend.py` 只可对临时测试库/OpenAPI 运行，不能代表真实业务验收。

前端 browser 检查 current Overview/报告/策略/Event Library/Message Bus/运行记录/交易结果/配置的导航与操作，按 existing 测试能力使用本地 fake 数据；若代码退役改变页面关联行为，检查 1262px、1559px 两个桌面宽度。没有受影响的 UI 变化时无需开展视觉重设计。生产真实数据/真实模型/交易检查属于另一次授权的验收，不是删缓存的门槛。

### 10.2 防止退役能力重新进入 V2

加轻量工程检查即可，不设计新的线上阻塞系统：

1. 对保留生产入口、动态 entrypoints、console scripts 和 wheel 的内部模块检查已退休路径不存在/不被导入。静态图区分实际 import、TYPE_CHECKING、lazy export 与字符串装配；不把所有保留 monitoring/models 路径列禁用。
2. 对保留 bundle、catalog、schema、fixture/Gold 等真实输入做存在性/资源加载验证，路径检查必须跟随明确的资源引用，而非全仓库“出现 v1 就失败”。
3. 检查 tracked tree 中指定产物集合已退出；禁止新提交 node_modules/pytest workspace/系统缓存。Git ignore 和构建 ignore 解决不同问题，两者都核对。

当前仓库没有可依赖的 `.github/workflows` 自动验收记录。实施后先提供本地可复现检查；是否增加 CI 可用最小脚本/现有流程承载，不为退役专门建设复杂平台。

### 10.3 回退规则

每个源码批次形成可独立回退的提交，内容包括删除、消费者修改、测试迁移、文档/配置收口和 changelog。出现新导入失败、allowlist 变化、资源缺失、current 状态不兼容或业务退化，回退该批源码/配置；其他已通过的纯缓存批不必撤回。

普通缓存可重新构建。唯一历史证据、未提交工作和 ignored 状态必须由此前保全的档案恢复，不能指望 Git。正式数据不在退役批中，禁止通过 reset 消费 cursor、重建库、删除 journal/WAL、修改历史 pin 来“修复”代码退役问题。

若后续授权部署，保留各受影响服务的上一个镜像和正确 Compose/env/挂载参数；服务替换按实际 running baseline 进行，不能把本地共享镜像假设成所有线上服务的基线。存量运行 Effect/任务、真实持仓与 broker writer 在部署计划单独处理。本轮没有进行线上验证，也没有停止旧服务。

### 10.4 完成判定

完成应同时满足：旧自建 V1 已无法从当前代码/脚本/默认装配启动；V2 保留能力与历史读取正常；current/staged v2.1 未被覆盖；共享旧目录只剩有明确消费者的通用代码；旧专用 tests/docs/outputs 已退出或归档；本地可重建垃圾按清单清理；未处理项具有精确原因和去向。

不以“全仓库不出现 v1/legacy”“删掉所有 .tmp”“磁盘少了某个预估数字”作为完成标准。3,232 个访问受限路径、外部 Junction 和 current 状态库都必须在实际结果中据实报告，不能包装成已处理。

## 11 交付边界

本方案已经给出最终退役目标、路径类别、共享依赖迁移、混合测试/夹具、资源保护、本地归档和逐批验收。后续执行只需在获准后刷新变动路径/占用状态，把明确候选展开为精确操作清单并实施，无需再做一轮没有结论的全仓库“默认保留”审计。

本轮结果是方案，**不表示清理已经完成、测试已经通过或生产已经切换**。用户批准方案执行时，需说明是否只授权本地清理；远端推送、部署、停旧服务和数据维护各有独立作用范围，不能由审阅附件推导授权。

## 附录 A 本地根级临时目录清单

以下表将本轮实际存在的根级临时目录逐项列出。大小是可读取部分的逻辑长度；表中的处置是实施时的候选决策，受第 8 节具体保护项优先约束。访问拒绝和连接不计作已清理。




| 根级路径 | 可读文件数 | MiB | 处置 |
| --- | ---: | ---: | --- |
| `%SystemDrive%` | 3 | 0.94 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.mypy_cache` | 4 | 76.65 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.pytest-codex-20260809-b` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-20260809-c` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-20260809-d` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-20260809-e` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-20260809-g` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-20260809-h` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-20260809-i` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-live-20260809-a` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-live-20260809-b` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-live-20260809-c` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest-codex-live-20260810-a` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.pytest_cache` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.ruff_cache` | 179 | 0.27 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.test-tmp` | 未读 | 0.00 | 测试产物退出版本；访问受限项物理操作需核实 |
| `.tmp` | 8156 | 3308.05 | 按附录 B/C 细分，禁止整根清空 |
| `.tmp-cdecr-relevance-v2-full` | 27 | 17.83 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-cdecr-relevance-v21-full` | 27 | 17.83 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-cdecr-v5` | 82 | 96.81 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-codex` | 66 | 10.53 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-deploy-latest.tgz` | 1 | 0.60 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-evidence-ref-audit` | 未读 | 0.00 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-media-enrichment` | 1 | 0.00 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-parent-v2` | 5 | 3.26 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-pytest-debug-viewer` | 12 | 0.21 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-relevance-full` | 27 | 17.83 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-runtime-20260707` | 11 | 1.06 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-semantic-full-v1` | 147 | 47.95 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-semantic-v2` | 8 | 4.50 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-semantic-v3` | 23 | 13.45 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-semantic-v4` | 2 | 1.20 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-semantic-v5` | 2 | 1.21 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-pytest-semantic-v6` | 1 | 0.61 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-remote-compare` | 4 | 0.09 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-remote-wip-snapshot-20260708-010624` | 7 | 1.86 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp-rpds` | 29 | 0.57 | 依赖修复资料，确认安装引用后归档 |
| `.tmp-smoke` | 8 | 0.01 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-step9` | 31 | 0.78 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-stocktwits-test` | 7 | 0.47 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-test` | 7 | 0.00 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-uv` | 6097 | 1323.54 | 缓存/staging/历史测试按子目录清理，不跟随连接 |
| `.tmp-uv-cache` | 6 | 0.00 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.tmp-verify-o3` | 13 | 0.54 | 历史实验/审计/源码快照，保全必要资料后归档或清理 |
| `.tmp_pytest` | 1 | 0.00 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.uv-cache` | 12624 | 687.31 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |
| `.uv-python` | 4425 | 73.07 | 保留当前环境及解释器 |
| `.venv` | 17597 | 835.18 | 保留当前环境及解释器 |
| `dist` | 3 | 88.78 | 可重建缓存/历史测试/旧构建产物，按第 8 节清理 |

## 附录 B .tmp 子目录逐项分类

覆盖可读盘点中所有一级子目录，数字为下限。保护项不是垃圾；归档项保存必要证据后移出；当前保留项在相关工作完成后归档。实际操作需逐路径展开，不直接根据此表执行递归命令。

| 子目录 | 可读文件数 | MiB | 决定 |
| --- | ---: | ---: | --- |
| `.control/` | 33 | 26.22 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `c1-data-mcp-final-live-20260812/` | 1 | 0.00 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-data-mcp-fix-live-20260812/` | 18 | 0.16 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-data-mcp-fix-live2-20260812/` | 7 | 0.02 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-data-mcp-fix-live3-20260812/` | 43 | 0.13 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-final-live-20260812/` | 51 | 0.19 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-pilot-data-mcp-acceptance-20260812/` | 19 | 0.16 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-pilot-data-mcp-acceptance-v2-20260812/` | 123 | 0.40 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `c1-pilot-repair-live-20260812/` | 219 | 0.54 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `cdecr/` | 1 | 0.00 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `cdecr-audit-pytest/` | 9 | 4.85 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-audit-uv/` | 22 | 0.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-cross-focused/` | 8 | 4.82 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-degradation/` | 3 | 1.80 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-export-stage-v1/` | 5 | 0.13 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `cdecr-fix-two/` | 2 | 1.17 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-focused-2/` | 38 | 22.58 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-full/` | 159 | 55.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-full-final/` | 159 | 55.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-mu-v2-local-20260909/` | 10 | 1.11 | 保护 exact registry/prebuilt 引用；废弃副本归档 |
| `cdecr-mu-v2-local-final-20260910/` | 31 | 131.69 | 保护 exact registry/prebuilt 引用；废弃副本归档 |
| `cdecr-mu-v2-local-retry-20260909/` | 31 | 47.33 | 保护 exact registry/prebuilt 引用；废弃副本归档 |
| `cdecr-p0-1/` | 127 | 39.73 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-prebuilt-export-20260910/` | 5 | 131.18 | 保护 exact registry/prebuilt 引用；废弃副本归档 |
| `cdecr-resource-monitor/` | 14 | 3.86 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `cdecr-resource-monitor-smoke/` | 13 | 0.02 | 清理历史测试/缓存，必要摘要单独保留 |
| `cdecr-rklb-prebuilt-20260916/` | 5 | 85.57 | 保护 exact registry/prebuilt 引用；废弃副本归档 |
| `cdecr-rklb-v2-local-20260914/` | 11 | 159.62 | 保护 exact registry/prebuilt 引用；废弃副本归档 |
| `cdecr-step2-acceptance-20260824/` | 66 | 39.57 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `codex-gpt6-upgrade/` | 800 | 220.15 | 当前保留：部署/修复/回退资料完成后归档 |
| `codex-live-http-workspaces/` | 12 | 0.34 | 保护运行/pin 引用；结束且无引用的历史运行归档 |
| `codex-live-workspaces/` | 14 | 0.42 | 保护运行/pin 引用；结束且无引用的历史运行归档 |
| `codex-workspaces-o3-acceptance/` | 375 | 4.68 | 保护冻结输入；建立复现映射后其余产物归档 |
| `dashboard-document-smoke/` | 4 | 0.04 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `dashboard_backtests/` | 2 | 0.04 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `data-mcp-final/` | 6 | 0.25 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `data-mcp-pack-final/` | 24 | 0.33 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `data-mcp-smoke/` | 122 | 1.67 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `db-optimization/` | 5 | 0.00 | 当前保留：部署/修复/回退资料完成后归档 |
| `deploy-finnhub-p0/` | 1 | 0.28 | 当前保留：部署/修复/回退资料完成后归档 |
| `doc-smoke/` | 19 | 0.01 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `doxagent-yfinance-cache/` | 6 | 0.08 | 确认 provider 引用后定向清缓存，不属 V1 |
| `eval-tmp/` | 6 | 0.08 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `exports/` | 17 | 526.73 | 当前保留：部署/修复/回退资料完成后归档 |
| `formal-runs/` | 12 | 0.35 | 保护运行/pin 引用；结束且无引用的历史运行归档 |
| `freshness-release/` | 727 | 195.22 | 当前保留：部署/修复/回退资料完成后归档 |
| `hk-deploy/` | 13 | 0.17 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `ib-gateway-viewfix-20261003/` | 42 | 2.92 | 当前保留：部署/修复/回退资料完成后归档 |
| `ibkr-mcp/` | 24 | 0.29 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `langsmith_audit/` | 8 | 81.08 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `mypy_freshness_final/` | 4 | 59.81 | 清理历史测试/缓存，必要摘要单独保留 |
| `o2-authoritative-deploy-20260911/` | 8 | 0.36 | 当前保留：部署/修复/回退资料完成后归档 |
| `o2-mu-r2-current-prompts-20260826-preflight/` | 3 | 105.98 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `o2-mu-r2-current-prompts-20260826-preflight-03/` | 8 | 107.44 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `o2-mu-r2-current-prompts-20260826-real-01/` | 385 | 109.77 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `o2-mu-r2-deterministic-alignment-20260827-preflight-01/` | 8 | 107.44 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `o2-mu-r2-deterministic-alignment-20260827-real-01/` | 605 | 110.22 | 保护冻结输入；建立复现映射后其余产物归档 |
| `o3-acceptance/` | 25 | 1.15 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `o4a-data-mcp-acceptance/` | 375 | 6.94 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `ops-cleanup-20260928/` | 4 | 0.06 | 当前保留：部署/修复/回退资料完成后归档 |
| `persistent-runtime-w3-pilot/` | 1 | 0.03 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `phase26_runtime_mu_e2e/` | 10 | 0.46 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `projection-acceptance-20260820/` | 184 | 0.86 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `projection-recheck-20260820/` | 29 | 0.38 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `projection-stdio-20260820/` | 6 | 0.02 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `pytest-audit/` | 1 | 0.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-audit-full/` | 1 | 0.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-audit-scoped/` | 1 | 0.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-batch1-full-20260726/` | 156 | 52.64 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-batch1-gate-20260726/` | 43 | 25.60 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-batch2/` | 24 | 13.74 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-batch2-full/` | 159 | 54.43 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-batch2b/` | 2 | 0.60 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-cdecr-final/` | 157 | 53.80 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-cdecr-v2-final/` | 104 | 28.59 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-cdecr-v2-repo/` | 111 | 29.02 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-cdecr-v2-repo-verified/` | 111 | 29.02 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-n13-rollback/` | 2 | 1.16 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-of-WEIXUANXIE/` | 36 | 1.63 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-plan/` | 51 | 31.70 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-relevance/` | 55 | 36.11 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-revenue-audit/` | 1 | 0.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest-snapshot/` | 1 | 0.00 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest_n12_normalize_20260726_01/` | 2 | 1.21 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest_n13_boundary_20260726_01/` | 1 | 0.61 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest_n13_boundary_20260726_02/` | 26 | 15.29 | 清理历史测试/缓存，必要摘要单独保留 |
| `pytest_n13_boundary_20260726_03/` | 29 | 17.08 | 清理历史测试/缓存，必要摘要单独保留 |
| `remote-p0-compare/` | 2 | 0.09 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `retired-v2-entrypoints-20260909/` | 44 | 0.37 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `reuters-chrome-identity/` | 535 | 72.86 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `reuters-chrome-sidecar/` | 604 | 130.63 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `runtime_smoke/` | 48 | 3.50 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `sg-deploy/` | 18 | 0.02 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `supabase_payload_logs/` | 8 | 0.09 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `supabase_write_failures/` | 6 | 0.02 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `ticker-init-sql-check/` | 311 | 24.26 | 迁出固定 PGlite 依赖及安装说明，再清理 staging |
| `ticker_initialization/` | 3 | 0.11 | 保护状态/身份/凭据/待交付负载，按资产分拆 |
| `trade-execution-gateway/` | 1 | 0.12 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `trajectory_eval/` | 3 | 6.98 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `v2-integration/` | 126 | 68.91 | 保护运行/pin 引用；结束且无引用的历史运行归档 |
| `v2-production-acceptance/` | 15 | 0.27 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `v2_backend_dist/` | 2 | 23.63 | 归档必要验收/审计证据后退出，不恢复旧入口 |
| `w1w2_mu_dataset_drafts/` | 3 | 0.13 | 归档必要验收/审计证据后退出，不恢复旧入口 |

## 附录 C .tmp 根级文件分类

集合仅用于说明，实际清理按枚举的精确文件执行；不是通配删除授权。

| 根级文件集合 | 文件数 | MiB |
| --- | ---: | ---: |
| 近期排查脚本/结果，诊断完成后归档 | 75 | 6.27 |
| 可重建 Python 字节码 | 1 | 0.02 |
| 旧 Blackboard eval/resume 日志 | 68 | 0.06 |
| 旧 Blackboard/run 状态快照 | 10 | 105.89 |
| 一次性 probe/inspect 工具，保存复现资料后归档 | 14 | 0.05 |
| 其他验收/测试输出，保留必要结论，清理重复中间件 | 36 | 8.70 |
| 源码 patch/bundle，保存唯一改动后归档 | 7 | 0.01 |
| current 默认/配置引用状态库及 sidecars，保护 | 8 | 3.12 |
| 旧 V1/测试库，必要证据归档后单独退出 | 4 | 0.62 |
| 其他数据库/锁，确认 owner 与引用，不按后缀删除 | 3 | 0.18 |

## 附录 D 审查证据索引

| 检查 | 本轮结果 |
| --- | --- |
| 附件 | 初始已完整读取 Word 正文/表格和 ZIP 两份 JSON，按外部材料审查；附件固定提交为 7796efa45724da19aabe9939c795e243c20c2a99 |
| 跟踪集合 | `git ls-files -z` 10,363 条，与附件逐路径核对 |
| 本地盘点 | 不跟随连接，不扫 `.git`；99,656 可读文件，329 个 `.tmp` 一级分组 |
| Python 重扫 | 1,043 文件、688 源码模块，AST 无语法错误；人工追踪父包与动态入口 |
| bundle | 25 条 resource_sources 目标均存在 |
| 配置/解释器 | 只读路径/开关，确认 codex-runtime、初始化库和 `.uv-python` 引用 |
| Junction | Get-Item LinkType/Target 证明导出依赖指向项目外公共目录 |
| 受限导入 | 无字节码写入/外部连接/SQLite 连接/子进程；current venv 有 rpds、_rust 基线失败 |

原始盘点与导入索引在系统临时工作区生成，方案不包含真实凭据或原始业务 payload。证据来自受审工作树；后续按路径与符号刷新局部变动，不机械依赖旧行号。附件预览目录在结束核对时已不再存在，因此本轮不提供附件文件 SHA256；初始解析和已完成审查不受影响。
