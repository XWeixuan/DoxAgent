# Runtime → Read 投影延迟复发：根因复查与精益修复方案

日期：2026-11-12。本文时间均为北京时间（UTC+8），原始JSON使用UTC。

**本轮状态：仅排查、只读性能探测与方案；未修改生产代码、配置、业务数据或调度优先级，未重启服务、执行repair或清空gap。** 诊断原型只在另一个临时Python进程内运行，不改变正在服务的Projector。

## 1. 结论

问题真实复发，而且不限于Runtime：当前Runtime落后约80–90分钟，initialization约半小时，Bus虽能追上，但被其他源的慢事件间歇阻塞到约两分钟。

**本次主因已从上轮的重复建连、历史repair和冻结Case上下文，转移到“每次Reference/激活都重新索引整份已发布知识库，并逐对象比对写入”的计算与写放大。** Policy授权头更新还会枚举数万条历史目录。四源在同一进程串行处理，时间预算只能在一个事件结束后检查，所以一个局部重事件就能占用全局通道数十秒。

证据链如下：

1. 实际生产仍运行上轮最终镜像，六份修复文件的哈希均匹配，不能用“没有部署”解释。
2. 24份生产计时报告合计约1,808秒tick墙钟，其中map约65.8%、ingest约30.7%，repair约0.19%。单份tick累计最长约101秒。**继续压低repair预算不是本轮主修复方向。**
3. 两个真实Reference事件的只读映射分别约13.70秒、9.93秒；两者触发4次完整 `LibraryIndexer.index()`，生成数千条event/detail/fact记录，合计约45,543次SQLite execute、5,044次sqlite connect和8,790次DTO校验。
4. 40秒外部非阻塞栈采样也落在正常map/ingest，而非repair；调用链同时覆盖 `FormalProjectors.activation()` 和 `reference.project()`，两者共享 `LibraryIndexer.index()`。
5. 抽取的12条Reference原始输入，其任务自身before/after版本及内容哈希全部相同，仍重复完成全库索引。**但这不等于当天累计Delta应为NO_CHANGE**：原逻辑会以当天第一份before为基线，后续方案必须保留这个语义。
6. 一次独立只读对照，先确认库版本已在Read物化、改为批量解析出生身份后，同一Reference映射8.618秒→1.020秒，输出5,337条→1,858条；省略的3,479条canonical记录，其载荷在Read中已完全相同，保留下来的派生记录和贡献无差异。这只是可行性实验，不是已上线修复，也未验证全部事务/元数据边界。

最优方向是**已发布内容只物化一次，Reference按实际捕获内容和既有身份投影，状态变化只更新状态，Policy按受影响集合更新**。保持现有SQLite、单Projector和源内顺序；不增加并发、服务器、优先级队列或第二条读模型链路。

## 2. 现网身份与故障规模

### 2.1 实际读取的是同一个库

| 项目 | 核验结果 |
| --- | --- |
| Projector镜像 | `sha256:645f58ed807e12968b93e08a571233ccd701c880deec91eb1ee821cf17838c2c`，连续运行约13小时 |
| 应用来源 | 上轮 `fbef70f7c` 六文件覆盖；当前冻结文件均与该实现工作树内容一致 |
| Projector / API resolved Read | `/data/read/serving-v4-20261018/v2.sqlite3` |
| 两者generation | `75a54e2449a84065a0aee86dac09e353` |
| 源顺序 | research → initialization → bus → runtime；每源最多500坐标 |
| 宿主 | 8 vCPU、约31GiB内存；根盘可用约19GiB、使用率96% |

排除了API alias指向另一个Read库、Projector未切换到上轮镜像这两类常见误诊。原Desktop工作区源码仍旧，且有其他任务dirty changes；本轮以实际容器冻结源码为准，未覆盖这些本地改动。

### 2.2 连续15分钟实测

