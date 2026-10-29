# W3 回测校准与执行环境升级方案

版本：2026-10-30。范围：代码勘察、实现设计和构造规范；本轮不实施代码、数据集重建或模型测试。

## 1. 目标与已确认边界

把现有回测改成**给生产 W3 的决策过程提供可信、充分的历史情景**。保留生产的业务判断标准，消除采集不足、缺口话术、错误时间锚点和不同资料入口造成的额外影响。不把“交易更多”当作校准成功，也不为追求实盘完全等价而模拟整套交易系统。

按本轮需求及后续澄清，确定以下边界：

- 新目录含59个案例编号，正式集只使用44个保留案例；15个移除项只做编号对照。原25个随机 `NOISE-*` 全部退出新集，不重新随机抽样，不删除旧数据和旧结果。
- 用户已核实消息时效性。最终Gold及Tag直接采用，不再发起时效性裁决、修改Gold、选择性淘汰或设置待确认评分区。时间准备只服务于Case时钟和证据截止。
- 每个Case只评估一次 `LONG / SHORT / NO_TRADE` 决策，不运行运营，不模拟交易受理、账户、成交、去重、WATCH/PIN或Policy业务状态。
- 不注入历史交易意图、WATCH、PIN、完整Policy、Condition使用状态；不通过另一个入口把它们补回。
- 回放不启用实时Web、Data MCP、IBKR、生产服务或子Agent。准备阶段沿用远端IBKR采集及 `gpt-6-luna/max` 证据准备任务。
- 可以复用本地、仅读冻结文件的W3 Research MCP；它不同于Data MCP或联网研究。现有模块之外不建模拟搜索服务、向量库、浏览器代理、事件时钟引擎或新harness。

本方案配套：[W3测试集构造规范手册](W3测试集构造规范手册.md)。手册是后续构造任务的固定依据。

## 2. 勘察发现：已验证的问题与应对齐的生产行为

### 2.1 对临时排查总结的复核

| 现状与证据 | 具体影响 | 本次处理 |
|---|---|---|
| `collect.py::_prompt` 明确要求最多6次search/open，通常4次；只输出简短改写摘要 | 原发、可比Prior、经济传导、条款和反证往往无法同时覆盖 | 移除6次上限，改为围绕Case研究问题准备有实质正文的资料包 |
| 73条共395份Source，平均5.41、最少1；`index.json`共300条web_gaps | 采集限制直接成为模型对事实可靠性的负面暗示 | 审计信息留构造侧，Agent视图只给事实、来源、时间和读取入口 |
| `_markdown_source` 固定附加“改写、非完整历史快照”等声明；observations含counterpoint_or_limit | 证据与采集器评语混杂 | 保留报道本身的事实限定，移除采集器的交易判断与普遍怀疑措辞 |
| 旧run的924次工具返回中，按明确截断标记复核357次，覆盖全部73条 | 文件存在并不代表关键正文已读到；临时总结362次使用的口径略宽，结论一致 | 复用分页读取，不再靠大范围Get-Content交付研究 |
| SDK历史记录仍有通用技能目录及网络可用描述 | 设置开关不等于实际生效，完整上下文并不相同 | 校正现有技能发现配置并核对母Thread实际消息；不重写SDK系统提示 |
| `runner.py` 每个Case新建Worker/Journal，再调用 `execution.trade()` 和 `execution.operations()` | 重复初始化母Thread，并承担不需要的宿主/运营依赖 | Worker与母Thread提升到run级，只调用第一阶段模型 |
| 回测已有Template初始化和Fork，不是完全没有母Thread | 新建另一套线程机制没有必要 | 复用现有Thread生命周期和注册方法，改变生命周期范围 |
| 旧价格为10分钟线及由其聚合的自然日日线；offline_market_view明确缺小时线 | 与生产三段行情不同，且缺口会进入决策 | 重新采集真实5分钟、小时和RTH日线，固定为同一as_of |
| 旧cutoff混用原Worker启动、上游路由完成、NASA合成时间 | 同一情景混入不同的“当前时刻” | 统一按用户指定发布时间规则构造Case时钟 |
| 旧回测统一将D2 publication state写为COMPLETE，研究contexts为空；历史W1/W2也不等于当前路由结果 | 研究呈现状态与生产不同，不能据此推断全链路效果 | 版本状态只如实保存在构造档案，不向Agent添加伪完备/缺口结论；本轮不评测上游或路由 |
| 当前remote_export主流程仍有直接json.loads读取Case的路径 | 新远端记录已有NativeContent外置，无法据此完整恢复输入 | 按指定Case ID查询并使用已有load/decode恢复，不扫描全库抽噪音 |

