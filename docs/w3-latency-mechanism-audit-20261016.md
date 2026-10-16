# W3 时效机制审查与轻量探针方案（2026-10-16）

本轮只读生产并导出证据，没有修改编排、提示词、模型配置或部署，没有重跑业务 case。目标是为持久化研判 p90 ≤150 秒找到可执行的优化顺序。证据目录为 `eval/w3_latency_20261016/`；20 条逐工具审查见其中 `case_flow_review.md`。

## 结论和统计口径

W3 的主要成本是**长上下文下的多轮模型交互、每条交易任务的启动，以及部分 case 的首次 Template/Fork/文件准备**。本地读文件通常不足一秒，单批 Web 通常约3–5秒；但每次串行工具批次都需要再次模型处理。因此应优先缩减无效上下文和往返轮数，同时削掉固定启动成本。当前数据不支持把慢研判统称为网络慢，也不支持先提高全局并发。

只改 W3 还不足以达到整个 case 的150秒目标：并发修复后183个W3 case中，**Case创建至W3 Effect创建这一段本身 p90=200.5秒**。即使把W3耗时降到零，沿用这个上游分布的端到端p90仍不能到150秒。本轮先解决W3是合理顺序，后续还必须审查W1/W2、准入、输入准备与队列。

本报告区分三个终点：

- 主时效：`trade_decision.committed_at`，即宿主已持久化接纳交易/不交易判断；形成意图的case同时核对真实`trade_intents`。这里不把模型返回但未被宿主接受当完成。
- W3耗时：`route_to_w3 Effect.created_at → trade_decision.committed_at`，包含W3调度、准备、重试、Worker和提交。
- 后续运营：`post_decision_operations.accepted_at → committed_at`。已经在独立Effect中异步执行，不应加到交易判断关键路径上；它仍竞争CPU、Worker启动资源并影响后续上下文。

Case端到端暂用`Case.created_at → trade_decision.committed_at`。消息采集/发布至Case创建另属上游，不能将本表称为“新闻发布至交易”。旧`completed_at`不是所有历史记录都能代表W3交易完成：样本MU SpaceXSI的该字段早于后来的决策提交。建议验收固定使用明确的决策提交时间。

## 样本、版本和全量参照

按北京时间周三、周四，即 **2026-10-14 00:00至10-16 00:00**，按Case创建时间筛选；对应UTC `[2026-10-13T16:00, 2026-10-15T16:00)`。不是按美国交易日筛选。

窗口内904个Case，430个实际进入W3，全部REALTIME、orchestration_revision=3。其中426个取得决策提交，4个无提交。409个运营已提交、17个运营失败。17个失败中13个为Event引用不在Reference View，其余4个为OLD缺少Event引用或引用URL而非E#。这是运营正确性问题，不将失败算作快速成功。

窗口内实际交易意图**仅3个，全部导出并进入深审**，不以成交筛选。两个RKLB意图来自同一Neutron消息的不同Case，保留真实重复，不合并成一条。另选7个Policy召回、10个按ticker/北京时间日期分层抽取的普通W3，随机seed=20261016。召回包含HIT与RELATED，样本总计7 HIT、3 RELATED-only、10 OPEN。深审样本用于机制分析，其p90不能代替生产自然分布。

远端当前主检出为`e929134d70995933d16e23df6e66c6195d7ac3b8`；scheduler镜像`doxagent-v2:main-e929134d`，Worker镜像`doxagent-v2:main-618fd9c5`。**镜像标签不同不等于本次相关源码不同**：分别从两运行容器读取源码，统一LF后，W3、生命周期、SDK、Worker、队列等13项运行容器源码比对均与本地当前源码一致，hash在`extended_analysis.json`。20个Case冻结AGENTS一致，分析实际使用冻结文件和原生回执，不以今天的提示词推断历史步骤。

并发修复在北京时间10月15日05:22:31启动，按Case创建时间分组如下。发布前Case可能跨发布恢复，发布后也有共享负载变化；此表是观测分组，不是严格控制实验。

