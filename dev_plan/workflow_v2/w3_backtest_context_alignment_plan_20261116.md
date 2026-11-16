# W3 回测输入与编排对齐方案

日期：2026-11-16。执行更新：用户批准实施，并明确仅支持v6。下文保留勘察基线；原v5运行兼容项不实施，历史报告仍可读。开发与数据补充回执见 `eval/w3_backtest/reports/context_alignment_20261116/completion.md`。未部署、未发起评分回测。

## 1. 推荐结论

保留现有本地、单轮决策回测框架，做 **6 项调整**。核心是让生产与回测共享“业务输入构造、v6 可见字段投影、研究入口及输出校验”，回测只负责提供冻结数据和替换外部证据交付方式。

不把生产 Scheduler、交易执行器或数据库复制到回测，也不另建 agent harness。Case 可以只提供消息和部分背景；拿得到的输入按生产形状注入，拿不到的不伪造成“已经确认没有”。是否值得比较，由报告展示数据覆盖情况，不设置全量数据齐备才能运行的门槛。

优先解决业务上下文差异。模拟 Web 再丰富，也替代不了生产已经直接注入的 Event、Policy 和历史决定；只换提示词或把输入 JSON 改成 v6 名称，都无法解决这件事。

## 2. 勘察范围与版本事实

勘察基于当前本地工作区，HEAD 为 `dcded5e6f`（W3 Case 隔离与 v6 重构提交），并读取工作区未提交文件。本文不推断远端已部署状态。

必须区分以下三件事：

- **代码已有 v6 能力**：`w3_v6.py`、`w3_execution.py`、`w3.py`、研究 Store 和 v6 测试已有实现，不是只有设计稿。
- **本地提示词资产仍有过渡状态**：本次读取的 `prompts/persistent_runtime_v2/w3/contract.json` 仍声明 v5，`operations.md` 在工作区被删除，AGENTS/Skill 有未提交修改。因此不能直接把本地目录称为已就绪的 v6 发布包，也不能据此推断用户最终想保留的资产布局。
- **回测局部跟进了 v6，但主要执行器没跟上**：`cli.execute_run` 已会按 v6 marker 收集 `operations.md`，但 `DecisionReplayRunner` 仍拒绝非 v5 contract，`runtime_case` 仍写死 v5，研究预绑定仍无 v6 scope。不能把“CLI 认识 v6”当作“回测支持 v6”。

11 月 15 日两份方案/提示词对齐说明仍有“待实施”文字，本次以实际代码为准。资产准备问题在实施时确认契约即可，不应因此推迟本文的回测架构调整。

### 关键代码入口

| 层 | 文件与函数 | 本次确认的职责 |
|---|---|---|
| 生产输入 | `persistent_runtime_v2/w3_execution.py::trade/_events` | 选 W1 关联 Event，冻结全局状态和历史成员，构造 Trade 输入 |
| 共同业务输入 | `persistent_runtime_v2/w3_context.py::build_case_payload/case_labels` | W1/W2、Policy、标签、PIN 等业务输入与对齐信息 |
| v6 可见投影 | `persistent_runtime_v2/w3_v6.py::trade_input/policy_projection` | 删除不外显字段、裁剪 Policy、标题消歧、近期成交列表 |
| 最终任务与 SDK | `persistent_runtime_v2/w3.py::_run_phase_materialized` | 模板、任务 JSON、Schema、上下文绑定、阶段投影和实际 prompt |
| 研究读取 | `persistent_runtime_v2/w3_research_store.py` | 分页、原文/切片、scope 和离线证据读取 |
| 正式回测 | `w3_backtest/decision_runner.py` | v2 数据集的单轮 Template/Fork 执行器 |
| 回测准备 | `w3_backtest/curated.py` | 原 Case/D1/D2 导出、简化上游输入、来源编译 |
| 回测隔离与运行 | `w3_backtest/runner.py::local_worker`、`cli.py::execute_run` | 私有 Worker、关闭外部能力、冻结运行、并发与结果汇总 |

以上路径均相对于 `src/doxagent/`。相关现有测试包括 `test_w3_v6.py`、`test_w3_backtest_curated.py`、`test_w3_backtest_runner.py`、`test_w3_backtest_prompt_snapshot.py`、`test_w3_research_v2.py`。