窗口 **19:21:43–19:36:43**，31个数据库点，每30秒一次；资源另有61个15秒点。最长一次数据库采样约318ms，未跨采样持有读事务。

| 源 | 起始checkpoint / head | 末次checkpoint / head | 新增 /s | 消费坐标 /s | 最老未投影事件最大年龄 |
| --- | --- | --- | ---: | ---: | ---: |
| Runtime | 4,527,686 / 4,532,416 | 4,528,336 / 4,533,120 | 0.782 | 0.722 | 5,359秒，约89.3分钟 |
| initialization | 157,294 / 157,450 | 157,325 / 157,486 | 0.040 | 0.034 | 2,249秒，约37.5分钟 |
| Bus | 11,215,567 / 11,215,608 | 11,217,608 / 11,217,631 | 2.247 | 2.267 | 98.2秒 |
| research | 52,296 / 52,296 | 52,546 / 52,548 | 0.280 | 0.278 | 54.4秒 |

Runtime距离4,730→4,784，15分钟净增54；但最老事件时间仅前进约7分钟，墙钟前进15分钟，**时间延迟还在扩大**。低速平台不是安全的“基本追平”。initialization同样消费不足。源坐标距离与事件数须分开，Bus可能包含已退役技术头跨越，不能把它的坐标吞吐都算成业务对象写入吞吐。

同窗口每点最近60个mutation commits去重后的抽样：Runtime 255条，投影年龄P95约5,307秒；initialization 34条，P95约2,222秒；Bus 120条，P95约106.8秒、P99约115.1秒。它们不是全部事件总体分位数，不能与源head/checked_at混为一谈。

19:28选最近源端不同Case作定向对照，49/49的Read状态未反映该条最新源回执，多数尚无Read Case。该样本有意取自源头部、位于当前checkpoint之后，**不是全系统Case错误率估计**；但足以验证新Case创建/终态/W3 Operations显示确实受队列阻塞影响。

### 2.3 当前负载并不需要新架构

对完整的11月12日08:00–19:00分钟桶（空分钟补0）：

| 源 | 11小时事件数 | 均值/s | 分钟速率P95 / P99 | 分钟峰值/s | 固定5分钟桶峰值/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Runtime | 24,269 | 0.613 | 1.967 / 3.300 | 6.500 | 3.057 |
| Bus | 25,730 | 0.650 | 2.267 / 5.200 | 9.200 | 4.260 |

总坐标产生速率不高，**事件类型的服务成本差异和重复操作才是关键**。早先17/12,000条Reference的样本不能代表当前：19:23的待消费范围中Reference有229条、policy_authorization_head有114条；此前已消费的5,000个Runtime坐标中，Reference就有247条。

部分较早实际落地时间桶本轮查询未返回记录，不能据此推断那几个小时停机；本报告消费增长率使用连续窗口，不用缺失的历史桶补造趋势。

## 3. 根因与延时机制

### 3.1 Reference把一次输入状态变化放大成整个库的工作量

路径：`runtime_values(reference_deliveries)` → `FormalProjectors.__call__` → `reference.project()` → 对before/after分别 `LibraryIndexer.index()` → 大数组记录 → `ReadStore.ingest()`。

`reference_capture.prepare()`保存完整before/after、rendered及任务输入；`settle()`随后仅改变status/settled_at，也会产生新的回执。即使两侧同版本、同内容，甚至已在Read完整注册，mapper仍会：

- 重新编译known index；枚举该版本所有canonical events。
- 逐event调用Native repository读取事件；逐fact读取revision/status/birth identity，反复开启连接和核验同一个版本。
- 重新构造、序列化、校验数千条DTO，再提交给ingest逐一做水位、旧值、元数据和定义处理。
- 再基于捕获的Reference快照生成reference_event/fact/changes；未变化的Reference snapshot成员也重复输出。

