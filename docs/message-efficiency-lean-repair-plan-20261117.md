# DoxAgent 消息效率：轻量修复方案

日期：2026-11-17。范围：基础／进阶去重、Google 搜索后相关性筛选、Quantum Zeitgeist（QZ）与 DCD 正文污染。本文保留方案形成时的评估与状态；后续本地开发已完成，见 [开发记录](message-efficiency-implementation-20261117.md)。**尚未部署生产镜像。**

## 1. 建议与实施状态

|方向|结论|本轮状态|
|---|---|---|
|基础去重|保留当前身份、别名、正文指纹、版本补全机制，命名上定义为基础去重|勘察完成，不重写|
|进阶去重|新增一个进程内、有界、事务外运行的模块；宽松 URL、归一标题完全相同、完整正文相似度 ≥95%，三者 OR|方案与本地原型完成，未上线|
|默认与例外|源级开关默认 true，现有／新增源自动继承；IBKR News 显式 false|待实现|
|Google|现有 `require_relevance=true` 已支持搜索召回后进入普通 L2 正则／模型筛选，无需新分发模式|9 个现有订阅全部配置完成，8 个本轮修改|
|QZ／DCD|修复站点正文边界，并覆盖候选回退、缓存复用和 304 路径|机制及实际 DOM 核实完成，待修复|

建议先修正文、完成 Google 自然流量验收，再发布进阶去重。无需建设新服务、向量库、Redis、全文索引或历史去重数据库。

有一项必须明确的取舍：**三个 OR gate 会拦截部分有事实更新、正文补充的稿件；95% 相似不等于事实相同。**仍按本轮要求设计，不额外增加“数字不同就否决”或“标题与正文必须同时相同”的条件。另一个取舍是资源上限：无法同时保证无限历史／任意长正文的穷尽比对与低延时。本文明确给出时间窗口及降级边界，避免把漏去重包装成完整覆盖。

## 2. 当前架构与适合的接入位置

生产主路径为：采集 Capture → Live article／共享 BODY → ticker 相关性判定 → `prepare_publication` → StorageLane 短写事务 → Stream → W1／W2／W3。

已核对的关键实现：

- [deduplication.py](../src/doxagent/message_bus_v2/deduplication.py)：现有 URL、provider ID、别名、完整正文指纹和版本判断。
- [repository.py](../src/doxagent/message_bus_v2/repository.py)：`publication_match`／`published_candidates`，通过既有身份索引查询有界候选，不是每次遍历历史正文。
- [live.py](../src/doxagent/message_bus_v2/live.py)：`LiveDeliveryWorker.process` 完成相关性判断，`publish` 把准备与提交分开。
- [live_repository.py](../src/doxagent/message_bus_v2/live_repository.py)：`prepare_publication` 在 BEGIN 外准备正文、Native 引用；`publish` 在事务内核验版本、绑定与竞争发布。
- [storage_lane.py](../src/doxagent/message_bus_v2/storage_lane.py)：现有单写队列、2 个准备槽、2 个读槽；复用其调度，不另起执行器或常驻扫描线程。

**进阶去重接在相关性通过之后、首次新增正式 Stream 之前。**先保留基础去重得到的“已正式发布文章更新／补正文”路径，再对仍准备新发的文章执行进阶 gate。否则同一已知文章补齐正文时可能被自己的旧标题拦截，破坏现有补全机制。只有 raw／standard 已存在、仍在 buffer 中，不等于已经正式发布，不能因此跳过最终 gate。

具体接入 `prepare_publication` 的基础身份判断之后、`raw_json/standard_json` 序列化与 Native 引用落盘之前；命中直接准备终态更新。不能先完整准备两份持久化正文再做 gate，否则虽然省下 Stream，仍产生不必要的文件写入与引用清理负担。

这个位置主要节约新增 Stream 与后续 W1／W2／W3 成本。BODY 抓取、相关性模型调用已发生，不能把这些成本也计入去重节省。第一版不再建设 BODY 前、相关性前的第二套宽松去重。