## 3. 生产与回测实际差异

下表“生产”指本地代码的 **v6 分支**；历史 v5 不按新版含义重解释。

| 方面 | 生产 v6 | 当前正式回测 | 建议 |
|---|---|---|---|
| 线程 | 公共模板，每个新 Case 独立线程；同 Case 才恢复/继续 | 已是公共母线程 + 独立 Fork | 已基本一致，保留，不恢复旧跨 Case 混用历史 |
| 契约/阶段 | v6；Trade 四字段，Ops 仅 PIN/WATCH | 单轮执行器写死 v5 | 支持明确的 v5/v6，仍只跑 Trade |
| 消息 | `message_content` 生成 title/body/source_name/published_at/url，含日期精度处理 | 原样保留另一套 publisher/publication_time_basis 等字段 | 共用消息投影，保留原始发布时间精度 |
| W1 | result/confidence/reason，并内联最终 reference_ids 对应的 Event 事实 | `neutral_upstream` 只留 result/confidence/reason；未带关联 Event | 可选恢复匹配事实，不提供整库 Reference |
| W2 | flags/confidence/policy_matches；v6不外显长 reason | 通常只剩 confidence/reason；原 HIT/RELATED 身份丢失 | 构造侧保留完整 W2，模型侧复用 v6 投影 |
| Policy | 关联规则直接内联，保留标题/传导/条件/参考基线，删除预设方向等 | 不提供 | 按需提供冻结关联 Policy，这是最高价值补齐项之一 |
| alignment/标签 | 原始对齐状态、存在时的布尔值与 additional_tags | 简化输入未带 | 有可信来源则复用，不用默认 false 补齐 |
| PIN/近期交易 | Trade 看 PIN；近期交易为截点前48h实际 ENTRY 成交 Case | 全部没有 | 作为可选静态输入；不接交易执行系统 |
| WATCH | Trade 外显已删除；仍可影响入口与跟进标签 | 也不跑 WATCH | 不需要模拟 WATCH 维护；按需保留已冻结跟进标签 |
| 研究工具 | v6 `d1-d2.v1`，不能旁路读 Policy/Reference；已有行情入口 | 已用相同 Store/MCP，但预绑定无该 scope，走 v5 | 共用 scope；模拟网页作为明确的离线附加资料保留 |
| D1/D2 元数据 | 有绑定版本与研究 cutoff，未知为 null | 只取文件文字，未投影 contexts | 有哪些就提供哪些，cutoff 不用 Case 时间代填 |
| 时钟 | Trade cutoff 默认取消息总线事件时间，消息 published_at 独立 | as_of 默认取消息发布时间；同时额外注入多套时间字段 | 保留既有情景时点，可显式选择原生产截点 |
| 行情 | 真实 provider 后台准备、可能暂不可用 | 已冻结同类三段行情，但无条件要求三段均有数据 | 沿用生产格式；允许明确缺失，不伪造 READY |
| 回测规则 | 生产没有 backtest.md | 文件明言不提供 Policy/PIN/历史业务，价格与搜索完整 | 改为识别可选上下文，避免和新增输入冲突 |
| 模型/速度档 | W2 HIT/RELATED 默认 Sol/medium，其他 Luna/max；Luna Trade Fast | 默认固定 Luna；最新 SDK 又可能隐式继承 Fast，之前实验靠源码快照关掉 | 生产路由与固定模型实验都支持，实际档位显式冻结 |

主要差异在**当前一轮被模型看见的业务输入**，不在是否使用 Fork。新版生产本身也隔离 Case，因此没必要为了“真实”把旧长线程、运营和整套历史重放系统搬回来。

## 4. 改动一：贯通契约和指令快照，保留单轮执行

涉及：`decision_runner.py::runtime_case/__init__/initialize`、`cli.py::execute_run`、`curated.py::snapshot_production_prompts`。