例：回执4,527,789的只读profile输出包含636个event、636个event_detail、3,665个fact及其他记录。该profile中4次完整index约20.27秒，DTO校验约12.31秒，Native fact_status约5.13秒；这些是有包含关系的累计时间，不能相加。cProfile有额外开销，完整生产耗时还包含写入。

现有 `library`、`document_ref` 等记录并没有在调用入口被用作“本generation已完成物化”的可验证凭据；下游虽然会比较旧值，却已经支付了几乎全部上游成本。**内容相同不产生最终变更，不等于处理成本接近0。**

此外，Reference快照ID来自完整captured内容；快照改变后，成员的parent/id可能一起改变。即使大多数fact正文相同，仍可能生成一整套成员关系。不能仅凭内容定义已经归一化，就假定成员操作没有写放大。

### 3.2 初始化激活也重复解析继承的文档和库

`FormalProjectors.activation()`每次都索引D1、D2、完整Event Library、D3，有source_document2时还可能再次处理相同D2。继承的run/artifact/version未变化时，仍先读取正文、构造页面对象和内容引用，随后才形成新activation。

本轮在线栈中约21.5%的样本落在activation→LibraryIndexer调用链，约18.3%落在Reference→LibraryIndexer调用链；其余另有两类路径和共享读取/校验。这个40秒窗口只有1,659个可归属栈样本，不能外推为全天精确占比，但与源码和只读profile相互印证。

activation的**不可变内容物化**与**当前激活指针、lineage、is_active、admission和生命周期变更**混在同一构造函数中。修复要拆清这两个责任，不能简单“已有document_ref就整段return”，否则会丢掉这次激活所需的合法动态变化。

### 3.3 Policy头更新扫描全部历史目录

`policy_authorization_head`分支调用 `rows(store,'policy_catalog',ticker)`，对全部带directions的summary重算并追加记录；`policy_direction_usage`也先读全目录再按policy_id筛选。activation lifecycle还会载入全部历史catalog，虽然之后会跳过已经RETIRED的项。

BE实测：**16,123条catalog，只有63条ACTIVE，15,948条含directions**。COHR目录19,158条、IONQ 28,725条、RKLB 23,876条。INTC/MU的目录COUNT在1秒诊断预算内未完成，不能填写猜测值。

Policy头分支实际使用的是“授权头已存在/已admitted”这一证据，不使用当前head内容去改写每个历史版本。其存在状态未变时，重复枚举所有旧版本往往没有新语义；真正的rearm/consumption有自己的事件。然而**首次admission、head删除/恢复和新激活目录**必须正确处理，不能把所有head事件一律跳掉。

只读EXPLAIN显示当前全目录查询选择了较宽的 `current_source(kind,ticker,…)`。改索引选择可减少局部成本，但即使强制 `current_identity`，枚举数万条历史payload仍是错误的工作量级；**不能把仅加索引当作根本修复**。

### 3.4 单事件预算不会中断慢事件，造成全局队首阻塞

当前时间预算是在load/map/ingest整条事件完成后才检查。满页“5秒”不是硬上限；Reference或activation耗时十几至几十秒时，这个源继续占用进程，其他三个源都无法获得下一轮。

因此形成两种叠加机制：

1. Runtime自身重任务平均服务需求超过供给，队列长期净增长，Case和结果跟随源顺序落后。
2. initialization等源的重任务让整轮变长，即使Bus自身吞吐够，仍产生周期性几十秒至两分钟的停顿。

健康检查和所谓“每分钟”性能日志也在tick结束后才更新；它们的正常输出不代表事件级延迟已受控。减少sleep或把每源预算从5改成1，均不能中断已经开始的慢mapper。

### 3.5 资源压力是放大器，不是唯一解释

15分钟资源差分：全机CPU busy均值89.35%、15秒桶P95 97.17%；CPU PSI some avg60最大56.04%。磁盘util均值40.03%、P95 54.34%，write await桶P95约1.89ms。没有证据说明磁盘设备持续满负载或swap暴涨是唯一原因。