这能确认环境差异与污染路径，不能据此量化“保守化”有多少来自框架、多少来自生产提示词本身。后续应清除框架诱因，保留真实业务分歧。

### 2.2 最新生产与本地草稿需要分开

2026-10-30只读核实，生产active bundle为：

`1e140cd16d588450c1fae255ba6780be978d1feb4a028e1d5c05a5c943930284`

其业务合同为 `persistent-runtime.w3.v5`，入口为 `w3-entry-v5`。第一阶段是四字段 `decision/reason/prior_expectation/expectation_delta`。

生产AGENTS与Skill的文本SHA256分别为：

- AGENTS：`c2f82cef2deb292e0840eefc80d6f66e0de5eb9cf4cecb27f8e88b29b17b9598`
- Skill：`4308c000d7208e5757198e03ea2cee1a1cf19de92f2eca65db50bb73696629bf`

当前本地两文件均有未提交改动，hash分别为 `a14b4d9bcf6701f8c53c217a2de5cd79c3615eff7b0df0b86e0058eb1f13db0d`、`d6e0ee1a05ee349ea5691974b8095f41e2384c4fa39e4b8d34ee5e7e347009fa`。差异涉及时效、传播阶段、剩余条件、行情元数据检查和PIN，并非纯格式变化。旧的10月19日入口说明仍写业务v4，不能代替当前代码与active bundle。

**校准生产时默认只读导出active bundle并冻结；提示词实验才显式指定本地prompt-root。**不覆盖本地草稿，不自动发布提示词。每个run记录实际来源、bundle ID、三份提示词及contract hash，不能用本地草稿的结果声称与当前生产对齐。

生产需要对齐的内容：同一业务提示词和方向schema；母Thread先加载公共规则；Case Fork后带当前消息与时间；按需读取研究；拟交易时查看统一行情。生产现有48小时Policy/Event关联复用、READY池、实时行情刷新、第二阶段及私有历史状态，均不在本轮模拟范围。

## 3. 数据集：59条目录、44条正式集

新建独立数据集 `w3_curated_20261030`，旧 `w3_history_20261014` 及其run只读保留。

- `catalog.jsonl`：59条，包含保留/移除、Gold、Tag与原Case ID。
- `cases.jsonl`、`gold.jsonl`：只含44条保留案例，4 LONG、3 SHORT、37 NO_TRADE；Gold与Tag只供宿主评分。
- 不复制25条旧随机NOISE；不能把新增的INTC–Hamas等用户选定反例也按“看起来像噪音”擅自移除。
- 原48条保留33条，新增11条全部保留。主表与后面的runtime映射表分别解析，不能把映射表再算成11个案例。
- 与旧Gold相比，保留项中 `BE-02-02`、`INTC-01-04`、`EXTRA-INTC-TERAFAB`、`EXTRA-RKLB-SYNSPECTIVE` 改为NO_TRADE。其他裁决直接按新文档导入，不从旧结果推断。

新增11个Case已全部在远端runtime找到，均带D1/D2版本绑定。值得单独处理的输入：