2026-11-17 13:44 UTC 只读检查：当前 **335 个启用绑定均为 immediate**。但缓冲发布代码仍存在：`service.flush_binding` → `repository.publish_buffered`。进阶检查必须同时覆盖这个最终出口，而不是只在 Live 即时路径加一个判断。

## 3. 进阶去重的业务定义

### 3.1 开关与比较范围

在 `SourceDefinition` 根级新增 `advanced_dedup_enabled: bool = True`。不要塞进各适配器的 `default_parameters`：许多源的参数 schema 是 `additionalProperties=false`，那会迫使所有适配器修改参数契约。

- 缺省字段即开启，因此历史源定义及后续新增源均覆盖；沿用现有源配置更新、版本和审计接口，不增加逐 ticker 开关。
- IBKR 初始清单和现有生产源显式设 false，启动有效值核验必须在进阶模块开始处理之前完成。基础去重继续照常工作。
- 本方案把“IBKR 暂关”定义为**既不检查 IBKR 输入，也不把 IBKR 放入进阶参照池**。否则虽然 IBKR 输入不被拦截，仍可能通过参照池影响其他源，例外并不完整。
- 在同 ticker、同 delivery mode、同语言、兼容的既有 `body_access_scope` 内跨源比较。不同 ticker 均需保留各自的首份投递；测试数据不能污染正式数据。
- 默认比较最近 **12 小时已正式发布的代表文章**，按首次 Stream 发布时间过期；重复轮询或后续看到同一篇不续期。

12 小时来自上一轮窗口回放：正文 95% 去重在 5 分钟／30 分钟／2 小时／12 小时分别可合并 8／22／33／45 条，存在相隔约 9–11 小时的转载。无限历史会持续放大缓存或 SQL 成本，也会把以后复用的标题当成旧闻。

### 3.2 三个独立 gate，任一命中拦截

“并行”指业务上三个独立条件 OR；实现按 **URL → 标题 → 正文** 短路，不为三个 gate 分配三个线程、异步任务或数据库查询。

|Gate|归一与命中规则|边界|
|---|---|---|
|宽松 URL|沿用基础规范化，再对已验证站点处理 AMP／表现层路径、可识别的文章 slug 与尾部流水号；相同发布域、语言和归一文章定位键即命中|只在已验证 URL 形态上宽松化；保留承载身份的 query ID、日期及无文本 slug 的数字文章 ID。未知形态回退基础 URL，不删除所有数字|
|归一标题完全相同|HTML 实体解码、NFKC、casefold、空白合并；Google RSS 仅去掉与已有 publisher 元数据一致的来源后缀；归一结果完全相同即命中|不做标题 95% 模糊匹配，不去数字／日期／分篇序号。空标题不建键，不用任意 `rsplit(' - ')` 截掉真实标题后半段|
|正文相似度 ≥95%|只比较已有完整性证据为 FULL／SHORT_FULL 的非空正文；实体解码、NFKC、空白合并、casefold；使用 RapidFuzz `fuzz.ratio`，未四舍五入的得分 ≥95 即命中|相似度明确为 normalized Indel，即 `2×LCS/(len(a)+len(b))`。不比较摘要、preview；不另设 400 字门槛，合格短正文也适用|

URL 规则首批只覆盖已有样本验证的几类形态，不建设一个站点规则平台。Google 包装 URL、provider 主页不能从末段或域名臆造同文关系。**宽松 URL 仍有风险**：例如移除 Investing 文章尾部流水号后，同标题 Form 4／144 可能合并不同申报；标题 gate 也存在同样问题，不能声称保留 URL ID 就彻底解决。

同一基础身份的已发布文章更新继续走原有版本／正文补全机制，不创建新 Stream；进阶模块不是另一个身份版本系统。被进阶拦截的文章不能升级为参照，避免 A≈B、B≈C 逐步把差异较大的 A、C 合并。只与实际留存代表直接比较。