Projector cgroup平均0.563核，块读6.30MiB/s、块写3.35MiB/s；Read GC块写约0.91MiB/s。容器计数包含本轮短时只读探针，不能完全归属服务进程；cgroup memory.current最大约2,082MiB包含缓存，不等于Python RSS。`resource_sample.py`里的单PID最初为容器init，其进程io/stat不可当Python数据使用。

根盘已到96%、约19GiB可用，低于上轮20GiB发布余量门槛。下一轮不得做全Read复制、在线VACUUM或未经评估的大表表达式索引。应首先消除重复生成和写入，保留GC的实际退出能力。

### 3.6 过期展示与真实积压相互放大

前端 `staleMessage()`仍只显示refresh_error，忽略聚合STALE。HTTP请求成功、页面重新加载，并不表示已经消费最新源事件。另一方面，Read的聚合freshness也被17,204个历史gap长期置为STALE，直接对所有页面报警会制造常驻噪声。

因此应提供**页面依赖源的消费坐标、源事件时间与延迟**，与HTTP刷新时钟分开；它只是恢复可信度的一部分，不能替代吞吐修复。当前API与Projector同库同generation，已确认的80–90分钟源差异不能归咎浏览器缓存。

19:28的Runtime consumer ACK与checkpoint均为4,527,820；此前事实同步修复仍在工作。本轮没有把持久cursor数值较旧、但对应kind没有新变化，误判成同步未完成。

## 4. 对上轮修复和验收的复盘

上轮修复的重复建连、repair、Case薄解码和冷启动事实复制有真实价值，本轮也未见repair重新成为主因。但**上轮验证范围不足**：

- 5分钟Runtime新增仅0.053/s，不能覆盖当前维护/激活混合负载。
- 400条输出对照没有证明重型Reference和activation的持续成本受控。
- 10.8MB小库写入基准不能代表当前大Read及多个索引的逐对象比对成本。
- 聚合计时缺source/table/namespace和大事件记录量，定位重事件还要再次采样。

当时“暂时追平”不能视为全链路吞吐修复验收完成。本轮方案要求按真实重任务占比验证，既看总体速率，也看单事件长尾和所有源的停顿。

## 5. 修复执行顺序：先去重和缩小范围，再决定是否需要其他改动

以下为下一阶段执行工作，本轮均未应用到生产。

### P0-A：复用已完成的库物化，Reference只做必要工作

**修改入口：** `v2_read/reference.py`、`libraries.py`、`formal.py`；复用既有Read generation、library/document_ref及索引。

1. 将“解析LibraryRef与canonical出生身份”从“生成整份库页面记录”中分离。Reference需要ref、event_key、fact_key，通常不需要重新生成所有canonical页面。
2. 对该generation已完整注册、source root/identity/ticker/version/契约均匹配的库，复用原有library ref。一次批量读取captured实际引用的出生身份，保留RECORDED provenance、版本保留与缺失检查。真正未注册的版本才走物化。
3. 完成证明必须与该版本全部成员在同一次成功发布边界内成立。现有库行只有在能确认其旧写入契约完整时才可复用；重建中途、旧格式不确定或相关成员缺失时，回退补齐，不能只检查“有一个library对象”。必要时在现有read_meta保存少量契约完成标记，不新增服务或队列。
4. Reference快照按canonical captured digest复用已经物化的event/fact成员；只有新快照或实际变化成员才生成记录。保留完整before/after引用、removed/retired身份、精确ordinal和原版本详情，不改现有API输出。
5. 同一delta_id的PREPARED→SUCCEEDED/FAILED等仅状态变化，更新输入状态、delta_day及必要动态字段。使用旧输入摘要判断内容是否相同，不再次索引两侧库或重发所有成员。新任务输入相同也保留新的任务审计，只复用不可变内容。
6. **保留当天累计基线。** `prior.before`、跨root、prepared_at先后和日边界必须沿用原规则；不得因为任务自身before==after就把当天Delta改成NO_CHANGE。