| Case | 已验证情况 | 构造要求 |
|---|---|---|
| ADD-INTC-NEWSTREET | 原标题通用、正文为空 | 保留通用标题；在模拟检索中准备真实New Street研报/评级信息，不直接将标题改成Gold事件名 |
| ADD-CEG-DOE | 正文为空 | 补足DOE原发与条件性贷款条款，保持“承诺/条件性”事实状态 |
| ADD-COHR-CORNING | 原正文仅121字符 | 恢复康宁披露及对COHR的事实背景，不把原运行PIN写入回测 |
| ADD-SNOW-EVERCORE | Barron’s多股票汇编 | 保留原题及相关段落，不换成虚构的Snowflake专属原题 |
| ADD-NBIS-CITI | publication_time_basis=DATE，但published_at带16:00时间 | 按日期级处理，不能把占位时分当精确发布时间；仅补构造所需的时间锚点，Gold不重审 |

历史D1/D2优先沿用原Case的旧版本；如版本在新as_of之后，使用当时已有的最近版本。只做版本级恢复，不重新生成研究，不把今天的current研究搬入旧情景。本轮不交付D3/Policy资产。

历史W1/W2不重跑，也不将旧ARCHIVE/旧路由结果作为NO_TRADE标签。用户保留的44条都可以直接评估W3方向；这是固定情景下的W3决策测试，不是当前W1→W2→Router→W3全链路复现。

## 4. 证据与时间：把准备责任移回构造侧

### 4.1 模拟Web Search重做

使用现有 `collect.py` 和其Luna/max任务，不另建采集运行平台。每个Case先从W3视角列出会改变当前选择的研究问题，再执行检索与打开：核心披露和时间；变化前的可比Prior；经济分量与必要传导；原文细节；同期回应与决定性反证。阅读与停止条件见手册。

取消“最多6次、通常4次”和仅简短改写的合同。普通Case参考准备12–20份有实质内容的来源页面，复杂跨公司事件可20–30份或更多；这是准备预算参考，不是最低数量门槛，也不能用同源转载凑数。足够的关键原文、Prior和条款比域名数更重要。

资料交付应有可用正文或充分的原文相关段落；原消息已有完整正文直接复用。构造者的综述可以做导航，但不能替代全部原文，也不能冒充逐字原文。对付费原文使用现有可访问渠道、授权副本或同篇合法分发；需要的事实仍由构造侧补足，不能靠“不要怀疑证据”把单句话摘要变成完整资料。

现有70,000字符研究打包也要改：给构造Agent同一份业务提示词及D1/D2读取入口，按需读章节/Unit，不再在不稳定文件顺序中吃完预算后直接截断。允许标准本地文件工具与真实Web Search；不接生产MCP/券商，也不让它读取Gold、Tag、旧W3判断或收益。无需额外harness。

### 4.2 时间规则

- 有真实时分的Case：as_of取该消息实例的发布时间。内部保留已有秒与时区，展示为 `yyyy-MM-dd HH:mm`，不把精确时间提前截到尚未发布的分钟起点。
- 只有日期，包括basis=DATE而字符串带占位时分：使用原发信源已确认的首发时间加5分钟。此处只补时钟，不重新判定是否过时或是否应交易。
- `as_of`、新闻截止、价格observed_at均固定为该Case时点。模型运行耗时、SDK超时和作业状态使用真实时钟。
- 用户已确认的时效性标签和Gold不再复核。既有首发时间和传播时间可以作为事实资料呈现；已有时差可记录在编制侧，不再安排独立时差调查、淘汰或待评分流程。

### 4.3 三段真实价格快照

改造 `prices.py` 的准备阶段，复用生产 `w3_market.py` 的 `MarketProvider.history`、`compile_series` 和路由规则，通过远端拉取后直接存文件：

1. 最近5个已完成的XNYS RTH交易日日线，独立请求 `1 day/use_rth=True`；不再从10分钟线合成自然日日线。
2. 截至as_of前24小时的真实60分钟线。
3. 截至as_of前4小时的真实5分钟线，按生产规则拼接SMART/OVERNIGHT。

