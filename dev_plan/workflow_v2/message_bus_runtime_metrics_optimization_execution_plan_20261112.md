# 消息总线与运行状态指标优化执行方案

日期：2026-11-12，北京时间。**本轮只读勘察与方案设计，没有产品开发、生产写入、构建、发布或重跑Case。** 本方案取代上一版方案中与本轮要求冲突的抽样及W3计时定义。

## 1. 实施结论与不能混用的时间

建议实施文章定额抽样、抓取延时、Runtime KPI调整，继续使用现有Read投影、Case贡献和指标桶，不创建监控服务、定时回填任务或逐节点计时系统。

必须先解决三项真实数据缺口：

1. **现有Case.decision_completed_at不是模型产出时间。** W3 `_restore_trade()`从交易阶段提交时间赋值，不能按本轮新定义直接使用，更不能全局重解释此字段而破坏交易/恢复逻辑。
2. **SDK实际派发、模型供应商首次请求、回合结果生成、Worker完成是不同边界。** Worker已有`execution_timing.sdk_dispatched_at/sdk_completed_at`，但Read目前只投影Job壁钟和SDK时长，丢掉绝对SDK区间。`sdk_dispatched_at`取于调用`thread.turn()`前，证明发起Agent回合，不能证明供应商第一次HTTP请求已经发出。**用户本轮已明确接受以“应用实际发出SDK回合请求”作为W3起点，位于Thread Fork之后。** 采用该可控边界，不为供应商内部首HTTP再造追踪系统。SDK的`Turn.completed_at`存在，但当前适配器未持久化。
3. **阶段committed_at在事务内部生成。** 真正生效的边界是输出事务成功COMMIT，届时READY意图才能被另一个连接读取。现有阶段、Case、意图的时间都不能准确覆盖最后一次COMMIT等待。需要补一个提交返回后的时间锚点，不能以模型结束或意图INSERT时间替代。

因此，“历史数据可以直接准确支撑全部新指标”这个判断不成立。常规成功Case已有大部分事实；原始SDK回合生成时间、提交后可见时间、失败尝试完整模型区间仍有缺口。后文给出最小补点，不猜历史，也不把已获授权的SDK请求边界称为供应商内部首HTTP时间。

## 2. 本轮勘察基线与生产证据

### 2.1 版本与操作边界

原`bus-metrics-reading`工作树已被其他会话退役，实际代码位于`C:/Users/WEIXUANXIE/Desktop/DoxAgent`。勘察开始时main和origin/main为`af5b47b48bd5e152cb214de7b20be787391e4e39`；期间其他会话将main推进至`6c5cb8b878f1c02b924acbb5ac47b89d4f3cfc18`，最终仍核对了下述所属进程文件与main一致。保留其他会话的changelog、消息源稳定性文档、测试及未跟踪文件，未切分支或合并它们。本轮方案与证据单独新增，未提交或推送。

生产API仍标记`5f0b3dea…`，Web为`be4be0fe…`，Scheduler为`f3f5a7be…`。**按代码所属进程核对实际文件**：Scheduler中的`w3_execution.py`、`round_receipt.py`和`trade_output.py`与当前main一致；codex-worker中的`sdk_runtime.py`、`jobs.py`与当前main一致。API的两个指标计算文件仍是本报告描述的旧公式。不是用API容器里未执行的Worker代码推断实际Worker版本。

证据位于`eval/metrics_optimization_audit_20261112/`：

- `inventory.json`：镜像、代码哈希、Read现有索引。
- `owners.json`：Scheduler/Worker所属文件哈希及可用库路径。
- `sample.json`：最近最多100条Case元数据中选出的6条成功REALTIME W3 Case、按主键读取的Job/阶段/轮次回执，以及5条Yahoo消息样本；进程约0.90秒。
- `precision.json`：最近200条Standard Message精度字段抽查、按主键跨库对照和查询计划；约0.18秒。
- `sdk_capability.json`：实际codex-worker安装`openai-codex 0.159.3`，Turn原始开始/完成为Unix秒，RawResponseCompleted只提供ID/usage等，不提供首请求时点。

探针使用只读SQLite连接、索引或rowid LIMIT、小字段选择、4–12秒预算；没有读取冻结研究文档、文章正文、全库或模型历史日志。时间快照不同，不声称跨库原子一致。

### 2.2 模型结束与提交已有可见差别

INTC `case_9d02926b5e774805b0272c88e8dd3ea7`，北京时间11月12日：