## 4. 轻量实现与硬边界

### 4.1 一个有界内存索引

建议新建 `advanced_deduplication.py`，仅包含归一、匹配、过期与容量管理；由现有 Bus 发布 owner 持有。无需新数据表、索引、定时清理任务或跨进程缓存服务。

|对象|建议上限／实现|
|---|---|
|参照元数据|每 ticker 最多 1,024 条，全进程最多 10,000 条；12 小时 TTL|
|URL／标题|字典精确查找；长键使用固定长度摘要，命中后核验原归一值，避免把 hash 碰撞视为同文|
|正文存储|按归一正文摘要共享字符串与引用计数，多 ticker 只存一份；新增正文字符串预算 32 MiB，按实际字符串占用计数|
|正文长度|单篇最多 65,536 个归一字符；超长篇跳过正文 gate，URL／标题仍检查。不能截断前 64K 后当作全文相似|
|正文候选|每个比较桶维护按长度排序的轻量数组；bisect 取可能达 95% 的长度区间，再做精确比较|
|正文计算预算|每次检查 20 ms 软预算，检查相邻比较之间的耗时；超预算停止剩余正文比较并放行，保留其他两个 gate 的结果|

对于 ratio≥95%，短／长正文长度比必须至少为 `0.95/1.05≈0.90476`，可以无损排除明显不同长度。不要误写成“长度差只能 5%”，那会漏掉真实可达 95% 的候选。

20 ms 是比较循环的软预算，一次 C++ 比较无法中途抢占，实际可能多出最后一次比较耗时。需通过服务器样本确认，不能宣称绝对 20 ms 封顶。正文池满时先淘汰最旧正文特征；保留未到期的 URL／标题键。元数据达到上限再淘汰最旧代表。淘汰和 TTL 使用队列摊销，不每次全量清扫、排序、计算总大小或重建字典。

缓存只保留必要键、标准消息 ID、时间、scope 和共享正文引用，不持有完整 Raw／Standard、raw_payload 或 metadata 对象，避免隐性正文引用绕过容量计数。

上述限制是**覆盖范围与资源降级**，不是把 95% 改成更高阈值，也不增加事实否决条件。发生降级时允许漏去重，不能因此误合并或阻塞正常发布。记录 `body_skipped_oversize/budget/capacity`，让覆盖缺口可见。

不建议引入 SimHash、MinHash 或 embedding 作为第一版唯一召回器：它们不能直接保证“所有 ≥95% 必被召回”，还会增加算法、索引维护和复核成本。当前规模先使用有界长度剪枝和原生精确算法。

### 4.2 稳态不新增去重 SQL 查询

源开关从已经加载的 SourceDefinition 读取，正文从正在处理的消息读取；URL／标题／正文参照来自内存。**正常热缓存命中和未命中均不再查询历史库。**每篇归一一次，同行业文章多 ticker 投递复用正文特征，不能每个 gate／ticker 反复读 Native 正文。

冷启动或新增 ticker 时，按 ticker 延迟预热一次：

1. 用现有 `(ticker,stream_offset)` 索引倒序分页取最近 Stream 元数据，每页 64 条；到 12 小时或 1,024 个代表即止。再按 `stream_members` 主键取成员，标准消息按 ID 有界加载；先过滤 IBKR／scope／mode，再读取必要正文。
2. 复用现有 StorageLane 准备槽。预热完成前该 ticker 的进阶检查等待这次有界准备，不在每条消息上重新预热；异常时显式放行并记 degraded，采用有冷却的单次重试，不能每条重新扫描。
3. 不查询全历史，不读取所有 source／ticker 的正文，也不为保留 12 小时另建历史持久化副本。容量不足后的热路径不得重新查库补回已淘汰内容。

实际 `EXPLAIN QUERY PLAN` 已确认 Stream 查询走 `idx_mbv2_stream_ticker`、成员查询走 `sqlite_autoindex_stream_members_1`，没有全表扫描或额外排序。这里证明的是**分页元数据查询**，不代表冷启动 Native 读取耗时已经完成服务器压测。