1. 从本次冻结指令包读取 contract，而不是在 `runtime_case` 写死 v5。按契约选 `w3-entry-v5/v6`，Trade 输出继续使用共享的 `W3DirectionDecision`。
2. v5 保留用于历史回测。新 v6 Case 不用“最新提示词 + v5 外壳”运行，不自动改历史数据集里的 marker。
3. 指令来源明确选择本地包或已导出的生产 bundle；默认使用数据集已冻结的包，`--prompt-root` 显式覆盖。三种来源均在开始运行前冻结并记摘要，不能执行中读取最新目录。
4. 本轮仍只测 Trade：必要文件是对应契约的 AGENTS、唯一 Skill、contract 与 backtest.md。**单轮回测不因缺少 operations.md 拒绝运行**；若提供完整 bundle，可以在宿主保存它的来源/摘要，但不让模板和 Trade 读取运营正文。生产完整 bundle 的发布要求不因此放松。
5. 公共模板只读公共规则及回测环境说明，每个 Case 仍独立 Fork；不新增预热池。恢复有结果则直接消费，不为了重建摘要重跑模型。

改后：可以测试明确的新版 Trade，也能继续复现旧 v5；不把 Ops/W1R3/发单作为前置依赖。

## 5. 改动二：共享生产输入构造和可见投影

涉及：`w3_context.py`、`w3_v6.py`、`decision_runner.py::agent_payload/runtime_case`、`curated.py::neutral_upstream`。

### 完整上下文时走同一条路径

回测先从静态资料恢复中间业务输入，再复用 `build_case_payload` 与 `trade_input`。消息统一调用 `message_content`；W1 Event 选择、标签和 Policy 标题消歧抽成可接收冻结数据的纯函数，生产原函数继续负责查库与严格校验，回测调用纯函数。

不要实例化完整生产 Service 去跑这段逻辑，也不要在回测再写第二套字段白名单。现有 `build_case_payload` 对完整 W1/W2/state 的依赖需要做小幅拆分：查询/校验留生产适配层，数据到输入的确定性转换供两边复用。

### 缺失上下文时仍可运行

生产 `policy_projection` 当前会对 HIT 身份/正文缺失报错，`_events` 也要求引用事实可恢复。**这些生产校验保持不变**。将其数据呈现部分提为共享纯函数；回测在适配层处理“已知提供了哪些”，不能把生产全局改成宽松模式。

具体口径：

- W1 有判断但拿不到 Event：保留判断，不猜事实；未知的 `events` 不伪造成已确认空集合。引用 ID 及缺失详情留宿主。
- W2 有 HIT/RELATED：保留已知 flags/confidence。规则全文可得时，生成与生产一致的 matches 和裁剪 Policy。
- 已知 HIT 但缺少规则正文/可用标题：不强改 NO HIT、不虚构标题或条件。只传可确认的上游字段，缺失的 matches/details 留空缺；在宿主记录该项未恢复。
- 旧 Case 只剩 W2 reason：优先从准备档案恢复原 `w2_final`。确实拿不到时，不解析自由文本去猜 HIT/RELATED，也不把旧长 reason 当作新版生产 `w2` 的替代品。保留可确认的 confidence，其余缺失。
- 部分 Policy 恢复成功就呈现该部分，不为凑完整列表拉入其他 Policy。v6 不补回 condition decision、match_scope、trigger_boundary、内部来源定位等已删除字段。
- 有 alignment 才带原状态与布尔值；缺失不自动等于 false。标签只传可确认信息，继续不充当控制开关。

这种局部缺失是回测输入的自然状态，不新增一套“认证级别”、审批链或 Case 分流状态机。完整数据测试必须与生产投影逐字段一致；部分数据测试只省略不可知的项，不能改变已知事实。

## 6. 改动三：可选上下文文件，优先复用已有冻结资料

涉及：`curated.py`、`remote_export.py`、`decision_runner.py`。建议每条 Case 增加一个**可选** `context.json`，不要再建数据库。

```text
inputs/<case>/
  case.json                 # 原消息、现有情景时点、基础上游结果
  context.json              # 可选：冻结的业务背景
  evidence/                 # 现有模拟网页与行情
preparation/<case>/          # 来源、完整旧输入、缺失记录；宿主侧
```

`context.json` 按实际需要可包含：`w1_assessment / w1_events / w2_assessment / policies / alignment / additional_tags / pins / recent_trade / research_cutoffs`。这些是构造侧中间数据，不能整份直接塞给 Agent；最终字段仍由生产投影决定。