| 记录 | 时点 |
| --- | --- |
| Case.created_at（来自Runtime admitted_at） | 01:20:18.893775 |
| W3 Job.started_at | 01:20:38.022880 |
| SDK派发 | 01:20:38.096675 |
| 本地观察到SDK回合完成 | 01:21:15.142359 |
| Job.finished_at | 01:21:15.292253 |
| trade阶段committed_at，事务内赋值 | 01:21:15.853675 |
| 旧Case.decision_completed_at | 01:21:15.853729 |

模型回合完成到旧Case时间相差约0.711秒；后者仍早于最终COMMIT返回。此例足以证明锚点不等价，不是对全站差值的统计。本次6个成功Case均能找到SDK绝对区间；这不能证明旧历史、超时或失败后成功的全部尝试也都有完整区间。

### 2.3 新抓取延时必须先修复精度丢失

最近200条源Standard Message含193条EXACT、3条DATE、4条RELATIVE。抽查发现：

- CEG `std_live_114392a290b8efc465f879d873885b61`：源库为DATE，Read native精度字段缺失，页面MessageSummary却为EXACT，时间为`2026-11-11T12:00:00Z`。
- MU `std_live_8a5e64491e6190ce98270d4a8f6b2314`：源库为RELATIVE，同样被Read显示为EXACT。

根因是`serving_fields.select('native:standard_messages',…)`白名单未保留`publication_time_basis`，`projectors.message()`随后默认EXACT。**不先修这个字段链路，新指标就会把补造的日级时点计入真实抓取延时。** 不得根据ISO字符串带`HH:mm:ss`就判断原文有精确时间。

## 3. 消息总线统一文章抽样

### 3.1 已确认的口径

用户已确认：**24小时按首次抓取/发现时间，不按原文Publish Time。** 时间窗口使用UTC的滚动24小时，不使用交易日或自然日。

每个当前ticker的绑定source，按canonical文章去重，使用该绑定的首次发现锚点`first_discovered_at`，重复发现、正文修订、模型恢复均不刷新首次时间：

```
R = 该绑定最近最多20篇不同文章，按first_discovered_at倒序，稳定身份作并列排序
H = R中 now-24h <= first_discovered_at <= now 的文章
S = H                   若 len(H) >= 5
S = R中的最近5篇        否则
```

历史本来不足5篇就按实际数量。只读最新20篇便足以决定分支：若全天有5篇，最新20篇中一定至少有5篇；无须COUNT全天文章或向前逐日查询。

先形成共同文章集合S，再计算各指标：正文率可使用未发布文章；延时必须已有正式首次发布。不要因为某篇未发布、时间精度低或正文失败，就从更远历史补到5个/20个“成功样本”。不得变成“只找最近20篇成功发布的文章”。明确不适用正文的记录仅在正文率分母中沿用eligible语义排除，不影响先取R/S，不再向前补篇数；正文失败/等待均不能当作不适用而剔除。

同一文章多源发现继续沿用现有稳定的源归属，不在本轮另造抢功或分摊逻辑。canonical合并时维持唯一文章身份与最早发现锚点，不把历史重扫当新文章。缺失可信首次发现时间的旧记录不猜时间。

### 3.2 指标定义与顶部聚合

| 指标 | 分子/终点 | 分母/起点 | 样本 |
| --- | --- | --- | --- |
| 卡片平均端到端耗时 | 首次正式进入Message Bus的时间 | 同一新消息首次发现时间 | S内已发布且锚点有效的单条消息 |
| 卡片P95端到端耗时 | 同上，最近秩法第ceil(0.95*n)条 | 不对来源P95再求平均 | 同一端到端样本集，n=1也显示 |
| 卡片正文补全率 | S内FULL/SHORT_FULL文章数 | S内eligible文章总数，含等待与失败 | 不要求已发布；若正文结果尚缺失，不能直接从分母删掉 |
| **卡片真实抓取延时** | 首次正式进入Message Bus的时间 | 原文可信Publish Time | 从端到端样本集中再排除Publish Time不合格者，显示均值 |
| **顶部平均真实抓取延时** | 合格消息延时总和 | 合格消息数 | 同上，排除当前failed消息源 |
| 顶部平均端到端耗时 | 各源S内有效端到端耗时总和 | 有效消息数 | 为满足同范围要求，与新真实抓取延时使用同一批文章cohort与源筛选；精度门槛仅新指标独有 |
| 顶部正文补全率 | 各源S的FULL/SHORT_FULL总数 | 各源S的eligible文章总数 | 与卡片同cohort，不再混用原周期的大范围BODY查询 |