### 4.3 事务、并发和提交结果

比较必须在 `BEGIN IMMEDIATE` 外，通过已有 `storage.prepare` 执行。不要把 RapidFuzz、正文归一、文件读取或缓存预热放入 `repository.publish` 的 SQL 事务，也不要在 asyncio 事件循环内同步跑整轮比较。线程化也不自动保证 GIL／事件循环零影响；验收需同时查看 loop lag。若服务器单次超长比较仍明显干扰调度，降低正文长度上限，第一版不为此新建进程池。

现有即时发布需要一个小调整：`publish` 目前有多条 `return None` 分支，既可能已发布，也可能因文章版本变化或取消而退出；不能据此把候选提前放进缓存。返回最小的明确结果：`PUBLISHED / UPDATED / SKIPPED / RETRY` 及实际标准消息 ID、首次发布时间。

每 ticker 只在“进阶复核 → 现有短提交 → 记入代表池”这一小段使用发布锁；BODY、模型和预热不持有该锁。缓存仅在 SQL 成功提交后更新，且将提交后记账放在同一受保护执行单元内，避免取消发生在提交与记账之间。基础重复的正文更新应刷新已有代表特征，首次发布时间不延长。

现有两个准备线程可能同时读索引；索引／共享正文引用的增删使用一把短内存锁，取好有界候选引用后立即释放，再做相似比较。锁内不跑 RapidFuzz、SQL 或文件 I/O，避免“移出数据库事务”后又引入一个全局长锁。线程只持有本次候选引用，不复制整池正文。

当前方案以单个 Bus 发布 owner 为前提。兼容生产入口必须进入同一 owner／发布协调函数；不得给 API、旧 worker 各自建一个独立缓存后宣称避免了竞争。现有硬身份去重仍在事务内兜底；进程崩溃后的参照由有界预热恢复，不建设分布式相似锁。

缓冲模式在**最终 flush 前**对候选成员执行同样检查，并对本次准备批次中先保留的成员进行局部去重；事务只提交存活成员、删除对应已处理 buffer 项，实际发布后才加入全局池。被拒绝的成员不计入编译正文／member_count，空批不生成 Stream。不把“刚入 buffer、尚未发布”的文章提前当作永久参照。当前即时绑定无需为此增加热路径步骤。

### 4.4 拦截留痕与恢复

复用既有 `live_deliveries` 的终态更新：`state=DEDUPLICATED`，原因保存为 `ADV_DUPLICATE:<gate>:<representative_standard_id>`。不生成假的 PUBLISHED、标准消息、Stream offset 或 Case，不把近似关系写进基础硬身份 aliases，也不为每次命中另写一条审计事件／大段正文日志。

状态更新必须沿用 article revision、result_ref、binding、terms 的条件核验；候选在准备与提交之间变化时复用现有重准备流程。恢复、状态统计及 BODY revision 唤醒必须识别这个终态：相同正文不能反复重试，正文真实变化时只在已有 revision 事件上重新判断一次，不再抓一次正文，不伪装成已消费。

进阶比较失败／预算耗尽时仍执行基础去重并正常发布。关闭源开关后停止检查并移除该源参照；不自动重放此前拦截记录，也不删除已经发布的业务数据。

## 5. 回放收益与误拦风险

### 5.1 本轮组合原型

使用上一轮冻结的 2,065 条已发布 article×ticker 输入，按 Stream 发布时间顺序回放；IBKR 输入和参照均排除，12 小时／每 ticker 1,024 条，只保存实际保留代表。运行 3 次结果一致：