| 人群 | W3 Case /有决策 | Case至决策 p50 / p90 | W3至决策 p50 / p90 |
|---|---:|---:|---:|
| 两天全部 | 430 /426 | 421.9 /2424.1秒 | 204.3 /342.7秒 |
| 并发发布前创建 | 247 /243 | 700.6 /4288.9秒 | 205.9 /364.4秒 |
| 并发发布后创建 | 183 /183 | 306.6 /449.2秒 | 203.1 /303.1秒 |
| 20个深审样本 | 20 /20 | 394.1 /2604.6秒 | 182.0 /347.5秒 |

p90统一采用nearest-rank（排序后ceil(0.9×n)）；缺失时间保留null，失败和样本缺失数单列。不同分段的p90**不能直接相加或相减**。全量有决策426个中仅76个W3耗时≤150秒；深审20个中6个。

## 实际执行链及分段耗时

代码链为`W3Execution.trade → prepare_case → derive_research_assets → _thread → run_phase → Worker → schema/业务校验 → TradeOutputService事务提交`。

1. W1/W2路由生成`route_to_w3` Effect，coordinator领取执行，登记交易stage。
2. 冻结72h历史成员与PIN/WATCH全局输入；加载版本固定D1/D2/PolicySet/Reference View。已有进程内context cache和研究切片cache，但没有耗时/命中计数。
3. 选择可复用Case Thread；无合适Thread则取兼容Template，冷Template要先跑一次模型初始化，之后Fork。Template/Fork与执行Capsule为不同App Server，writer交接有既定修复，不能为提速撤掉释放机制。
4. 在workspace写AGENTS、唯一Skill、研究切片（按workspace/asset_key去重）、Reference View、task和schema。研究切片首次逐文件HTTP PUT；当前缺少批量总耗时、文件数和字节观测。
5. Worker接收排队、获得启动槽、找同Thread Capsule或新建进程，初始化SDK/工具注册表/Data MCP，resume Thread并发起turn。
6. Agent读控制文件、消息、W1/W2、意图/PIN/WATCH；按需定位研究、定向Web、在拟交易时查历史行情否决完成态；继续推理并输出单个交易对象。
7. 宿主校验、存结果、ACK Worker、执行交易输出事务、提交stage；独立enqueue运营Effect。
8. 运营在同Thread延续，刷新PIN/WATCH，维护Novelty/Fact/Policy coverage和动作，不重新交易，最终释放Case所有权。

下表为20条深审交易阶段；标为“组合”的区间仍不可解释成某个单项。

| 分段 | 覆盖 | p50 | p90 | 含义和限制 |
|---|---:|---:|---:|---|
| Effect创建→stage首次接受 | 20 | 1.2秒 | 3.1秒 | 首次领取；不覆盖随后全部重试间隔 |
| stage首次接受→最终Worker job创建 | 20 | 16.8秒 | 78.1秒 | 组合：历史/研究、Template/Fork、写文件、可能的既往失败/等待 |
| Worker创建→started_at | 20 | 0.038秒 | 0.174秒 | 准入及ensure_attempt前后；不是纯队列的严格边界 |
| Worker cold_start_ms | 20 | 33.7秒 | 49.9秒 | 含启动信号量等待、Capsule/SDK/MCP/resume/turn启动，不是纯进程spawn |
| SDK整个交易turn | 20 | 102.3秒 | 237.3秒 | 含模型多轮、工具、潜在压缩，不是纯推理 |
| 工具区间并集 | 17 | 15.2秒 | 55.3秒 | 原生外层exec发出→回执，扣除重叠；3条轨迹缺失 |
| turn减工具并集的剩余区间 | 17 | 82.3秒 | 189.6秒 | 模型/服务排队/传输/压缩等混合；不能命名为纯reasoning |
| Worker结束→交易stage提交 | 20 | 0.77秒 | 1.32秒 | 含结果接收、校验、ACK及提交；最大3.89秒 |
| 交易提交→运营stage开始 | 20 | 3.0秒 | 8.4秒 | 最大48.9秒，不阻塞已提交交易 |
| 运营stage执行 | 20 | 64.2秒 | 107.7秒 | 后台路径；运营SDK p50=57.5秒 |