顶部均值按**单消息加权**，不是来源均值的平均。对失败源的排除使用既有`poll_status='failed'`，不新造失败判定；休眠不等于失败。partial如仍有有效消息，不擅自等同failed。将源筛选计算一次供两个顶部均值复用；卡片保留该源自身有效样本，不因失败而隐藏诊断价值。

“平均真实抓取延时”是用户指定名称，其实际范围包含原文可见至抓到、正文、过滤及发布等环节，并非单次HTTP耗时。它可能真实地达到数分钟，不应用10分钟Runtime排除规则限制它。

其他指标边界：当天新增继续用既有业务日消息桶；周期新增继续服从页面周期选择；源状态、实际刷新间隔、轮询覆盖率继续使用采集账本和逻辑轮次，不改成文章率。**文章类指标采用滚动文章样本，不再跟7天/30天选择器混名**，卡片/顶部统一简短说明“近24h最多20篇；不足5篇取最近5篇”。不增加复杂的无样本原因分类，无样本显示“—”，保留真实n即可。顶部由5项变为6项，源卡片由6项变为7项。

### 3.3 Publish Time资格与数据传递

- 原文精确到秒：使用原始秒值；精确到分钟：在已知日期、时区、小时和分钟的前提下将秒置00。
- DATE、UNKNOWN_FIRST_SEEN、RELATIVE及其他没有可靠HH:mm的时间不参与新指标；仅有日期附带的默认12:00、当前抓取时间、相对时间换算都不能冒充原文准确时点。
- 有HH但未知分钟时，同样不自行补00分。来源时区未知，不猜时区。没有秒与没有时分必须分清。
- 分钟精度要在解析源时间时保留，不能从规范化ISO结果反推。复用现有metadata精度字段，只有缺少标记的对应解析器补一个标量，不逐源开发新时间系统。
- 补上Standard→Read native→MessageSummary的basis/precision传递，取消缺省EXACT对该统计的授权。保留用于排序/业务兼容的既有日期表示，但新延时取样必须验证来源资格。
- 负时间差不填0、不取绝对值、不纳入均值，按既有日志定位源时间问题；无需新增原因面板。历史精度丢失的样本只能按选出的少量消息ID主键修复，不能把历史所有EXACT默认视为可信。

### 3.4 查询实现与索引取舍

现状`recent_business()`按近20轮时间窗口走`current_source`，该索引为`(kind,ticker,source_id,day,sort_key,id)`；delivery的sort_key优先`first_poll_due_at`，并非精确的首次发现排序。

实测：仅按sort_key全历史倒序LIMIT20，优化器选`current_page(kind,ticker,sort_key,id)`后再筛source，低频源可能扫大量其他来源；这是不能照搬的SQL。按`day DESC,sort_key DESC,id DESC`能直接走`current_source`覆盖索引，但仍按poll_due而非first_discovered排序，不能悄悄当作准确新口径。

建议采取唯一必要的索引改动：

```sql
CREATE INDEX current_delivery_discovery
ON object_current(
  ticker, source_id,
  json_extract(payload,'$.first_discovered_at') DESC, id DESC
)
WHERE kind='native:live_deliveries'
  AND json_extract(payload,'$.first_discovered_at') IS NOT NULL;
```

锚点必须统一UTC可排序字符串格式，沿用first_discovered_at已经存在的字段，不另存一份时间。若现有旧值带不同offset/精度而不满足同一排序形式，应在投影选字段时统一，并对有限旧样本显式验证，不能依靠未经验证的字符串比较。

每分钟一个dashboard请求：一次批量源ID→每源一次此索引的LIMIT20 seek→一次按选中IDs批量关联BODY和message。SQL可通过`json_each(source_ids)`加相关LIMIT子查询返回IDs，随后按当前对象主键批量关联。允许数据库内部每源一个有界seek，**禁止前端逐源请求、Python每源打开连接、逐篇取网络正文**。

返回行数上限为20×源数，内存只保留轻标量，n个样本最多20条原地排序求P95。顶部与卡片共享同一计算结果；不再并行调用旧`current_body()`与`mean_period()`重复算文章指标。保留旧函数给其他未改调用方，不在本轮顺带改Overview口径。