|指标|结果|
|---|---:|
|候选拦截|110 条，占全部 2,065 条的 5.33%|
|按 URL→标题→正文短路归因|URL 21、标题 78、正文 11|
|单条本地算法耗时 P95|2.79–4.95 ms|
|P99|5.49–9.52 ms|
|最大值|27.67 ms|
|正文比较次数|全回放 13,010 次；每次检查 P95 20 个、最多 54 个候选|
|结束时参照|680 条；最多的单 ticker 153 条|
|结束时共享正文估算|360 个不同正文，Python 字符串约 3.81 MiB；不含索引、对象、分配器和原有 Native 缓存|

这是**本地算法原型，未接生产 SQL，也未开启上述 20 ms 预算和全局内存淘汰**，不能证明服务器端到端延时或最终上线节省量。导出数据未重建全部权限 scope，Google 标题按 RSS 来源后缀惯例处理；正式实现使用已有 publisher 元数据和 scope 校验，结果可能不同。窗口开始前 12 小时的代表未补入；QZ／DCD 清理后还需重新回放。三个 gate 的计数受短路顺序影响，不是各自独立的全部命中数。

复用上一轮已人工核对的边，对本轮 110 个拦截关系作保守映射：38 个已确认纯重复，6 个正文补充，1 个事实变化，7 个申报身份未确认，另 58 个尚未逐对复核。**110 不能称为 110 条真重复；1/110=0.91% 仅是已知事实变化占比，不能当整体误拦率。**

### 5.2 反例与严重性

上一轮正文 95% 的 45 次合并已完整检查：36 次纯重复（80%）、6 次补充（13.3%）、3 次事实变化（6.7%）。正文 98% 的 20 次合并仍有 2 次股价更新（10%）。因此单纯将阈值从 95% 提到 99%，不足以解决小数字变化。

|类型|已核实内容|本轮取舍|
|---|---|---|
|股价快照|MU 从盘前 4.4%／$743.08 更新为早盘 7.1%／$723.09；NBIS 8.8%／$105.70 与 7.9%／$106.70|短时行情更新有时效价值，但通常可由独立报价补充。IBKR 排除后，本轮原型的已知风险映射未再出现这两对，不能据此认为其他源没有同类风险|
|诉讼截止日|同一 D-Wave 诉讼通知的 lead-plaintiff deadline 出现 12 月 15 日与 14 日差异，相似度约 96.6%|对法律行动有实质影响；虽为 IONQ 竞品新闻，仍不能解释成无意义差异。该对仍被本轮 BODY gate 拦截|
|限定条件补充|ZutaCore BW 稿增加温度、液／风冷分配、满载及 pPUE 基准条件；另有 Intel 诉讼稿补全 alleged misstatements|不是新事件，但会改变原消息的证据完整性。本轮组合仍有 6 次此类损失，不声称已保留更丰富版本|
|同标题申报|Form 4／144 同公司同日期但不同文章流水号；本轮共 7 次未确认身份的候选拦截|潜在严重性高于普通转载，可能对应不同申报人／交易。尚未证明是不同申报，也未证明是重复；标题 OR gate 使风险真实存在|

建议仍试行本轮指定的 OR 规则，保留来源开关、代表 ID 和原输入以便定位。第一版**不新增自动挑选“最完整正文”、事实融合或数字解释模型**；这会把去重升级为内容版本合并系统，成本和错误面明显扩大。若试行出现实际丢失重要新事实，优先关闭对应源的进阶开关，再决定是否单独修改 gate 语义。

### 5.3 高频／长期放大为何需要预算

另做了本地合成压力探针：同长度、约 93.5% 相似的正文会绕过长度剪枝，又无法命中 95%。每个长度仅运行约 100 ms，再线性估算 1,024 次比较：8K／32K／64K 字符分别约 **0.27／3.69／14.35 秒**；64K 单次比较最大约 14.14 ms。估算不是完整 1,024 次实测，但已足以否定“只有 1,024 条，所以一定便宜”。

因此时间预算不可省；不要以真实样本当前候选较少为由，设计每条都穷尽 1,024 篇的无预算循环。也不要每次 SQL 取 1,024 篇、把 32 MiB 内容缓存提升到数百 MiB，或在所有 36,199 份相关性投递前先运行一次宽松去重。