缺文件表示没有提供额外背景；字段缺失表示没有恢复该项；显式空数组表示构造者确认该集合为空。只要某项存在就按对应共享模型/转换函数处理，不要求先集齐其余项。无需为每种组合创建运行模式。

### 数据取得的优先顺序

1. 已保存的原 Worker 任务/Case 冻结快照、原执行 bundle 和历史资料；先读本地 preparation，避免重复远端采集。
2. 准备阶段按指定 Case ID 只读导出该时点已冻结的 W1/W2、关联 Policy、PIN/近期成交快照和版本信息。
3. 原冻结快照不存在，且容易按绑定版本恢复时再补；拿不到就保留部分输入。不得用今天的活动 Policy 或当前 PIN 倒填历史情景。

保留当前49条与 Gold，不一口气重做全部案例。迁移先补基础字段，再按需要挑 Case 增加上下文；历史结果保留原冻结输入。

### 近期成交与 PIN 的边界

- recent_trade 复用 v6 的展示形状：历史消息 + 四字段决定；成员依据为截点前48h同 ticker实际 ENTRY 成交，部分成交算，接受/意图未成交不算。显示内容和成员证据分开保存。
- 优先复用生产已冻结 `w3_recent_filled_snapshot`；必要时只在准备期复用现有成交筛选。回放不去连接 te 数据库或 IBKR，也不复刻成交状态机。
- 人工情景可明确提供假设的历史成员，但在宿主来源记录中注明，不伪称已核对实盘成交。
- RECENT_TRADE 只有身份匹配信息足够时才复用生产计算；RELATED 和 Prior 引用不算实际命中。只有显示列表、没有身份依据时，不据此猜标签。
- PIN 可按原结构提供快照，无需模拟 SET/CANCEL 和持续运营。WATCH 全量状态不注入新版 Trade；已有可信 WATCH_FOLLOWUP 标签可保留。

## 7. 改动四：Research MCP 对齐 v6 可见范围

涉及：`decision_runner.py::run_case`、`w3.py` 的 manifest 构造、`w3_research_store.py`、`runner.py::_ensure_research`。

当前回测提前写入 `w3_context_v2` binding，后续 `run_phase` 直接复用它，不会再替它补上新版 scope。因此不能只把 RuntimeCase 的 contract 改为 v6。

推荐抽取一个生产/回测共用的 manifest 构造函数，供现有 `run_phase` 与回测预绑定调用。保持一个 binding 创建入口，避免回测先绑定一个宽权限快照、生产投影再生成另一个描述。

v6 manifest/Worker 请求均使用对应 entry 与 `d1-d2.v1`：

- 研究组件只安装实际提供的 D1/D2；已存在的编译、切片、catalog/search/read、cursor 与完整页回执继续复用。
- contexts 的 cutoff 来自研究版本元数据；确实未知为 null，不用文件采集时间、Case 时间或“现在”替代研究时点。
- Policy 通过裁剪后的输入传给模型，不能从 `raw_document(policy)`、Reference、history/state、旧索引等路径取回被隐藏的方向与长 reason。
- 离线 `evidence` 保留为模拟 Web 的额外数据源。Store 已有 `OFFLINE_REPLAY + d1-d2.v1` 例外，继续使用，不为模拟网页开放生产完整 Policy/Reference。
- 行情仍走同一个 `market_view` 工具，只将 backend 换成已冻结文件。

模板/Fork、MCP 服务和本地 Worker 已存在，无需另造工具体系或把模拟网页做成一个新的搜索服务。D1/D2 不必重新研究，网页也不因这次编排升级全量重建。

## 8. 改动五：统一情景时钟，允许可选资料，修正回测说明

涉及：`agent_payload/runtime_case`、`market.py`、`curated.py::build_curated`、`prompts/w3_backtest/backtest.md`、`w3.py` 的离线任务说明。

### 业务时钟

内部保留两个概念：消息自身 `published_at` 与情景判断时点 `as_of`。最终给 Agent 的当前时点以生产字段 `cutoff_at` 表达，等于本次选择的 as_of，不再平铺 as_of/time_utc/time_new_york 多套重复字段。