**索引的真实成本必须列明：** 生产Read物理文件约34.75GB；SQLite构建这个部分索引仍会遍历当前object_current，不是只读20行的免费操作。它不扫描历史表、不扩展正文大对象，但有一次性I/O和短期写入影响。不得在GET/普通启动时建索引，也不得为了本需求重建34GB整个Read库。实施时先在现有离线演练库测索引构建、大小和锁持有；放入已有显式迁移窗口，以`nice/ionice`低优先级、单线程、8–16MB页缓存和初始60秒硬预算执行（先按演练确认窗口足够），超过窗口就中止本次迁移并保留旧页面，绝不退化成在线全范围排序。若这一次性成本不可接受，应延后文章新口径发布；不能以近似poll_due排序冒充已准确完成。本轮没有执行索引构建。

## 4. Runtime延时口径与过滤

### 4.1 一轮判定延时

沿用`hot_path_contributions()`：REALTIME、已有W1/W2有效判定；W1/W2的R1/R2以及W2 R3中，最早调用开始到最晚完成的整体墙钟，排除W1 R3；并行不相加，期间重试和衔接等待保留。

新增唯一过滤：**单Case D1>600秒则该Case的秒数与样本数同时不贡献此项均值。** D1=600秒仍计入；不截成600，不改其他计数或Case状态。W1/W2中单独一次调用短于600但总区间超过600，也必须排除。API继续读桶，不在GET扫描Case。

### 4.2 二轮研判延时

```
D3 = 成功形成有效交易判定的原始SDK回合完成时间
     - 该Case第一次W3 trade_decision实际SDK请求发送时间
```

仅REALTIME，排除CLOSED、post_decision_operations以及从未形成有效交易判定的失败Case。成功判定包括NO_TRADE，不等于一定产生下单；后置运营失败不撤销已成功的交易判定。第一次失败、随后重试成功时，起点仍是第一次真正发起的trade_decision SDK请求，不能只取最后成功尝试。该起点已获用户确认，包含SDK派发后服务侧等待，但排除Fork/准备，不能标成供应商纯推理。

均值仅纳入`0 <= D3 <= 600秒`；大于600的Case仍保留真实D3供标签/明细和其他指标使用。一轮、二轮各自过滤，不因一轮超过600就顺手排除二轮，反之亦然。

**锚点实施规则：**

1. 保留旧`Case.decision_completed_at`的业务兼容语义，不改它的赋值用途。新建清晰命名的`model_decision_completed_at`，表示本轮要求的结果生成终点；再单独使用`decision_effective_at`表示正式生效。这两个字段不能互为缺失fallback。
2. Worker在既有完成回执中带出SDK原始`Turn.completed_at`及时间精度，成功解析且业务输出合法后关联到获胜的trade_decision尝试。这是Agent服务的回合完成时间，包含其内部推理和工具流程，不是Job完成、结果落盘或数据库时间。若供应商/SDK能提供更直接的合法最终输出完成事件，使用该事件；不得根据token最后更新时间猜测。
3. 当前本地`sdk_completed_at`是接到TurnCompleted通知时的时间，用于客户端模型在途区间；D3终点优先使用SDK原始Turn.completed_at，不能无标识用通知接收时间替代。原始completed_at为秒精度；保留真实精度，D3计算将起点降至同一秒精度，不将结果伪装成毫秒级测量。边界只按原生精度判断，不承诺180秒附近的亚秒区分，不拿SDK duration倒推未记录的请求时间。
4. 在现有`thread.turn()`真正发起处捕获SDK请求发送时间，保留首次发送值，重试不能重置。当前相邻采样点与Fork已经分离，必要时将其向下移动到SDK请求实际提交处；不使用Job.started_at或stage.accepted_at代替。服务内首次供应商请求不在本轮指标定义中，不新增代理或rollout扫描。
5. 文章抽样、精度修复、KPI布局、Policy相关数量可独立落地；W3新均值与长耗时标记在原始完成时间透传并通过验收后启用。缺锚点显示未记录，不保留旧跨阶段值伪装成新指标。

统计数据可以随后随已有成功Case/Effect持久化事件入Read；其计时终点仍取此前捕获的SDK原始完成时间，不取事件保存时刻。不为“内存刚生成结果就立即显示数字”另建通道。

### 4.3 长研判耗时与标记