## 6. Google：复用现有能力，配置已完成

当前 Google source 仍为 `by_search`。`LiveDeliveryWorker.process` 已有：

```text
Google 搜索召回 → 已有 BODY 流程 → require_relevance=true
  → 普通 L2 正则阳性：发布
  → 正则阴性：L2_DUE → Decision Model ≥0.5 发布，否则 IRRELEVANT
```

这实现用户要求的近似效果，**不改 AcquisitionMode 枚举，不把源改成 shared_distribution，不再跑一次搜索，不增加另一套分发队列**。其筛选强度与当前普通 by_distribution 相同：正则和模型仍为 OR，正则命中不会再交模型否决。因此正文污染和过宽规则必须分别修复。

2026-11-17 **21:31:09 北京时间（13:31:09 UTC）**，已通过 `MessageBusV2Service.update_binding` 对 8 个绑定更新并保存版本／审计：BE、CEG、COHR、IONQ、NBIS、ONDS、RKLB、SNOW；INTC 原本为 true，无重复写入。读回确认现有 **9/9 个 Google 订阅开启**。保留各自 search_terms、context_terms、轮询周期、发布方式及 enabled 状态。

MU 目前没有 Google 订阅，已有订阅脚本明确排除 MU；本轮不借配置相关性筛选新增订阅。这里的“全部 ticker”是全部现有 Google 订阅，MU 的历史不订阅意图继续保留。

额外成本来自原来直通、现在正则阴性的文章调用现有模型，而不是数据库写事务变长。沿用目前模型并发 2 和既有请求／轮次限制；模型不可用或条款缺失时进入现有错误／重试状态，不降级成无条件发布。另需知晓：当前 10 分钟 Decision Model 截止只用于 `BY_DISTRIBUTION`，Google 仍为 `BY_SEARCH`，**不会自动继承这个截止**；本轮不为此增加新的状态机，验收时查看 L2_DUE 和重试滞留即可。

已知基础身份、已有 raw 的文章可能直接进入原更新路径，不重复相关性分类，也不新增 Stream。这不等于搜索结果失去筛选。配置不会追溯删除此前已经发布的消息。

验证：`tests/test_google_relevance_opt_in.py` **5 passed（3.61s）**，覆盖原请求参数、opt-in 路由及模型阴性不发布。生产已确认模型开启、key 存在、9/9 配置生效；截至 13:44 UTC 的观察窗口没有新 Google article，**自然流量端到端验收仍未完成**，不以测试替代这一结论。

## 7. QZ／DCD：按发布域修复正文边界

### 7.1 机制及修复内容

|源|已证实规模|修复|
|---|---|---|
|QZ|153 条中 152 条含固定宣传尾句；删除尾句后 30/153 从正则阳性转阴性|只删除独立的固定宣传段落，标题、技术正文、Source／论文链接保留；摘要若带同一句亦清理|
|DCD|150 份投递中 139 份含 More in 推荐区；按边界切除后 17 份／9 篇失去原正则命中；已明确 6 份由其他文章推荐误放行|优先从 DOM 删除推荐模块，再抽正文；所有文本回退候选都执行同域名边界清理|

沿用 `content_enrichment/quality.py` 与 `publisher_documents.py` 的发布域处理扩展点，增加两个小的域名分支／纯函数即可。不建设新的规则配置中心，不直接改 IONQ／CEG／NBIS 的全局关键词来掩盖正文污染。

实际使用当前配置的公开读取路径（QZ direct／urllib，DCD us-standard-5／curl）于 13:39 UTC 均取到 HTTP 200：

- QZ 选中的 `article_dom` 正文 2,593 字符，尾部仍包含固定句：`See today’s quantum computing news on Quantum Zeitgeist for the latest breakthroughs in qubits, hardware, algorithms, and industry deals.` 对该独立段落精确清理，不泛删包含 quantum computing 的句子。
- DCD 的 `More in Cooling`、`More in UK & Ireland` 是 `h2.panel__title`，其上层推荐块为 `.w-block-auto_featured_content.block-auto_featured_content`，确实嵌在 `.article-body` 内。可移除**含上述 More in 标题的该类推荐模块**，不要删除整个 article-body，也不要泛删所有 h2。