请求明确传历史end=as_of。不能把旧10分钟拆成5分钟，不能填造零成交K线。历史请求可能返回跨as_of的完整bar；Agent快照中剔除这些bar，不能把未来OHLC放入forming后交给模型。只保留截至as_of已经完成的日线/小时/5分钟bar。

已知核心披露不在标准窗口时，预备事件段：从事件前30分钟到 `min(事件后1小时, as_of)`，不用运行时再连IBKR。生成统一 `market_view.json`，保留生产的series/columns/rows与时间含义；采集错误、权限、缓存及实时刷新健康信息留编制侧，不把历史快照按今天的系统时间标成STALE。

快照必须确实来自真实数据；不能将空文件或编造数据标成完整。构造错误在准备侧修复，或记为数据构造失败，不伪装成模型NO_TRADE。正常休市区间与形成中末条无需另做防御性说明。回放不刷新行情，也不模拟推理期间时间推进。

## 5. 中文backtest.md及最小Agent视图

新增独立 `prompts/w3_backtest/backtest.md`，与生产AGENTS、唯一Skill同为版本化资产。母Thread初始化时将Skill与backtest正文一并交付，并物化文件；不能只在task.json里留一句“离线限制”。其hash进入run快照、母Thread复用key及恢复身份。生产调用不加载此文件。

建议首版正文如下；它是环境合同，不改Surprise、方向和行情权限的业务标准：

```markdown
# W3 历史情景回测规则

你正在执行一个历史消息的第一阶段决策。业务判断沿用已加载的AGENTS.md与skill.md；本文件只规定本轮情景、资料与能力的对应方式。

每个Case给出的as_of是本轮唯一的当前时点。假设自己身处这一时点，只依据当时已知信息完成判断。不要用系统日期或本机时钟重算消息年龄、交易日或机会窗口；模型运行耗时也不会推进本Case的业务时间。

本轮模拟Web Search材料完整代表该Case在as_of时点研判所需的已知公开披露。材料内容真实，发布时间与来源由构造侧整理；它们应像该时点正常搜索取得的资料一样使用。无需因本地文件、无法实时打开网址、缺少网页历史存档或采集方式而再次怀疑真实性和完整性，也不要将离线环境本身写成NO_TRADE理由。

真实报道中的传闻、预测、条件性承诺与已实现事实仍保持各自含义。信任材料不等于所有商业条件已经兑现，也不等于必须交易。仍按业务提示词判断Surprise、经济分量、净方向与消息时效。

本轮价格快照真实、完整且已按as_of准备，可直接信任并使用，不再核查数据供应商、权限、实时刷新或采集健康状态。按已完成bar核验与本次催化对应的显著重定价完成态；价格仍只能按业务提示词规定的权限使用。

本轮不提供历史交易意图、WATCH、PIN和Policy业务机制，不需要查找、补建或解释它们的缺失，也不能把未提供状态当作已有交易、禁止交易或信息不存在。市场过去已经公开的事实仍通过本轮资料中的内容与时间判断。

研究入口以当前任务给出的冻结资料入口为准。可以按需要读取和检索本轮材料；不调用实时Web、Data MCP、IBKR、账户或生产服务，不通过shell/Python访问外部网络，不调用子Agent。

本轮只完成trade_decision，输出当前schema要求的decision、reason、prior_expectation、expectation_delta。不执行后置运营、不创建WATCH/PIN、不模拟发单。不要因为这是回测而要求额外确认、降低或提高交易门槛，也不要迎合假想Gold；按同一业务标准提交真实判断。
```

建议payload保持以下最小范围：ticker、phase、as_of/cutoff_at与时区、原消息及来源、可用W1/W2事实判断与理由、D1/D2入口、Web证据入口、价格快照入口、当前输出合同。原W1/W2完整结果保存在构造侧；Agent视图不再发送Policy/Condition ID、消费/使用状态或关联路由。保留原事实判断，不伪造OPEN、HIT或RELATED结论。