全量回执的426个成功交易turn：SDK p50=132.3秒、p90=214.0秒；启动p50=35.1秒、p90=62.6秒。另保留17个失败尝试；不将缺失SDK时间填0。426个运营turn虽Worker均成功，仍有17个被宿主校验拒绝，必须区分模型完成与业务提交。

### Agent遵循的流程及实际偏差

17条仍有完整原生轨迹的case合计167个工具批次：交易通常7批（p90=17），对应模型response通常8轮（p90=18）；运营通常2批/3轮。70批Web工具全部发生在交易阶段；17条运营没有Web/行情研究调用。没有证据表明所有case被强制逐项执行同一张分析表，存在明显快路径和长研究路径。

- **每次重新识别控制合同。**常见首批是pwd/find/读取AGENTS/task/schema；部分case接着再次读Skill、再次查task keys、枚举workspace。shell本身p50=0.46秒、p90=0.91秒，主要可优化的是多次模型往返和工具输出长度。
- **局部研究不总是一次定位成功。**通常查index、按业务词grep D1/Reference View、读D2 Unit/完整Policy；存在猜测字段、猜文件名/路径后找回再读的重复步骤。不是文件系统读取慢。
- **Web多次追溯相同消息。**已提供正文也会搜索原稿、查IR、点主页、再次搜索媒体转载。合理的来源/时间核验与收益很低的补搜混在一起。单批Web p50=3.52秒、p90=4.89秒，但顺序执行会叠加模型处理。
- **拟交易时行情入口探索。**三个交易case都先查`IbkrTwsSession/historical_bars`代码或枚举文件，再读catalog/查ALL_TOOLS，最后调用实际开放的`mcp__data__ibkr_market_history`。提示词说“通过Python调用既有IBKR”，真实可用入口是MCP，这个表达与能力发现不一致，产生确定的探索步骤。
- **后台运营通常复用研究，但仍长推理。**如ASTS比较case，运营只执行1个约0.46秒的本地工具批次，却花143.7秒；不能通过把运营工具改快来解释全部时间。

### 三个交易意图的关键证据

| Case | 业务/路径 | 前置组合 | Worker启动 | SDK turn | 工具并集 | 非工具剩余 | W3合计 |
|---|---|---:|---:|---:|---:|---:|---:|
| `case_c5e73efb13bc4bf9b262e198fd4227cf` | INTC工厂爆炸，RELATED→Expert SHORT | 17.4 | 35.0 | 292.9 | 36.9 | 256.0 | 347.5秒 |
| `case_cf7cc11b11df4d298b1d15b94864196e` | RKLB Neutron延后，Policy SHORT | 58.5 | 28.6 | 101.3 | 55.3 | 46.0 | 189.8秒 |
| `case_05b485b82f8e404e8849f28c88e79be0` | 同一消息另一来源，Policy SHORT | 8.8 | 30.6 | 137.1 | 64.7 | 72.4 | 178.4秒 |

表中前置组合起于stage而非Effect，另有少量领取、接收与提交，因此不能仅相加前三列声称精确闭合。

INTC交易阶段有18个工具批次，6批Web及多批研究/能力定位。原生时间线显示前四批主要是控制文件/研究导航，之后仍反复查询爆炸、停产和来源；工具实际等待只有36.9秒，整个turn292.9秒。不能直接认定每次搜索都无价值，但这是减少串行往返和无效研究的最高收益样本。

两个RKLB交易case均两次行情失败：MCP返回`ibkr_gateway_unavailable`，具体为TWS nextValidId握手超时、提示client ID 71。实际Observation call记录分别为22.248+20.019秒、20.837+20.004秒；外层exec跨度分别为43.131、41.061秒。改变duration或原样重试没有解决连接层错误。INTC同工具成功，provider执行2.715秒、外层3.515秒。

**这里只能确认握手失败，不能确认就是client ID冲突。**源码确认MCP传入的是配置中的IBKR_TWS_CLIENT_ID，`ibkr_history.client_id`虽在case输入中生成，实际MCP接口不接受这个字段、provider仍按settings创建session。同进程锁不能协调不同MCP进程。应先修正实际连接身份传递/会话复用并记录握手错误，不能将减timeout或放过行情核验当唯一修复。