推荐 DOM 定位基于完整 class token，例如：

```xpath
//*[contains(concat(' ',normalize-space(@class),' '),' w-block-auto_featured_content ')]
  [.//h2[starts-with(normalize-space(.),'More in ')]]
```

纯文本回退只在 DCD 域名内、独立段落形式的 `More in <category>` 边界处理；正文句子中的 “more information in …” 不属于边界。DOM 修复是主路径，不能全站简单按子串 `More in` 截断。

### 7.2 不遗漏候选、缓存和跨源入口

当前 `choose_candidate` 会按结构性、scope priority、长度择优。只清理最终选中正文，或只清理一个 DOM 候选都不够：未清理的长候选可能被重新选中。

必须在选择和 FULL 质量判断前清理所有同域名候选，覆盖 JSON-LD、DOM、trafilatura、reader；DCD 的 trafilatura 当前接收**原 HTML 字符串**，仅修改 lxml root 不会改变它的输入。可对这两个域名复用清理后的序列化 HTML，外加候选文本收尾；其他站点不新增序列化／重解析。

同时按已验证的最终 publisher host 作用，不只按 `source_id`：Google／Yahoo 解析到同一出版商时也要得到相同的干净正文；未验证跳转不能仅凭标题套规则。

`TechnologyBodyReader` 的 304 分支会直接返回已缓存 FULL；`ArticleBodyResults` 也可复用已有正文，单改 HTTP200 提取器会继续传播旧污染。新增幂等的 publisher 清理版本标记，在正常加载／复用时清理这两个域名的旧正文，然后再算 digest、基础正文指纹与相关性。沿用既有正文完成提交保存；相同正文／版本不重复落盘。无需全库回填，也不改变文章身份 key 或强迫重新下载。

若现有共享结果在清理后发生变化，继续通过原有 article revision／result_ref 更新和相关性重判传播，不只修改 W1 所见正文而让分类器继续看到旧内容。已发布历史不批量删消息或撤销 Case。30／17 份只是失去模板正则直通，清理后模型仍可能正确放行，不能直接把二者当作最终节省量相加。

## 8. 最小改动范围、发布与验收

### 8.1 建议改动清单

|位置|必要改动|
|---|---|
|`schema.py`、IBKR manifest、源 API 契约|一个源级 bool；既有源默认行为；IBKR 初始化 false；现有配置入口可修改|
|新 `advanced_deduplication.py`|纯归一／精确 gate、有界索引、过期和预算；不访问数据库|
|`live.py`／`live_repository.py`／现有 buffer 发布调用方|事务外检查、明确提交结果、去重终态、并发及缓冲出口一致性|
|`quality.py`／`publisher_documents.py`／正文复用接点|QZ／DCD 定向清理和幂等缓存升级|
|Google|本轮配置已完成，无新产品代码|
|`changelog`|代码实施、配置变更、测试、发布结果留痕|

SourceDefinition 是严格字段模型，新增 JSON 字段前需更新会反序列化源定义的 API／Collector／Bus 等实际消费者。源开关可复用当前编辑入口，不扩建管理页面。不要只更新 Bus 然后让旧进程因新字段无法读取 IBKR 配置；回滚到不认识该字段的旧镜像前需先兼容处理字段，优先用新开关前向回滚。

### 8.2 实施顺序