明确移出Agent视图：Gold/Tag/入选原因、旧W3结果、web_gaps、historical_state_limitations、collector研究笔记/结论、counterpoint_or_limit评语、UNKNOWN/UNVERIFIED历史快照审计标签、采集失败清单、direct_replay_only/would_enter_w3、旧交易状态及CLOSED/过期/宿主拒绝提示。这些信息可保留在preparation或报告，不能经raw文件入口重新读到。

## 6. 执行编排：共享母Thread，只跑决策

### 6.1 run级资源与线程

将现有 `ReplayRunner` 的Worker/Agent/本地Journal生命周期提升到run级，继续用已有Thread生命周期实现。一个固定模型profile对应一个母Thread；key包含模型/effort、AGENTS、Skill、backtest及schema版本。母Thread只读公共规则并返回ready，不放ticker研究、Case内容或答案。

每个Case独立创建workspace与子Thread，从已就绪母Thread Fork。无Policy/Event关联Resume、不继承其他回测Case的业务结论。模型或规则变更开新run，恢复则沿用已有母/子Thread及已保存结果。run结束统一关闭Worker，不能在第一个Case结束时关掉共享资源。

共享Journal记录线程/阶段与真实作业时间；as_of放在独立Case输入中，不依赖给共享Journal安装“当前Case时间”的可变全局clock。因为不运行TTL、宿主交易和运营，无需引入新的语义时钟机制。

### 6.2 决策执行终点

v2回测不再调用 `W3Execution.trade()`：该方法在模型后还会创建TradeRecord、记录宿主disposition并排队运营；仅删除 `execution.operations()` 不够。

在 `runner.py` 复用现有母Thread初始化、`ThreadForkRequest`、`CodexW3AgentRunner.run_phase(TRADE_PHASE)`、业务schema解析和阶段持久化。模型返回合法方向对象即保存 `decision.json/result.json`，到此结束。不生成交易/运营effect，也不做Policy授权和历史消费映射。

不另造一个Agent执行器或交易模拟器；需要的变化集中于现有Runner和编排函数的回测资产接入点。旧run的两阶段报告仍可读取，不重写成v2结果。

### 6.3 资料入口的便捷复用

现有 `_LocalWorker` 已有本地ResearchMcp启动、D1/D2安装、context绑定和OFFLINE market_view接口，成本主要是接线。建议在前述去偏和单阶段跑通后直接启用这条现成路径：

- `components`只安装历史D1/D2；evidence只放清洗后的Web资料和真实market_view，不安装D3、状态、历史意图或编制审计。
- 预绑定 `w3_context_v2` 和本阶段manifest，再调用生产run_phase。这样可避开旧 `W3PreparedContext` 强制PolicySet/ReferenceView的要求，无需伪造Policy资产。
- Case请求开启research_assets_mcp，保持data_mcp=false、web=disabled。复用read/catalog/search分页和market_view；market_view只返回固定文件，不启动后台Broker请求或READY补池。
- 不新增Web Search MCP。冻结Web资料作为raw_document被现有read/search访问；分页收据和正文在外层预留足够输出预算，不再一次打印整份原件。

这项按用户给定低优先级安排，不成为数据重构、backtest提示和单turn执行的前置条件。复用遇到额外生产依赖时，先沿用已有切片文件入口完成主线，不为此建设替代服务或新reader框架。

## 7. 改动文件与实施顺序