- 使用**同一个未经600秒过滤的D3**，`D3>180秒`计一个Case；180不计，600及超过600都计。
- 同样是成功形成有效trade_decision的REALTIME Case；不能拿Operations时长、Job总时长或整个Case时长替代。未完成/失败Case不猜耗时标记。
- 新增Case派生标签`LONG_W3_DECISION`，例如放在`runtime_tags`，不能塞进`first_round_tags`，因为它不是W1/W2的一轮业务结论。
- KPI按Case去重计数，Case重投影和重试不会多加。最近处理记录新增相同标签筛选，走现有`result`筛选链路；不增加流程图节点或为每条记录另发详情请求。
- 为避免ALL时对全部Case JSON扫描，不再为标签构建第二个全表表达式索引。采用既有对象/成员投影形式：只为命中标签的Case写一个轻量`case_tag`对象（id=Case ID、parent=LONG_W3_DECISION、day/排序沿用Case），走已有`current_parent/current_day`索引；标签更新与Case投影同批写入。筛选查询先定位标签成员，再按Case主键关联，source/q条件在分页LIMIT前应用，保留已有水位/游标约束。它是筛选所需的业务成员关系，无新物理表、无后台维护任务；KPI仍从贡献桶读，不COUNT成员列表。
- “长耗时但被均值排除”是预期行为；两者阈值彼此独立。标签与KPI共用一次D3计算，不再查询一遍Job。

## 5. 非模型调用延时：可实现的公式及真实性边界

### 5.1 总体起点与终点

起点优先复用`Case.created_at`：当前`execute_message()`以`admitted_at`创建Case；Coordinator在接纳CASE任务时写admitted_at并传入，能覆盖任务排队、输入准备等Runtime阶段。它不是Message Bus发布时间，不把上游消息抓取延迟算进Runtime。演练须核对重放/恢复不会将created_at改成恢复时间。

终点为**W3交易输出事务COMMIT成功返回**：

1. `_commit_trade()`在同一事务中完成TradeOutputService输出、Policy准入/消耗、trade阶段提交、Case更新及Operations Effect入队。
2. 该事务返回后，READY意图已对`TradeOutputService.deliver()`的独立读连接可见，具备向下游执行交接的条件。不是Broker收单/成交时间，不包含后续Delivery排队、行情、券商网络，也不等待Operations。
3. `stage.committed_at`、`intent.ready_accepted_at/released_at`及旧Case时间都在事务内取值，会漏掉剩余COMMIT等待；需要在事务context退出后立即取`decision_effective_at`。
4. NO_TRADE也存在正式生效的判定，只是不产生意图；正常抑制/去重/时效拒绝是确定性出口，不伪称READY。建议一并统计这些已成功提交的判定，并记录其既有disposition，避免只统计READY而将被12分钟业务时效限制拒绝的长尾全部排除。失败未提交、CLOSED不计。页面说明“到交易判定出口提交，含不交易/不下发结论”，不能声称每个样本实际下单。

### 5.2 扣除方式

令`[A,E]`为接纳到正式出口生效区间，M是这个Case所有实际模型在途区间：W1/W2所有相关请求和重试、W3 trade_decision所有请求和重试；W1 R3若在E之前实际执行，也裁入该时间范围；post_decision_operations不参与。

```
Mi = [max(模型请求开始,A), min(模型请求/回合完成,E)]
non_model_seconds = (E-A) - length(union(全部有效Mi))
```

- 先裁剪再合并并行/相交区间，不能直接减每个模型耗时之和。W1与W2同时运行10秒只扣10秒。
- W3 Agent整个模型回合含推理、工具往返、模型生成，应整体扣除，不能只扣tokens生成或最后一次SDK时间。Thread Fork、工作区准备、写回和结果校验在模型回合外，仍计入Runtime。
- 重试的每次模型调用均扣，失败后等待/阶段衔接仍计入；不能只扣最终成功调用。SDK/HTTP内部隐藏的请求重试若无法拆开，按完整外部调用区间扣除并如实注明边界。
- **不应用600秒排除，不截尾。** Case有3小时无模型在途的Runtime等待，就应保留3小时；不能因交易结果未下发而把所有此类Case排除。
- 缺任一实际模型区间、无法判定是否已发送的孤立失败请求，或缺E时，该Case不生成该项样本；不把未知模型耗时当0。复用Coverage给出样本数/未覆盖数即可，不展开大量原因。
- 指标是“扣除模型在途区间的Runtime墙钟”，不是CPU执行时间，也不能定位与模型并行发生的Runtime阻塞。模型调用内含供应商排队/网络，无法仅靠客户端字段拆成纯思考时间；不把剩余时间未经证据全部归因于SQLite锁。
- Provider外部冷却/退避若处于模型调用区间之外，会保留在此墙钟差值中；现有专门审计可诊断其原因，本轮不另造分段KPI、不从日志估算后偷偷扣掉。

### 5.3 现有数据足够到什么程度