- 既有 Case 的 as_of 原样保留，不自动改时间或 Gold，也不重新核实时效性标签。
- 新 Case 默认可以继续用消息发布时间；想复现生产延迟接收时，显式设置为原 `message_bus_event_time` 或指定的情景判断时点，并记录依据。
- runtime_case.source 的真实发布时间与 publication_time_basis 分别恢复；不再全部写成 as_of/EXACT。日期型消息保持日期展示，不能因运行需要有一个精确时点，就把消息发布时间伪造为精确时分。
- 更换时点是新的输入情景，使用新指纹/新运行；证据与价格按该时点截止。Gold 不自动重标，报告说明使用了哪个情景。
- 原生 Elapsed 仍用真实执行时间，不能随业务时钟改写。继续复用现有起点和提醒，只记录可选起点写入失败，不新建计时系统。

### 缺资料与坏资料分开处理

当前 runner 在调用模型前硬性要求完整三段行情、D1/D2文件，build 也用这些条件判未就绪。改为：消息、明确情景时点、可执行指令包是最小运行输入；研究/网页/行情按实际提供情况安装。

- 缺少研究或行情不直接报技术错误，不创建虚构文件、不写 READY，不由宿主改成 NO_TRADE。
- 行情未提供时，复用现有生产不可用响应及 Skill 的处理规则，不无限等待，也不另外增加回测交易否决门槛。需要时模型可据现有资料判断；其结果仍保留和评分。
- 已提供数据若 ticker 错误、文件损坏或包含明确越过本次 cutoff 的完整价格条，属于输入错误，应修复或只跳过该 Case。不能把错误数据当成“缺一些背景”继续伪装。
- 正常休市、没有形成中的 bar、未提供某段，与文件构造错误保持现有区别。

backtest.md 调整为：提供了哪些上下文就按业务身份使用，未提供不等于不存在；移除“一律没有 Policy/PIN/历史机制”的断言；只对实际交付的静态网页、行情说明可直接使用，不对缺失内容宣称真实完整。保留不质疑离线交付、不把离线本身当 NO_TRADE 理由的现有原则。

v6 任务压缩不能把唯一的情景时钟提示一起删掉。把离线能力/时钟简短说明放在独立 backtest.md 中，与实际字段一致（`input.cutoff_at`），避免 v6 投影后仍要求读不存在的 `input.as_of`。生产通用 instruction 可继续复用，回测不重复附加一大段免责话术。

## 9. 改动六：冻结实际运行条件，报告输入覆盖和可比性

涉及：`cli.py`、`runner.py::local_worker`、`report.py`、`decision_runner.py`。

1. v6 默认可复用生产模型选择；显式 `--model/--effort` 仍用于固定模型实验。有可信 W2 时按 HIT/RELATED 路由；W2缺失时用本次明确记录的默认模型，不假装恢复了生产路由。
2. 将 service tier 做成运行参数/运行级 SDK 配置覆盖，支持显式 `default/fast`，未覆盖时采用共享生产规则。冻结实际 model/effort/tier/provider；不再靠复制源码改一行来开关 Fast。不改变生产全局设置。
3. CLI 的 `--effort` 应按当前共享执行配置接受合法值；至少不能让生产 medium 路由与回测枚举不兼容。初始分支和显式覆盖都要写实际选择，而非只写请求值。
4. 沿用现有 input fingerprint，补入可选 context 文件、研究元数据、最终模型可见 task 与 manifest 的摘要。宿主保留最终 input/task，方便直接与生产做 diff；Gold、旧当次决策及数据缺失清单不进入模型输入。
5. 每 Case 宿主记录 supplied/missing 项，例如 W1 facts、Policy、PIN、近期成交、D1/D2、网页、行情；这是描述，不是运行资格或强制排除评分的等级。
6. 报告保留全体 Gold 对照，同时可按输入覆盖/时点分组查看。新旧方案比较只配对同一 Case、同一已冻结资料/情景；新增 Policy 后的改善不能全归因于提示词。无需额外模型裁判。
7. 重复实验可直接并行调度，使用一个全局并发预算，Case 仍独立；不再批间隐式串行。保留成功结果，只补技术失败/未完成项。调度条件、模板初始化与 Case/SDK 耗时分开记录。
8. 正式 runner 优先按本 Case 的实际 Job/attempt 定位 `turn_summary.json`，不要再依赖固定一层 `attempts/*/audit` 路径，避免之前大量汇总为空、事后人工找回的情况。找不到时记录缺失，不能算0或重跑业务结果。