### 压缩及事故长尾

17个可审查的交易turn没有发现压缩完成标记；3个运营turn有压缩标记。可见活动→压缩完成的间隔分别为52.186秒（INTC爆炸）、17.305秒（RKLB Stocks to Watch）、23.472秒（RKLB评级）。**这些是混合区间，不是已测得的压缩耗时。**缺少开始标记，不能扣成“节省52秒”；也不能据此证明另外3个无轨迹case没有压缩。

压缩后INTC、RKLB评级case重新读AGENTS/task/schema/Skill，说明不仅可能有压缩服务成本，还出现重新定位成本。本次压缩发生在已提交交易之后，因此不是这三个case第一阶段的直接阻塞点；Thread后续复用、后台资源争用仍可能受影响。

MU SpaceXSI样本W3=4068.3秒，其中stage接受到最终Worker创建3999.3秒，最终SDK仅30.94秒。Effect残留错误明确为active writer，属于已修复事故/恢复长尾。不能把3999秒叫作研究准备，也不能拿这个样本要求普通模型提速100倍。事故Case保留原始SLO，正常路径分析另分组，不删除长尾美化p90。

## 上下文中值得优先削减的确定性负担

当前Template不是只有短指令：task中内联完整`research_index`，12个涉及Template的索引约143–271KB。其D3 section约123–237KB，约占索引86%–90%。源码把每个Policy的完整`match_scope`复制进导航项`scope`，还把Shell除units外的内容放入`content`。这是派生导航层冗余，完整Policy正文已经有对应文件。

17条可审查case的首个模型response输入：中位106,496 token，p90=149,550，最大226,904；运营首个response中位154,787。累计usage可达数百万，是多轮上下文反复处理的总和，不能当成一次输入；cached_input是input子集，也不能相加。首轮上下文还包括系统/工具、继承历史与case数据，不能把全部token都归因于index。

交易task还直接注入全库`policy_usage`；INTC例约38,967字节，PIN约15,409字节。这些有业务用途，建议完整文件化并仅内联本次候选及必要状态，保留全库可检索/可选Policy能力，不做静默top-K截断或删除历史。

## 优化优先顺序和边界

**P0：小范围探针和W3专用启动路径，尽快并行准备。**先把固定30–60秒启动拆出真实模块/等待，随后在W3只开放`ibkr.market_history`的既定权限下，使用轻量工具注册路径。`OpenAICodexRuntime.__init__`和`DataMcpApplication`都构建全量real tool registry；`tools.factory`又在模块顶层导入大量无关provider。仅最后过滤工具名单不能降低import成本。实现W3专用registry/contract入口，重型factory在其他节点按需导入，默认其他节点行为不变。不要先重构全部工具系统。目标是交易冷启动p90从现状62.6秒压到10秒量级，实际收益以探针验收，不承诺先验数字。

**P0：修正IBKR真实调用链与连接层重复等待。**将宿主可信的attempt/capsule身份用于实际MCP的只读client ID配置或复用受管session，保留账户/交易隔离；case输入那个未被消费的client_id不能冒充隔离已生效。同一连接层错误的第二次请求不应靠改bar/duration重复握手。工具明确暴露失败层级与有效重试建议；连接恢复后允许有限重试。不新增常驻行情服务或一套大型调度器。

**P1：把Template导航变为真正的导航，并减少控制文件重复读取。**index只保留完整身份、title、path、Shell关联和必要简短定位元数据，全文scope留在Policy正文；Template加载AGENTS+唯一Skill、短目录/路径，case内联完整实际召回Policy，其他Policy仍可按全量目录读取。保留版本pin、权限、源全文与新Thread兼容性key更新；不要让旧Template复用新格式。把canonical task/schema/catalog路径一次明确给出，取消误导性的Python入口描述，合并控制合同读取。预计主要收益来自首轮上下文和减少往返，需真实A/B验证。