| 事实 | 已有能力 | 缺口与最小改动 |
| --- | --- | --- |
| Runtime接纳 | admitted_at→Case.created_at | 通常足够，验收恢复不重置 |
| W1/W2模型区间 | runtime-round-receipts成功记录provider_started_at/completed_at；turn可继承它们 | started在回执文件创建/fsync前取，completed在client.complete返回后取，含本地准备/解析；FAILED回执没有provider_completed_at。不能直接当严格模型区间 |
| W3模型区间 | Worker resource_receipt.execution_timing有SDK绝对起止；SDK telemetry有duration | Read ModelAttempt丢掉绝对区间；失败/超时路径未保证都有两端；SDK区间是客户端Agent回合，含服务/网络开销 |
| 模型原始回合生成时间 | SDK Turn.completed_at字段存在，秒精度 | 当前Worker没有持久化，不能从Job.finished_at还原 |
| W3起点 | 现有sdk_dispatched_at在Fork之后、thread.turn请求之前 | 按用户确认取SDK请求实际发送边界；不是供应商首HTTP，不必扩建遥测 |
| 交易生效 | 源交易事务、READY意图和阶段状态可证明业务事实 | 缺事务返回后的时间点，历史不能精确补 |

结论：现有材料能用于有界离线近似诊断，**不能无补点直接上线一个宣称精确排除模型耗时的非模型KPI**。不用`总耗时-sum(telemetry.sdk_duration_ms)`这种简单但错误的实现。

### 5.4 必要补点与保存方式

1. W1/W2在已有网络调用边界记录`model_request_started_at`、`model_result_received_at`，成功和错误都透传到既有RuntimeModelTurn/完成回执。采样位置移到实际调用前和原始返回事件后，先取时间再做输出验证/回执落盘；不为每个节点新建表、日志或单独计时任务。四类当前Provider适配路径共用同一小结构。
2. W3在既有Worker completion receipt/resource_receipt传递原始SDK回合时间、客户端调用区间及basis/precision；Read ModelAttempt增加一个小model_interval，而不是改掉现有Job wall timing。正常返回和sdk_completion_callback抢救回执两条路径都必须保留这些字段，不能被capsule_child重新组装execution_timing时丢掉。Fork仍留在区间外。超时/取消没有可靠完成时，不能拿任务数据库更新时间补。
3. 在W3输出事务返回后取E，**搭载既有route_to_w3 Effect完成写入**保存小timing摘要；不增加一次业务事务内写时间伪装提交后时间，也不轮询数据库等“生效”。执行路径需把该时间从`_commit_trade()`传回Effect完成处理，并投影到现有Case摘要。
4. 若事务已提交、进程却在原Effect完成回执保存前退出，业务恢复照常；E保留缺失，不能把重启/恢复时刻当原始生效时刻。无需为边缘计时再建恢复状态机。
5. Read在Case/相关attempt/Effect完成事件到达时，仅重算该Case一次：读取其小attempt列表、计算区间并集和D3、生成Case标签与sum/count贡献。合并本次incoming记录再计算，保证CDC到达顺序无关；结果不变不写。不要在Worker heartbeat或页面GET重复计算。
6. 只存必要绝对锚点及最终小摘要，不复制整个Worker resource_receipt、SDK事件、模型输出或工具轨迹。公共模板预热不分摊给每个Case；Case内真实Thread Fork/准备时间自然留在[A,E]中。

即便完成这些补点，仍应把“客户端模型在途”与“供应商纯模型计算”区分清楚。前者可实现且适合定位Runtime编排；本轮已确认采用SDK请求边界，不通过复杂代理或全量追踪追求供应商内部首HTTP。原始SDK完成时间若缺失，则该Case仍保持未记录，不能强行填数。

## 6. Runtime顶部KPI与Policy数量

布局调整后为：周期处理、一轮延时、二轮延时、NEW/OLD、Policy命中/相关数量、长研判耗时、非模型调用延时、交易执行。移除的只是顶部“二轮研判数量”和“事件发现数量”，不删除流程图、历史Case或其业务能力。

Policy两数按Case统计，不是Policy ID数：

- 命中沿用现有hit_cases对应的有效Policy结果口径；现代合约由W2提供，不用W3交易方向冒充命中。
- 相关增加related_cases，读取同一有效Policy结果的policy_related/related_policy_ids；不能用`POLICY_RELATED_EVENT`标签代替，后者还有NEW、事实对齐等条件，会漏掉单纯相关Case。
- 一个Case可以同时HIT和RELATED，两项分别计一次，不强制互斥；两数不再除以已判定Case生成比例。
- 旧合约复用现有effective_policy兼容函数，不在本轮另行重判历史。未知不当false，已观察到判定但计数为0时必须显示0。