本轮一次幼稚的试验“已有library则让index返回空数组”立即导致 `KeyError: E299`：原caller还从返回数组提取identity。这证明必须同时拆分caller的身份解析，不能只给旧函数加一个早退。

随后仅在独立只读进程中实现了最小身份解析对照：回执4,527,789，已注册版本NBIS 34/35；8.618秒→1.020秒、5,337条→1,858条，3,479条省略载荷与Read当前值一致，派生与贡献无差异。该实验未校验所有元数据、水位和故障恢复，也未做生产写入，不可直接作为待发布补丁。

**回归门槛：** 日内累计变化、同版本不同captured摘要、状态单独更新、跨日/跨root、首日空V0、缺birth identity、MERGED/SUPPRESSED、历史Subtype、旧gap修复、同seq重放、有效旧view/cursor。比较对象字段与metadata、贡献、指针、水位及历史前像，不只比记录数量。

### P0-B：激活只物化新的内容，动态关系继续完整提交

**修改入口：** `formal.activation()`、`ArtifactIndexer.index()`、`LibraryIndexer.index()`。

1. 对相同发布run/artifact SHA、库版本，复用该generation内已验证的不可变页面、正文引用与library成员。一个activation内相同D2只解析一次。
2. 每次仍核验实际发布态、ticker与所引用版本，并处理lineage、admission、active pointer、is_active、Policy/Event lifecycle。不可把“正文没变化”和“本次激活无业务变化”等同。
3. 对真正的新库版本，复用同一个只读连接完成该版本读取；只做一次版本存在预检。批量取得event/fact revision、state、birth mapping，消除每fact重复新连接和重复版本查询。
4. 相同规范化内容哈希只校验一次，复用已验证定义；新内容照常校验。不要全局关闭DTO校验，也不要先引入新的验证框架。
5. 仅在上述精简之后，若真实新版本仍出现>5秒不可中断事件，才设计小型可续跑准备步骤：沿用原源坐标与Read元数据记录进度，active pointer在完整准备后发布。此分支必须证明旧view无半成品、崩溃可重放、无gap假成功；不默认新增后台队列或第二个writer。

### P0-C：Policy按受影响集合更新

**修改入口：** `projectors.py`中的授权头/usage分支及 `lifecycle.activate()`。

1. head已存在且其“是否已admitted”证据未改变时，不再枚举历史catalog；实际rearm/consumption继续由自己的事件更新。首次出现、删除/恢复、新激活目录必须重算真实依赖。
2. 单个policy_direction_usage只定位该policy的相关summary，而不是先加载ticker全部目录。直接使用已有业务键、当前activation的policy_detail与旧activation集合。
3. lifecycle仅比较新入选集合与上次ACTIVE集合；已经RETIRED且本次不受影响的数万项不读取、不反序列化。
4. 保留旧版本策略详情、消费代次、重授权资格和retired有效性；不要通过删除历史catalog或只保留当前版本来提速。
5. 对已缩小的查询用EXPLAIN核对索引，优先 `current_identity/current_parent`。确实仍缺定位时才增加小而明确的定位索引，先测空间和迁移窗口；不对96%根盘直接建设大型表达式索引。

**回归门槛：** first admission、head不变、新revision同一Policy、head删除/恢复、rearm、历史scope隔离、乱序repair、水位保护、旧view和Policy统计一致。新旧ticker状态不得互相污染。

### P1：消除剩余逐对象开销并使长尾可测

先完成P0，再根据真实计时决定P1范围；不把本轮所有可疑点一口气重写。