**P1：让普通OPEN低价值消息和重复Policy跟进能及时结束。**对决定性拒绝已成立的case，停止追查次要原稿/全文/无关研究；HIT继承既有传导，仅处理实质疑点；已有同事件意图时聚焦本次增量。不把它改成“禁止Web”“搜索最多两次”或“超时统一NO_TRADE”。具体提示词迭代用本轮三个交易case以及既有73条冻结回测对照，尤其不能为了快而漏掉有效alpha。

**P2：只在探针证实后优化首次研究文件下发/Fork。**首次切片目前逐文件远程PUT，可增加受控批量下发或共享不可变asset目录，但必须保留workspace身份、不可变版本、原路径校验和幂等。不因为组合前置p90=78秒就先认定都是PUT慢。Template只对当前活跃ticker/研究版本/模型profile做已有机制上的预备，不新增常驻定时刷新队列。

**P2：运营独立做轻量化评估。**运营平均研究负担不同，可试更低effort/精简上下文；当前同Thread上下文保留过多、几批工具之外的大段耗时值得处理。但它已不阻塞交易提交，单纯优化运营不会直接把W3交易p90减去运营p90。不要重新合并两阶段，也不先用换模型掩盖结构问题。

这轮不建议提高Worker总容量、统一拉长timeout、增加并行研究子代理、重建全套Tracing平台，或取消既有Thread writer释放、输出校验与交易去重。

## 观测缺口与可直接实施的探针方案

缺口较多，值得先做一轮轻量探针。现有能力能恢复大段耗时和部分工具，不足以精确选定启动、文件/生命周期和模型轮次内部的修复比例。

| 当前可得 | 当前拿不到/容易误读 | 补法 |
|---|---|---|
| Effect/stage/Worker锚点 | 领取、历史冻结、context cache、切片、文件下发、Template等待、Fork各自耗时 | W3宿主边界span |
| created_at、started_at、cold_start_ms | 纯Worker队列、启动信号量等待、Capsule spawn/import、SDK/MCP握手、resume耗时 | 拆分现有启动计时 |
| sdk_duration_ms、部分SDK item duration | code-mode内部Web经常不在SDK工具计数；事件缺墙钟与起止 | SDK started/completed +原生工具元数据投影 |
| Observation `calls.latency_ms` | MCP预算等待、IBKR connect/握手/合约/历史请求细分 | 先复用现有calls；必要时再加IBKR连接阶段span |
| 原生`compacted`完成 | 压缩开始、确切持续时间及压缩后重读成本 | contextCompaction item起止；只有结束则partial |
| token_usage_record逐response | provider服务端排队、prefill、推理、上传/下载分别耗时 | 先记response间隔/usage；无正式provider证据时保留residual |
| job汇总及slowest_steps | ACK清掉request/events/agent_loop；过期workspace/rollout无历史逐步证据 | ACK/回收前持久化小型timing receipt |

### 第一版改动面

不改业务schema、模型提示词或Agent动作；不新增模型调用。新增一个小型`w3_timing`辅助模块，使用`time.perf_counter_ns()`计本进程span，UTC只作跨进程锚点。数据写attempt-local `audit/timing.jsonl`和`timing_summary.json`，在现有Worker telemetry中加入可选timing引用/摘要，旧回执缺字段仍可读取。

最少需要触及四类边界：