新计数及阈值指标复用既有Metric/Coverage机制，区分“已测得0”和“没有可测样本”：related_cases在有有效Policy判定时带一个观测样本；long_w3_cases在D3可测时带一个观测样本（包括D3>600）。沿用现有`metric_id + '_samples'`观测规则即可，不能借用已被600秒过滤的均值分母判断长耗时计数是否为已知0。KPI周期、前值、比较、分钟刷新与Runtime当前页面一致；均按Case原业务日归属，恢复不会改成恢复日。

## 7. 到文件的实施顺序

| 步骤 | 位置 | 交付与边界 |
| --- | --- | --- |
| 1：契约 | `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`、schema生成源 | 增加抓取延时/样本、related_cases、long_w3_cases、non_model_mean_seconds、runtime_tags与小timing字段；兼容HTTP Resource.coverage，不再次混淆两种coverage |
| 2：源时间资格 | `message_bus_v2`对应解析器、`v2_read/serving_fields.py`、`projectors.message()` | 透传basis/precision、分钟00秒；未知不能默认精确。仅按选中的最多20篇修少量旧精度，不扫所有Standard |
| 3：文章样本 | `api_v2/bus_dashboard.py`、显式Read索引迁移 | 一个selector返回每源S，BODY/E2E/真实抓取共享；顶部移除重复大查询，查询计划必须在LIMIT前锁定source；迁移资源单独验收 |
| 4：模型边界 | `codex_worker/sdk_runtime.py`、`capsule_child.py`、`jobs.py`；`persistent_runtime_v2/transport.py`、`openai_platform.py`、`bailian_chat.py`、`vertex.py`、`round_receipt.py` | 按已确认的SDK请求边界和原始Turn完成时间透传，必要小锚点搭载既有回执，保留所有尝试；不混用Job壁钟和业务提交时间 |
| 5：生效边界 | `persistent_runtime_v2/w3_execution.py`、`service.py`的Effect完成路径 | 取实际COMMIT返回时间并搭载既有完成写入；不改变输出权限、交易时效、幂等与恢复规则 |
| 6：Read派生 | `v2_read/runtime.py`、`projectors.py`、`serving_fields.py`、`source_snapshot.py` | D1/D3过滤、未过滤长耗时数、区间并集、Policy相关计数；单Case一次reduce；snapshot/增量同函数 |
| 7：API与筛选 | `api_v2/runtime.py`、`v2_read/repository.py`及契约枚举 | KPI仍从桶读；LONG_W3_DECISION复用result列表参数与轻量case_tag成员，按现有成员索引定位后主键关联；不跨全部Case JSON扫描、不在Python加载历史 |
| 8：前端 | `frontend/v2/src/pages/message-bus.tsx`、`runtime.tsx`、Case明细/导出映射 | 源卡片7项、Bus顶部6项、Runtime替换KPI和标签/筛选；数字不挤列，不改消息流刷新/阅读位置；无复杂空值面板 |
| 9：验证/发布准备 | tests、changelog、标准组件发布流程 | 定向测试、查询计划及UI验收。实施完成先合入origin/main，再仅构建API/Projector/Scheduler/Worker/Web等实际受影响角色，按需求选择Bus解析器角色，不整体build |

新口径建议用明确的新metric键隔离，如`hot_path_le600_seconds/samples`、`w3_model_le600_seconds/samples`、`long_w3_cases`、`non_model_seconds/samples`，保留API页面字段兼容可行部分。不要混入旧跨阶段w3_seconds，也不要用旧前值算新口径环比。删除旧KPI显示不要求立即全库删除旧桶。

## 8. 数据和性能预算

| 路径 | 预算与禁止事项 |
| --- | --- |
| Bus统计GET | 60秒一次现有请求；候选≤20×绑定源数，返回轻标量；索引定位source后LIMIT；一批BODY/message关联，无全天COUNT、无逐日回溯、无source独立HTTP请求 |
| Runtime KPI GET | 一次现有批量桶归并，新计数/sum/count几项标量；不连接Worker库、不读回执文件、不扫Case计算并集 |
| Runtime投影 | 仅本次受影响Case的小attempt区间排序O(k log k)，不遍历其他ticker；完成事件触发，不随心跳触发 |
| 业务写入 | 使用既有模型回执、Turn、Effect完成写入附带少量锚点；不新建时间序列表、定时扫描器或每阶段多次写状态 |
| 索引 | 仅必要的文章发现索引；一次性构建真实成本见3.4，不能在生产高峰偷偷运行，也不能当作零开销 |
| 历史 | 不做复杂回填。新锚点缺失的旧Case没有精确D3/E/非模型指标；旧样本不伪造，已有精确事实可按选中Case主键有限恢复 |