1. **Google：已配置。**观察下一批实际新文章的正则阳性、模型阳性／阴性及 Stream 结果；保留现有审计回执。
2. **正文修复。**固定已有 QZ／DCD HTML 与污染正文作为回归样本；覆盖 200／304／缓存复用和 Google 转入路径，发布后再以干净输入回放 gate。
3. **进阶去重。**更新根字段及兼容消费者，初始化 IBKR false，接上全部正式发布出口。先对同一冻结 cohort 和容量边界验证，再开启默认 true；不额外长期运行一套 shadow 服务。
4. **上线检查一个完整交易日。**查看命中、资源退化和少量命中原文，尤其同标题申报、期限和正文补充；不为去重新增诊断模型调用。若出现问题使用源开关止损，不回滚其他修复。

### 8.3 必要验收

- 语义：三个 gate 单独可命中、OR 生效；标题差一字不算 exact；正文 94.99%／95% 边界；合法短稿；同文跨源可拦但跨 ticker／权限／测试不可混；IBKR 不进进阶输入和参照。
- 可靠性：并发两篇近似稿只发一篇；取消、版本变化、写提交失败不会污染参照；重启预热仍能识别最近文章；基础补正文不被新 gate 吞掉；buffer 批次及批次内去重保持正确人数／正文／offset。
- 正文：QZ 技术正文与 Source／arXiv 链接保留；DCD 推荐列表去掉、主体小标题和正文中普通措辞保留；长回退候选不能带回污染；304 和跨源缓存也生效。
- 性能：热路径**新增历史 SQL 读取为 0**；相似计算、Native 加载和预热均不在写事务；真实服务器回放目标新增准备耗时 P95 ≤10 ms、P99 ≤20 ms，报告预算溢出及等待时间，不只报比较函数时间。门槛是待测目标，本地原型不算已通过。
- 稳态：缓存条数／字符串占用遵守上限，关闭／过期可释放；连续高频和长正文近阈值输入不会造成队列持续积压。只记录聚合计数和耗时直方图，复用已有日志／监控，不逐条另写 metrics 表。
- 成本：对比新增 Stream／Case、Google 模型调用、正文读取次数及写事务耗时；节省按实际结果报告，不将候选拦截数、正则转阴数和多 ticker 投递数混为一谈。

## 9. 证据与复现

- 前序报告：[消息效率评估](message-efficiency-assessment-20261117.md)、[宽松去重与 Newswire 标签评估](message-efficiency-dedup-and-newswire-tags-20261117.md)。Newswire tag 的额外工程不纳入本轮三个方向，以免扩张范围。
- 本轮操作：[configure_google_relevance.py](../eval/message_efficiency_plan_20261117/configure_google_relevance.py)、[配置回执](../eval/message_efficiency_plan_20261117/google-config-applied.json)、[配置读回](../eval/message_efficiency_plan_20261117/google-after.json)。更新经过现有服务校验／revision／audit，未重启服务。
- 运行结构：[索引及观察记录](../eval/message_efficiency_plan_20261117/live-after-final.json)、[实际配置路线的 BODY 边界](../eval/message_efficiency_plan_20261117/body-boundary-configured-route.json)。最初用通用 HTTP 客户端探测两站均为 403，改用现有源配置后取到 200；不得据最初探测误判生产源失效。
- 原型：[gate_prototype.py](../eval/message_efficiency_plan_20261117/gate_prototype.py)、[结果](../eval/message_efficiency_plan_20261117/gate-prototype.json)、[已复核证据映射](../eval/message_efficiency_plan_20261117/prototype-review.json)、[压力探针](../eval/message_efficiency_plan_20261117/body-stress.json)。均为离线／只读评估脚本，不是可部署去重实现。

```powershell
uv run --no-project --with rapidfuzz==3.14.6 python eval/message_efficiency_plan_20261117/gate_prototype.py
uv run --no-project --with rapidfuzz==3.14.6 python eval/message_efficiency_plan_20261117/stress_body_gate.py
.venv/Scripts/python.exe -m pytest -q tests/test_google_relevance_opt_in.py
```

原始完整正文继续留在已忽略的 `.tmp-codex/message_efficiency_followup_20261117/published_content.jsonl`，不复制到新数据库或提交完整私有输入。原型重跑依赖该冻结输入；报告与结果文件可独立阅读。