| 文件/位置 | 具体改动 |
|---|---|
| `w3_backtest/dataset.py`、`remote_export.py` | 59条目录/44条选集；取消v2噪音采样；指定ID只读恢复NativeContent、旧D1/D2与原消息；统一as_of |
| `w3_backtest/gold.py` | 新最终文档导入及Tag，旧Gold导入兼容保留；不把移除项写为NO_TRADE |
| `w3_backtest/collect.py` | 以决策问题驱动、增加正文与Prior覆盖、去掉6次上限；采集/审计与Agent资料分离 |
| `w3_backtest/prices.py` | 真实三段数据与事件段，复用生产Provider/编译函数，生成统一静态快照 |
| `prompts/w3_backtest/backtest.md` | 新增中文环境合同，独立维护、冻结 |
| `w3_backtest/runner.py` | run级Worker/母Thread；Case Fork；直接第一阶段；不再重建历史业务状态 |
| `persistent_runtime_v2/w3.py` | 仅增加回测专属补充指令文件的物化/初始化入口，替换旧OFFLINE单句说明；PRODUCTION默认不加载、不改业务逻辑 |
| `w3_backtest/cli.py` | v2构建、提示词来源快照、backtest/模型/数据身份、共享run生命周期；保留现有build/run/resume/report/compare |
| `w3_backtest/report.py` | 决策成功与数据/模型错误分开；运营标记NOT_REQUESTED；按新Gold/Tag分层，旧run兼容 |
| 现有回测/线程/market测试 | 覆盖实际变更，不为Gold结果写镜像单测 |

实施次序：①选集与时钟、旧原始数据恢复；②构造器和三段行情；③中文环境合同、净化Agent视图；④共享母Thread与决策终点；⑤现成本地研究入口；⑥小样本真实验收后跑44条。各步不引入额外审批或多套并行协议。

## 8. 验收与报告口径

离线验证只检查真正影响可执行性和隔离的条件：59/44身份及Gold正确、25旧NOISE未入集；DATE占位时分没有当真；提示词/backtest/model已冻结；母Thread每profile只初始化一次且子Thread独立；schema仍为v5四字段；没有运营/交易effect；不支持的状态、Gold和编制评语不在Agent可读范围；三段粒度和已完成bar时间正确；分页可续读。不能用字符串扫描把新闻正文中的“风险/未确认”删除。

真实验收先选6条：New Street空正文、CEG贷款、NASA、INTC–OpenAI、Synspective时效性NO_TRADE、MU-03-03。用现有 `--model/--effort` 显式固定一个模型profile和一份提示词，避免旧Policy分类引入模型混用；检查实际规则继承、资料阅读、所需行情与单一决策；不是要求6条必须全判对。工程正常后直接跑44条，不要求另做ABC实验平台或反复刷分。

报告同时展示：44条总结果、非边缘32条、边缘12条、时效性9条；交易正样本召回和误交易单列。非边缘交易正样本只有CEG与New Street两条，不能用高NO_TRADE占比下的准确率代替Alpha捕获能力。边缘案例的错误应表述为与用户Gold分歧，不自动归为无争议业务bug。

新集、时间和证据均发生变化，旧73条的61/73或12条选集的4/12不能直接用来证明校准改善。历史33个保留Case可以做同Gold重评分及理由对照，但要注明输入变化。环境诱发的“预采集不可信、没有历史网页、缺PIN/WATCH”等拒绝理由应消失；正常基于经济分量、Surprise或用户已确认时效事实的NO_TRADE必须保留。

### 本轮勘察依据

- 用户最终59案例文档SHA256：`0ad21e28c827baa68acb63eea3a14b85541d4c005f9e6a0ada8146fc45918e4c`。
- 用户临时排查总结SHA256：`8a25bd633adf7eec90193a0576526124d88cf7efe2b3c634de65faddd47e9d31`。
- 本地现行 `w3_backtest/*`、`persistent_runtime_v2/w3*.py`、`codex_worker/w3_market.py`、`mcp/w3_research_server.py`、生产提示词及旧run原生轨迹。
- 远端runtime SQLite只读核实新增11个Case及active execution bundle；本轮未拉行情、未运行研究/决策模型、未修改生产。