指标上线的样本起始版本/时间以既有coverage说明即可，特别是长耗时数量没有足够历史覆盖时不能宣称“历史0例”。不为补满7天/30天再扫Worker历史、读取rollout或重跑模型。未补齐窗口不展示误导性的同比/环比。

## 9. 验收标准

### 9.1 文章抽样与抓取延时

1. 24小时恰好5篇→取5篇；6–19篇→全取；≥20篇→只取最新20；24小时0–4篇但历史≥5→取最新5；历史不足5→实际数量。
2. 原文发布时间很旧但今天第一次发现→按今天发现入cohort；BODY更新、重复抓取、canonical合并不使文章重新变新。
3. S有5篇，其中2篇发布、1篇正文失败、2篇等待→正文分母5，延时分母只用有效已发布者，不能再向前补3篇成功发布。
4. 分钟级12:34→12:34:00；DATE默认中午、RELATIVE、未知HH:mm/时区均不计真实抓取延时；已有日期级被误标EXACT的生产例证不得再入样本。
5. 两源分别1条10秒、9条100秒→顶部均值91秒；失败源排除只影响顶部合格集合，休眠源有效样本不当失败；时间倒置不填0。
6. SQL EXPLAIN确认source前缀索引先定位后LIMIT，无跨源扫描、全历史排序/COUNT。页面顶部与卡片复用selector；每分钟刷新不重置消息列表。

### 9.2 Runtime与阈值

1. D1/D3分别测试599.999、600、600.001秒：前两项进各自均值，后一项秒数和分母均排除；Case状态和其他KPI不变。
2. D3分别180、180.001、600、3600秒：长耗时计数0、1、1、1；3600不进二轮均值但保留长耗时标签和筛选结果。
3. W3第一次请求失败后重试成功，D3从第一次真正请求开始；Fork/准备不能误当首次模型请求。Operations晚数小时不影响D3。
4. 验证模型完成→本地结果验证/文件写入→输出事务等待/COMMIT的顺序；模型终点不随后两项延迟增长，non_model则必须增加对应编排/提交时间。
5. 延迟COMMIT 30秒：新decision_effective_at至少晚30秒，不能因使用stage.committed_at漏掉；这项非模型时间保留。模型完成后尚未提交时不伪称已生效。
6. 模型区间W1[0,10]、W2[0,10]、W3[15,25]，全链路[0,30]→并集20秒，非模型10秒，不能减成0。重试失败区间、W1 R3的跨终点裁剪均验证。
7. 超长但成功提交的Case仍进非模型均值；业务时效拒绝/NO_TRADE不因没有READY意图就全部丢掉。真正失败、缺完整边界的Case不算成功样本，不当0。
8. W1/W2成功与失败Provider路径、W3完成/超时/取消分别验证时间资格。缺失败尝试区间时non_model必须缺失，不从Job wall/sdk_duration倒推。
9. Policy HIT/RELATED同时为true时分别计一次；多个Policy ID仍只算一个Case；纯相关但没有POLICY_RELATED_EVENT标签的Case仍计相关数；已知0正常显示。
10. Case、attempt、Effect不同CDC先后顺序及snapshot重建结果相同，恢复重投影不重复计数；LONG_W3_DECISION标签、列表筛选、导出、KPI一致。
11. D3按已确认的SDK发送边界验收，Fork前后注入等待验证起点；不能用turn.started_at默认等于供应商首HTTP，也不能把客户端通知接收时间默认等于服务端完成时间。原生秒精度与高精度模拟锚点分别验收，不承诺源记录不具备的亚秒精度。

## 10. 交付范围与需保留的判断

本方案可直接指导实现，但不掩盖两类限制：一次性文章发现索引构建有实际I/O成本；新计时需要补齐SDK原始完成时间、失败区间及COMMIT返回锚点，历史不具备时不能准确补算。用户已确认W3采用SDK请求发送起点，故无需为供应商内部请求追踪增加复杂性。

不重定义旧业务时间、不用最后成功调用美化等待、不截断长尾、不从残差宣称纯CPU成本，是本方案的数据真实性底线。所需补点仅围绕模型调用区间与最终业务出口，不扩成逐节点遥测平台。