1. **W3宿主：**`w3_execution.py`的trade/operations、`w3.py`准备与run_phase。记录`history_snapshot/context_load/research_derive/thread_select/template_initialize/template_wait/fork/workspace_materialize/worker_roundtrip/output_validate/trade_commit/operations_commit`。workspace只记录文件数、总字节、cache hit及累计/最长PUT，避免每个文件写一条SQLite事务。`fork` span覆盖HTTP RPC整体，必要时生命周期服务另记SDK客户端init/list/fork/close。
2. **Worker：**`jobs.py`在dispatch允许启动、进入/获得`_startup`、runtime.start返回、turn完成和cleanup完成处打点；`capsules.py/capsule_child.py`只增加boot_ready/SDK-ready时间元数据。让`worker_queue_wait_ms`在首次dispatch就结束，`startup_wait_ms`在取得启动信号量时结束，`runtime_start_ms`从此到turn handle就绪。保留旧`queue_wait_ms`兼容，但标记其旧口径；它当前在RUNNING时计算，包含冷启动。不要直接减掉后改名。
3. **SDK：**`sdk_runtime.py/telemetry.py`处理`ItemStartedNotification`与`ItemCompletedNotification`，按item_id记录相对turn时间，覆盖contextCompaction和dynamic/code-mode外层。安装SDK已经提供这两种notification以及`ContextCompactionThreadItem`，无需猜接口。文件仅保存类别、name、item/call ID、起止、status、字节数，不保存reasoning或工具正文。代码模式子调用若SDK不暴露，用现有rollout一次增量扫描投影call/output时间和工具名；不执行或改写Agent生成的JS，不为精确嵌套语义编写通用JS解析器。
4. **保留与汇总：**`job_store.py` ACK之前把compact timing摘要保留；`turn_summary`保持总数/分位所需记录而非只有末尾256项或最慢5项。摘要不因workspace回收而丢失。沿用当前磁盘管理做独立小文件保留约14天，保留失败/重试ordinal、每次尝试的身份和错误类；不保留完整网页/工具输入来换取观测。

建议每条span最小字段：`schema_version, case_id, ticker, phase, attempt_id, job_id, thread_id, turn_id, worker_generation, release_revision, span_id, parent_span_id, name, start_utc, elapsed_ms, status, counts/bytes, cache_hit, error_class, measurement_quality`。未开始/未完成为partial/null，不能置0。同一进程用单调时钟；跨进程不比较两个monotonic值，也不声称UTC差值有毫秒级精度。

**Data MCP第一版直接复用已有Observation call记录**，本轮五次实际行情调用都已恢复provider latency，无需再包一遍所有provider。仅缺“连接/请求”细分时，在`ibkr_tws.py`受管session的connect/nextValidId/contract/history边界加几条无正文span；错误层级与线程间client_id另行修复。禁止记录capability、认证或账户数据到timing。

探针写入缓冲后追加，不每事件HTTP往返、不修改业务事务；I/O异常只增加一次观测降级标记，不使模型重试或阻止交易。队列、运行、结束采用同一个业务身份和attempt序号，重试另外建span，不用最后一次成功覆盖此前耗时。

### 验收和后续时效预算

- fake clock/受控延迟验证队列、启动等待、runtime.start、turn与提交能闭合；缺失结束/重启标partial；并行工具按区间并集，不能求和重复计时。
- 验证普通/HIT/RELATED、冷/热Template、Fork/Resume、成功/工具失败/宿主拒绝/重试的探针身份正确；ACK后summary仍在，旧回执可读；不把失败转成NO_TRADE。
- 在已有真实业务自然流量观察至少一整交易时段，按版本、类型、ticker、模板冷/热、重试、意图与否分组；失败率与无完成覆盖率同行展示。
- 对照原生rollout、Worker汇总与stage提交，分别解释重叠或差额。provider内部拿不到的部分继续叫residual，不靠减法造“纯推理”。
- 规划W3关键路径≤约90–100秒、上游≤约45–50秒、提交余量若干秒，作为工程分段预算；最终是否p90≤150秒必须用同一case的端到端分布验收，不能相加各段p90。先降低固定启动与无效模型轮次，再评估价格预取/更低effort等质量敏感变化。

完整工具流程、20个case分段表、数据覆盖和复算脚本均随本报告保存。本轮没有植入生产探针；这里是可执行方案，避免把建议描述成已上线观测。

## 20条样本总表

单位秒。前置是首次stage接受到最终Worker创建的组合区间；缺失原生工具轨迹写“缺失”，不是0。运营单独展示，不并入交易关键路径。逐工具命令与时间见 `eval/w3_latency_20261016/case_flow_review.md`。