- immutable物化确认后，不应再把成千上万条旧canonical记录交给ingest做水位UPSERT、定义归一化和旧payload比较。
- 对仍有必要的大批记录，先预选实际变化集合，纯计算移出写锁；可用同事务批量读取/写入减少Python↔SQLite往返。保持当前单事件业务原子性，不直接在四源外套大事务。
- 新增source/table/namespace、records/changed/skipped计数、单事件load/map/ingest总时间、慢事件seq和写锁等待/COMMIT时间。每分钟聚合，>1秒事件最多每分钟5条匿名化日志；不打印正文/账户。
- 日志应说明实际覆盖时长；不要把tick结束后输出的“60秒报告”当固定周期计量。健康信息区分已观察head时间、已投影源事件时间和last commit时钟。
- 只有重事件本身减轻后，才试验同等短时间片轮转；不通过提前跳到Case/交易来掩盖吞吐不足。单改5秒预算无法解决原子慢mapper。

### P2：补源级新鲜度提示，独立恢复历史完整性

Runtime/Case页面显示Runtime消费进度及最老待投影事件年龄，库/Policy/Reference页按相应依赖源提示；HTTP刷新成功与事实截至时间分开。维持既有snapshot view契约，不强行读取Runtime源表绕过Read。

17,204个历史gap在本轮没有增长：ValueError 17,138、PROVENANCE_UNVERIFIED 66。它们需要原坐标/依赖/归属证据的专项恢复；hot未返回不代表证据丢失。不能清掉它们换FRESH，也不能因这些历史项尚未分类就长期搁置当前吞吐修复。对外必须分别报告当前消费延迟、有效业务缺口和已核验非Serving事实。

## 6. 验证、发布与量化验收

### 6.1 建立正确的回放负载

使用本轮真实Reference、initialization activation、Policy head、普通Case/Turn、Bus共同组成连续工作集。保留实际顺序、重复状态更新和已注册/未注册版本比例，并加入BE的16k目录/63 ACTIVE规模。

至少覆盖：同版本同摘要多次状态变化、不同task相同摘要、同version不同Reference捕获、跨日累计基线、两侧版本不同、真正新库/新D3、多个source同时有工作、旧gap重试与GC并行。不能再只用普通Case或空映射事件证明容量。

先在隔离fixture和脱敏依赖子集上做旧/新输出对照；容量试验限定一个worker、无新增并发，与真实服务器资源上限一致。临时写入≤256MiB，不做全35GB Read复制。小库相对收益和生产大库结果明确分开。

### 6.2 验收合同

| 维度 | 标准 |
| --- | --- |
| 语义 | 页面对象及metadata、watermark、贡献/指标、日基线、激活指针、内容哈希一致；没有新正常业务gap；不越过未提交事实 |
| 已注册库Reference | 常态map+ingest P95≤250ms；纯状态变化P95≤50ms；不生成全库canonical记录，连接次数不随全库fact数增长 |
| 授权头/usage | unchanged head处理P95≤50ms；单Policy处理量与该Policy相关集合成比例，不与全部历史目录成比例 |
| Genuine cold activation | P95≤2s、单事件通常≤5s；超过5s必须给出实际阶段耗时和完成前不可见的准备策略，不能任其阻塞四源数十秒 |
| 混合持续容量 | 真实重任务比例下，Runtime≥12坐标/s、四源合计≥35/s，连续30分钟，分别披露mapped/verified skip/实际mutation；不靠新增业务跳读达标 |
| 当前积压消退 | 以4,784条、持续输入0.782/s计算，30分钟消退至少要求μ≥3.44/s；目标仍为≥12/s，并对实际mix进行验证。9,589条积压和持续6.5/s输入下，μ=12/s理论约29.1分钟；实际≤30分钟 |
| 自然延迟 | 各源新事件可读延迟P95≤10s、P99≤30s；Bus不能再因库物化周期性停顿一分钟。普通最老事件≤60s，真实新发布突发≤120s且5分钟内恢复 |
| 资源 | 不增加worker或业务并发；Projector同负载CPU平均≤1核、RSS常态≤1GiB，缓存合计有≤32MiB硬上限；Projector+Read GC常态块写平均≤5MiB/s、追赶≤10MiB/s；无持续swap增长或数据库锁等待放大 |
| 全机 | 同负载CPU平均≤80%、CPU PSI some avg60目标≤20%；IO PSI some avg60≤5%。其他角色负载导致超限时标明归属，至少证明本次不新增竞争，不擅自减业务配额 |
| 稳定性 | 首次30分钟含真实重任务；再覆盖完整主要业务时段及24/72小时。7日复核Read/Native/根盘增长，而不是只看一次RSS或head追平 |