已有隔离边界继续保留：不接生产发单、运行期不开实时 Web/Data MCP/IBKR，不运行 Ops/W1R3。容器启动显式覆盖继承来的 Compose 项目标签，沿用上次实验已发现的修正，避免被其他验收项目清理。

## 10. 改后运行形状

```text
准备期：原消息 + 可选冻结 context + 历史研究/网页/行情
  → 保存原档与宿主来源记录

运行期：读取本次指令/数据快照
  → 公共 AGENTS + Skill + backtest.md 母线程
  → 每 Case 独立 Fork
  → 从已提供资料构造业务输入
  → 共用生产 v6 Trade 投影
  → 绑定 D1/D2 scope + 离线网页/行情
  → 共用 run_phase / 四字段Schema / 原生计时
  → 只记录模型决定，不执行业务动作
  → Gold对照、输入覆盖及最终input差异报告
```

最小 Case 和较完整 Case 的执行链路相同，区别只是可选上下文是否提供。没有 context.json 的现有 Case 仍能跑；有可靠完整背景的 Case 可以更接近生产。无需维护两个长期分叉的回测执行器。

## 11. 实施顺序与验收

### 实施顺序

1. **先贯通 v6 与共享投影**：改动一、二、四。用一份完整静态夹具对比生产与回测，不先跑付费全量模型。
2. **再补可选上下文与时钟/缺失语义**：改动三、五。只读迁移现有 preparation 中容易恢复的字段；默认不批量远端补采、不重标 Gold。
3. **最后固化运行参数与报告**：改动六。选择少量完整/部分输入做调用验收，随后是否全量真实回测另行执行。

### 必要验收

| 场景 | 预期 |
|---|---|
| 完整 v6 Case | 与生产同一业务资料生成的 input 字段及内容一致，除 context_ref/本地路径/离线证据交付等运行差异 |
| 有 W1 OLD 与匹配 Event | Event正文可见；没有整库Reference，不因缺少整库而失败 |
| W2 HIT/RELATED 与关联 Policy | flags/matches/标题消歧/条件基线正确；长reason、预设方向与内部ID不旁路泄露 |
| W2/Policy只恢复了一部分 | 保留已知判断，未恢复项不猜、不改 false，Case可运行 |
| 无 context.json | 现有Case可执行；不冒称已确认无历史交易/PIN/Policy |
| 显式空近期成交与未知历史 | 宿主覆盖记录能区别；RECENT_TRADE不由RELATED、未成交意图或未知资料推断 |
| 研究工具旁路 | v6无法读Policy/Reference/history/state原始对象，但可读取本Case离线网页和行情 |
| 日期精度/延迟接收情景 | 消息时间保留原精度，cutoff采用选定情景；不改已有Gold；业务时钟不随模型耗时推进 |
| 缺研究/缺行情 | 不造READY，不由宿主直接NO_TRADE，不阻止Case仅因可选资料缺失运行 |
| v5/v6指令与单轮边界 | 正确冻结版本；缺Ops正文不阻断Trade-only；不发起Ops或发单 |
| 模型/速度档 | 生产路由与显式固定模型都正确，显式标准档不会被生产Luna Fast覆盖 |
| 并行/恢复 | 不串错Case上下文、不重复成功调用；结果和实际audit/运行参数能一一对应 |

测试复用现有文件扩展，不建立大规模新测试框架。最重要的回归不是“v6名字是否出现在prompt”，而是**相同资料经两条入口是否给模型相同的业务输入，以及缺失资料时有没有被悄悄改造成错误事实**。

## 12. 明确不做

- 不恢复跨 Case Thread 复用，不导入历史 compaction 文本，不模拟全部长期会话。
- 不新增交易、PIN/WATCH运营、R3事实抽取或完整 Scheduler 回放。
- 不要求每条 Case 都恢复成交库、全部Policy或全部历史资料，也不为缺项引入自动禁止评分。
- 不用今天的网页、Policy和价格填充过去的空白，不把无限追索历史原件当作开工条件。
- 不改业务交易门槛，不重写 AGENTS/Skill，不把某个 Gold 倒推为输入。
- 不在本轮启动49条真实回测或部署。实施后先确认输入对齐，再决定需要测哪些Case。