| Case | ticker/类型 | Case至决策 | W3 | 前置组合 | 启动 | SDK交易 | 工具并集 | 非工具剩余 | 运营stage |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `case_c5e73efb13bc4bf9b262e198fd4227cf` | INTC/RELATED·意图 | 1254.6 | 347.5 | 17.4 | 35.0 | 292.9 | 36.9 | 256.0 | 102.0 |
| `case_cf7cc11b11df4d298b1d15b94864196e` | RKLB/HIT·意图 | 415.1 | 189.8 | 58.5 | 28.6 | 101.3 | 55.3 | 46.0 | 13.4 |
| `case_05b485b82f8e404e8849f28c88e79be0` | RKLB/HIT·意图 | 373.1 | 178.4 | 8.8 | 30.6 | 137.1 | 64.7 | 72.4 | 20.8 |
| `case_8a99488e95a4406681262e857e768da8` | MU/HIT | 4311.7 | 4068.3 | 3999.3 | 32.4 | 30.9 | 缺失 | 缺失 | 24.5 |
| `case_db829e71fcf74a88b1ba69672a9ae481` | INTC/HIT | 915.7 | 165.6 | 78.1 | 33.3 | 50.7 | 16.5 | 34.2 | 29.5 |
| `case_fa501d7fa20a49cf99720c441702de07` | INTC/HIT | 4416.9 | 107.2 | 19.2 | 36.7 | 47.8 | 10.1 | 37.7 | 24.5 |
| `case_3c11b525cc4a4242b74b8dec4a8ce3a0` | INTC/RELATED | 2604.6 | 139.4 | 9.8 | 34.9 | 89.9 | 7.7 | 82.3 | 23.5 |
| `case_b45cc5e094c74188b47063618ab717a5` | RKLB/HIT | 650.9 | 118.2 | 4.9 | 55.0 | 55.4 | 18.4 | 37.0 | 90.3 |
| `case_72272e4eeb934cc6b181e6143cc2b8de` | RKLB/HIT | 183.6 | 54.0 | 1.9 | 35.0 | 15.4 | 3.8 | 11.6 | 30.4 |
| `case_c8caff15a131445fac8431ea77931bdd` | RKLB/RELATED | 461.0 | 208.0 | 72.8 | 30.7 | 103.2 | 17.6 | 85.7 | 105.0 |
| `case_c4958af149b54592a55937e1f6135ad7` | BE/OPEN | 322.5 | 290.1 | 13.8 | 34.4 | 240.7 | 缺失 | 缺失 | 65.1 |
| `case_9c157cebd7aa482dad5136640a1f5b43` | BE/OPEN | 276.2 | 185.6 | 64.4 | 29.3 | 89.6 | 10.4 | 79.2 | 80.7 |
| `case_a10db1b2c2384f35bcae0fb065f06af6` | INTC/OPEN | 171.3 | 115.3 | 10.0 | 32.0 | 71.1 | 缺失 | 缺失 | 71.2 |
| `case_07bb48f22b414cb7b7bcb5d9566861e6` | INTC/OPEN | 508.0 | 168.1 | 14.0 | 43.3 | 109.4 | 7.4 | 102.0 | 107.7 |
| `case_887f5a8acf0643228f5474965071399c` | MU/OPEN | 320.7 | 189.8 | 26.7 | 34.1 | 124.4 | 12.6 | 111.8 | 57.6 |
| `case_a4bd175d9863442f902f1d51d9f92438` | MU/OPEN | 207.2 | 165.6 | 16.2 | 31.6 | 115.1 | 11.4 | 103.6 | 63.4 |
| `case_d4319d527b0d4986981acde7b1d01945` | RKLB/OPEN | 1439.8 | 369.5 | 100.8 | 72.6 | 191.6 | 35.4 | 156.2 | 147.6 |
| `case_198c965f02564b9dbc37e9e2f82489b2` | RKLB/OPEN | 340.1 | 276.3 | 10.6 | 27.6 | 237.3 | 47.7 | 189.6 | 108.5 |
| `case_07a2a0e65011418a88f03ac5a785f80e` | BE/OPEN | 233.2 | 135.4 | 15.6 | 32.8 | 85.0 | 14.8 | 70.2 | 57.2 |
| `case_132699043886430898c10aa3a557931d` | BE/OPEN | 318.3 | 234.1 | 37.4 | 49.9 | 143.3 | 15.2 | 128.1 | 77.6 |