μ与坐标计数仅在对应区间无坐标空洞/退役跨越时用于条数模型；每次报告必须同时列真实回执数和输出工作量。资源和延迟门槛共同通过，不能用更多CPU换追平后宣布成功。

### 6.3 轻量发布顺序

1. 先冻结当前镜像和六文件哈希，补充本轮重任务用例；在独立工作树实现P0-A/B和P0-C，分别保留可审查diff和changelog。
2. 两类改动先独立输出对照，再合并真实混合回放；修复首批应主要限于Projector及相应测试，不先调整上游维护频率或业务优先级。
3. 用既有canonical Projector单角色覆盖发布，兼容原Read/schema与历史回执；不重置generation/checkpoint，不补跑模型或交易。
4. 发布后记录每5分钟的四源λ/μ/B、最老事件时间、重任务数/记录量和资源。若μ≤λ或单事件仍>5秒，继续定位实际热点，不能靠再观察一晚结案。
5. 如果确实需要P0-B的可续跑准备分支或小索引，单独给出事务边界、空间开销和回退验证；只在前述简单精简后仍不达标时采用。

发生新语义差异、gap增长、旧view失效、有效策略代次改变或checkpoint跨未处理事实，立即回退该代码候选；保留源业务数据库和消费事实，不回滚成旧业务状态。CPU/IO持续恶化、盘余接近15GiB时停止试写/构建，先按现有生命周期核验明确可退出的临时产物；不执行全机prune或未知业务数据清理。

## 7. 本轮证据及边界

目录：`eval/runtime_read_projection_followup_20261112/`。

| 文件 | 用途 |
| --- | --- |
| `inventory.json`、`production-sources.json`、`production/` | 实际镜像、角色、资源及20份所属源码冻结 |
| `alias-check.json` | API与Projector同库同generation |
| `samples.jsonl`、`resources.jsonl`、`summary.json`、`resource-summary.json` | 连续15分钟与复算；统计不跨样本持有事务 |
| `performance.log`、`performance-summary.json` | 实际阶段时间；map/ingest而非repair主导 |
| `live-profile.raw`、`profile-summary.json` | 40秒49Hz非阻塞外部栈采样；是墙钟样本，不是纯CPU比例 |
| `map-probe.json`、`header-mix.json` | 真实重事件展开量、调用次数、队列类型分布 |
| `reference-probe.json`、`reuse-experiment.json` | 捕获摘要与只读对照；复用实验未修改服务进程、未调用ingest |
| `catalog-probe.json`、`metadata-checks.json` | 大历史目录/小ACTIVE集合、查询计划、ACK/cursor检查 |
| `latest-case-check.json` | 源头部49个Case与当前Read的定向差异 |

只读mapper实验禁用文件发布与内容登记，部分查询有1–3秒预算；其中 `OperationalError: interrupted` 是探针取消，不是生产gap。实验不会计入生产“修复成功”。cProfile有测量开销，容器资源统计也包含短时只读探针。

本轮没有给未知事件统一判无效，没有改业务维护节奏，没有设置自动任务。临时外部采样器仅位于指定 `/tmp` 目录，取证后按哈希核验清理。完整实施、发布及长期验收均待下一阶段授权执行。
