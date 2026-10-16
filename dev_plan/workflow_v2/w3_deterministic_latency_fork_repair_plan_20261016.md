# W3 确定性开销压缩与 Fork 恢复统一修复方案

日期：2026-10-16。目标：将正常已准入W3的**非SDK交易turn总耗时p90压到5秒以内**，p95尽量在8秒以内；不通过排除冷路径、失败、重试或挪动起点美化指标。本轮已完成本地边界探针、生产同版本隔离测试和根因核验；下文业务修复为待实施方案，未发布生产、未恢复历史Case。

本方案只采用《W2超时与W3 fork修复方案》的W3根因证据，不继承其实现方案；不修改W2。本轮也不更改交易模型/effort、业务AGENTS/Skill、交易判断schema、价格研究规则或交易授权边界。

## 1. 本轮补充的证据

证据目录：`eval/w3_deterministic_20261016/`。生产版本仍为main-e929134d scheduler、main-618fd9c5 Worker；本次探针通过临时源码overlay测试，生产程序未替换。生产SQLite仅只读；资产PUT测试写入独立`w3-boundary-probe-*`工作区，身份映射写入临时SQLite；SDK实验使用独立CODEX_HOME及复制的Template证据，没有运行模型turn、调用券商或生成生产Case。

### 1.1 冷进程成本

在真实Worker容器中，以独立Python进程串行执行三轮：

| 测量 | 结果 | 含义 |
|---|---|---|
| `sdk_runtime`模块导入 | 7.68 /8.30 /8.20秒 | 不含模型，不含随后MCP启动 |
| `OpenAICodexRuntime()`构造 | 4.43 /4.62 /4.84秒 | 包含全量registry/contract构建 |
| 单独全量tools.factory导入 | 3.02 /2.95 /3.87秒 | 是上述导入的组成部分，不能重复相加 |
| 单独全量registry构造 | 4.23 /3.70 /7.31秒 | 表明初始化大量无关provider有独立成本 |
| 单独IBKR历史工具模块导入 | 3.97 /3.62 /3.63秒 | 轻量路径仍有Python/Pydantic冷导入，不能假定零成本 |
| 单独IBKR client构造 | 0.47 /0.14 /0.14秒 | 构造不连接券商 |
| 官方Codex SDK单独导入 | 1.56 /1.58 /1.86秒 | 不含App Server启动 |
| Data MCP server模块导入 | 5.07 /4.90 /6.95秒 | 不含随后应用registry构造和MCP握手 |

这些独立进程测量有重叠依赖，不能全部相加形成一个虚构的冷启动数字。但已证明固定成本相当大；只调HTTP超时、只过滤已导入的工具名单无法解决。

进一步对生产同版本全量registry做构造期cProfile（没有网络调用）：总耗时5.11秒，注册128个工具，构造67个provider/HTTPX客户端；其中67次SSL context创建耗时4.37秒，仅`SSLContext.load_verify_locations`就占4.28秒。**初始化成本主要来自重复建客户端/加载CA，并非此时调用了券商或远端工具。**因此W3需要在构造前选择窄factory，不能只裁剪最后暴露的工具目录。证据`registry_profile.json`；这是单次带profiling的构造诊断，不替代三轮启动分布。

### 1.2 上游准备与文件下发

用四个真实版本固定Case运行当前`PublishedW3ContextProvider.load`和`derive_research_assets`；首次冷cache及第二次热cache分别记录。数据仓库使用只读适配器，保留业务解析/校验；四例PublishedDocument正文均在本地，没有发生远端对象存储下载。

| ticker/样本 | 冷context.load | 热context.load | 冷/热derive | 研究文件数/总大小 |
|---|---:|---:|---:|---:|
| INTC工厂爆炸 | 0.87秒 | 0.14秒 | 0.086 /0.025秒 | 211 /4.90MB |
| RKLB Neutron | 1.01秒 | 0.077秒 | 0.157 /0.072秒 | 176 /3.86MB |
| MU FeNAND | 2.33秒 | 0.17秒 | 0.276 /0.169秒 | 312 /7.94MB |
| BE飞机行业报告 | 0.52秒 | 0.049秒 | 0.079 /0.023秒 | 160 /4.27MB |

独立工作区真实HTTP逐文件PUT：INTC 211次、2.26秒；RKLB 176次、2.78秒。第二轮分别2.14秒和1.13秒。并非每次下发都耗几十秒，但它们完全可免除，且跨Case重复上传数MB、数百次RPC没有业务价值。读正文校验本身只需几至几十毫秒，context冷路径还包含Pydantic解析、完整Reference View编译和深拷贝；热路径也不是单次dict读取。

进一步在同一真实数据卷测试私有本地复制：211/176个文件分别182/134毫秒；**每个文件都fsync后也只有346/325毫秒**。因此最终选择普通本地私有复制，保持原有隔离和耐久性即可达标，不为再省一两百毫秒引入共享inode或新的挂载机制。证据`local_copy.json`。

### 1.3 额外定位的每Case全历史扫描

`W3Execution._thread()`在选择Thread前，读取该ticker**所有历史Event subject**，调用`resolve_event_subjects`逐个访问Event Library、查询lineage并通过写事务补关联。它没有限定为当前消息，也没有排除已删除/过期Thread。`maintenance.py`发布O2时已经调用`sync_event_links`，热路径又做一遍。

复制当前关联表到临时SQLite，用真实版本固定Event Library执行原函数，测得：

| ticker | 当前不同subject数 | 原函数耗时 |
|---|---:|---:|
| INTC | 546 | 2.49–3.04秒 |
| RKLB | 21 | 0.70–0.71秒 |
| MU | 229 | 2.00–2.52秒 |
| BE | 124 | 1.20–1.62秒 |

这是今天的关联集合对历史pin的受控重放，不是声称历史Case当时恰好花了这个数值。复制环境未模拟生产写锁竞争；原代码在生产反复取得写锁，多个Case可能进一步互相放大。因此应**移除每Case全历史同步**，而非给它多分配线程。

### 1.4 历史意图和Fork

- 四个历史会员为空的样本整理约0.1–2.6毫秒；另取两条实际有2个同ticker历史意图的RKLB样本，原`history_payload`为70.4、133.9毫秒。当前量级没有证据支持为历史意图新建复杂缓存系统。
- 官方SDK已提供公开`AsyncCodex.thread_fork`。在独立CODEX_HOME、已有Template证据、同一个SDK实例中，三次Fork为381、154、130毫秒；随后同实例resume为15–18毫秒。该测试未开启Data MCP、未调用模型，不代表完整生产首次执行也是这个耗时。
- 一个完全没有持久化turn的空Thread不能直接作为可Fork母Thread，SDK返回`no rollout found`。因此不采用“空Thread当模板、永久取消模板预热”的捷径。
- 受控故障注入复现：一个阻塞的`thread/read`让其他Fork等待共享锁；调用方超时后Worker Fork继续，最终COMPLETED，但模型jobs仍为0。探针分别记录锁等待和RPC执行，重试同key能恢复同一child。
- 重新只读核验38条历史Case：仍全部Fork COMPLETED、无模型jobs。额外检查了jobs.payload中的thread_id，避免ACK清空request后误判为“没有job”。证据`orphans_verified.json`。

### 1.5 对原大区间的修正认识

原来`stage接受→最后Worker创建`的78秒p90不能全算成资产加载。它包含全历史关联同步、首次Template模型调用、Fork全局锁/生命周期启动、逐文件下发、上下文/历史和既往失败恢复。正常context/文件操作各为亚秒至几秒；最极端长尾是生命周期无界等待。优先级应由这些已分解的事实决定。

`Effect创建→stage接受`原20例p90为3.1秒。源码核验发现，`service`默认在`save_case_with_effects`返回后已立即提交dispatch，并非所有消息都等1秒轮询；这条提交与`projection_outbox.flush`共用2线程executor，周期补偿才从coordinator.tick启动。`tick`又先执行reconcile，且非W3 Effect在claim批次内同步执行。因此存在executor排队、批次内阻塞、claim数据库锁及轮询补偿等候选等待。历史记录无法把这3.1秒逐一归因；本轮补上claim起止、批次大小、dispatch时间及线程启动延迟，生产发布后才能定量分配剩余等待。不能把“缺少立即唤醒”当作已确认根因。

## 2. 已落地的轻量探针与使用边界

新增`src/doxagent/w3_timing.py`，使用本进程单调时钟和ContextVar传递trace，记录父子span、UTC起点、耗时、计数/字节、cache hit、错误类型，不保存正文、密钥或隐藏推理。

已接入：

- service的W3 Effect执行：记录原创建/available时间、attempt/lease以及执行起点；异步dispatch另记录claim起点/耗时/批次大小、dispatch时间、线程启动延迟，区分claim前等待、claim数据库成本及执行入口。receipt按Effect+lease保存于`w3_boundary_timing`；不会用最后一次成功覆盖此前尝试。
- Stage.begin/accepted、context准备与PublishedDocument读取、缓存命中、研究派生、全历史subject解析、历史会员/载荷、case载荷、Thread准备、Fork HTTP、研究文件PUT及各phase的Worker roundtrip。
- journal连接、BEGIN等待与commit。高频SQL/文件span仅保存少量样例，累计次数/耗时/字节完整，不让详细事件截断丢掉主要边界。
- Worker队列至_run、ensure_attempt、启动信号量等待、runtime.start；仍保留原cold_start_ms和queue_wait_ms的历史口径，新的`startup_timing`嵌入resource_receipt。
- Capsule启动、schema/SDK导入、Runtime构造、SDK thread_start/resume/turn_start。boot耗时带`reused`，热复用时不能再把第一次boot成本计入本轮。
- 生命周期Fork/status/delete的锁等待、client start/initialize、RPC、close；`boundary-timing/*.progress.json`保存进行中的span，故障卡住时仍能知道停在哪里，完成receipt另存。读诊断文件若正写入，应作为partial，不能用于业务状态决策。

探针写入失败不改变业务结果、不发起模型重试。Effect诊断写入单独使用10毫秒SQLite busy预算，争锁时回退到小文件，避免继承业务30秒锁等待而让探针自身变成耗时来源；已做持写锁故障测试。Worker的轻量receipt在独立`boundary-timing/`，ACK不会删除；后续纳入已有运维留存清理，建议14天，不新建Tracing服务。本轮没有执行生产清理。

**本地源码及隔离实验已覆盖这些边界，生产尚未发布探针。**当前证据不足以精确追认历史最先卡住的是哪个具体生命周期RPC；不把读/status或delete其中一种猜测写成确定根因。首次发布探针后需用自然流量补足这一点，但不必等新的事故才修复已证实的全局放大与恢复缺口。

## 3. 最终架构选择

不以“把现有每Case重建流程尽量调快”为目标。拆成三个稳定层和一个短热路径：

1. **公共指令Template：**只随ticker、AGENTS/唯一Skill、模型profile与能力合同变化。研究版本变化不再强制重新跑Template模型。
2. **版本化研究资产Pack：**D1/D2/D3按出版身份/内容摘要派生一次，Worker持久化一次；Reference View和身份映射另按O2版本准备。
3. **Worker可执行准备：**少量已启动但未绑定Case的Capsule，复用既有资源治理；取得任务后，由同一SDK/Capsule完成Fork和首轮执行，取消额外生命周期进程与writer交接。
4. **Case短热路径：**取得当前固定资产句柄、冻结必要动态状态、解析当前消息少量关联、接入持久化prepare operation、提交现有模型job。正常路径不扫描全历史、不重新编译研究、不逐文件下发、不临时初始化公共Template。

这三层使用现有journal、Worker JobStore、Capsule与Thread注册表，不新增任务引擎、向量库、通用workflow框架或行情服务。没有合适热Capsule时允许按现有治理弹性冷启动，不能额外等待某个仍在跑模型的进程释放；冷回退必须纳入总时效统计。

## 4. 可直接实施的工作包

### A. 移走全历史同步，先去掉确定的秒级浪费

文件：`w3_execution.py`、`w3_context.py`、`maintenance.py`、`w3_threads.py`。

1. 删除`_thread()`开头“SELECT DISTINCT全ticker subjects→逐个resolve”的热路径。
2. O2发布已有`sync_event_links`改为按**活跃、未过期、仍可复用的Case Thread**批量读取关联，并一次写入解析结果；不扫描DELETED历史Thread，不为每个已存在alias反复BEGIN/COMMIT。
3. 同步标识使用`(library_lineage, ticker, published_version, mapping_format_version)`，成功后记watermark；发布/恢复时补齐一次。不要用“今天已经同步过”替代版本身份。
4. 当前Case仅解析自己的Event keys，以及首次构造history_matches确实需要的少数keys；同一个Case里解析结果直接复用，不在thread选择、labels和payload各调用一遍。
5. 旧pin按旧版解析，不能用最新映射把历史消息换成当时不可见对象。原subject/alias只追加兼容，不删除旧身份；历史线程仍依原TTL/关联规则复用。
6. 发布期间映射尚未准备时，单次补齐该版本或查当前消息的直接映射；不恢复成“每Case同步所有历史”的兜底。

验收：相同消息/Policy/Event仍选择相同可复用Thread；原子Fact转canonical和redirect正确；旧版本、不同lineage不串身份；健康热路径解析数与当前消息/相关历史规模相关，而不是与累计Thread历史规模相关。

### B. 研究Pack按版本准备一次，Case只做绑定

新增窄模块`persistent_runtime_v2/w3_assets.py`，扩展`w3_research_assets.py`、`w3.py`、Worker workspace API/client和`workspace_store.py`；在`execution_bundle.py`、O2/O3出版/激活现有节点挂准备通知。

**身份与失效：**

| 层 | 身份输入 | 变化时动作 |
|---|---|---|
| D1/D2/D3研究Pack | ticker、各出版artifact sha、D3原始版本/来源映射摘要、derivation版本 | 派生并安装一个新Pack |
| Reference Pack | library lineage、ticker、Event Library version、view格式版本 | 编译/保存新Reference View及Event IDs |
| 公共Template | ticker、业务指令hash、执行model/effort/provider、能力合同版本、模板格式版本 | 预热新母Thread |
| Case overlay | 消息、W1/W2、case cutoff、provisional、dynamic状态 | 每Case生成，固定至该次阶段 |

实现步骤：

1. 出版/激活通知进入现有后台执行器，读取完整固定版本，调用现有派生一次。相同版本并发准备采用single-flight；持久化manifest/完成标记使重启能复用磁盘成果。禁止在每Case先序列化全文再算同一个asset hash。
2. Context热cache按这些出版身份保存不可变base。Case只浅复制外壳并附加本Case overlay，清除V3路径上的无效整树deepcopy；原始Pydantic对象在W3消费路径禁止原地修改，用并发测试保证。legacy V2的消费过滤只复制需要变化的Policy部分，不能污染base。
3. Worker增加`ensure research pack`和`attach pack to workspace`两个内部操作。Pack完整性在安装时按manifest检查一次、完成后原子发布；Case绑定只验证已发布Pack身份，不逐文件重算全量hash。
4. Worker把已安装Pack本地复制到原来的`context/research/<asset_key>/...`，保持每文件fsync和Case私有副本；Reference View同理复用固定底座的预编译产物。本Case provisional保留独立overlay，校验Event可引用集合仍按底座+本Case可见provisional构造，固定版本的Event ID集合可随Reference Pack缓存。
5. **不使用symlink或共享hardlink，不放宽现有workspace路径检查。**当前WorkspaceStore拒绝symlink/越界路径；生产容器中的SDK又可能使用full-access，不能靠提示词声明来保证任何in-place写入不会污染共享inode。已有私有本地复制实测不足0.35秒，选它既更简单也足够快。
6. 相同目标已绑定同一Pack时立即返回；冲突身份明确报错，不覆盖其他Case研究。共享Pack只由Worker安装器写入，Agent和业务API不修改；Case工作文件保持私有，不回退成数百次HTTP PUT。
7. AGENTS/Skill按公共版本一次安装、Case按当前冻结hash绑定。task、schema、Reference overlay和动态状态走一次小型`materialize case`请求，沿用原文件路径和不可变规则；请求中不再次携带所有研究全文。
8. 出版版本完成即主动准备，旧Case继续按原pin读旧Pack，新Case用新Pack。新Pack未准备的异常恢复路径可以按同版本ensure一次，显式记录cold；不能为快而借用旧研究。

**不做的缓存：**72h会员窗口和执行状态不永久冻结。现有会员在Case首次stage接受时冻结；PIN/WATCH保留该阶段快照，运营阶段仍刷新。历史消息正文/已完成决策可按intent身份与业务版本复用，但这轮实测70–134毫秒，先保留现有实现，最多避免为了取两个匹配字段而对整个历史Case做model_dump。policy_usage与最终授权仍按现有冻结/提交边界处理，不随研究Pack缓存。新增意图、成交回执、WATCH取消或时间窗口移动不必重建整个研究Pack。

### C. Template与研究解绑，消除消息临时触发的模板模型调用

文件：`w3_execution.py`、`w3.py`、`w3_threads.py`、`execution_bundle.py`。

1. 新模板格式`instructions-only-v1`，母Thread仅初始化AGENTS+唯一Skill和阶段合同。不给母Thread内联完整research_index，不让其读取具体消息或研究全文。
2. key不再包括research asset_key/O2/O3版本。模型profile和能力合同仍在key内，Luna/max和Sol/medium不能混用。
3. 本Case的当前research_path、Reference View、已召回完整Policy及必要元数据仍明确给出；全量研究继续可按文件索引读取。业务提示词文本不改，改变的是准备层的数据放置方式。
4. Worker启动和执行bundle激活时预热当前活跃ticker实际需要的公共模板；研究出版只准备Pack，不再调用模板模型。公共指令或profile变更才预热新母Thread。
5. 新模板key及Case Thread的compatibility_key都加入`instructions-only-v1`/资产绑定合同版本，与现有含旧研究的母Thread及派生Case Thread分开；仅改模板key不足以阻止先被acquire选中旧Case Thread。不原地改旧母Thread；已有明确record/operation的历史恢复继续接回原Thread，保留原冻结语义。
6. 冷启动缺模板时记录为准备状态，由已持久化的公共模板准备接续；不让每个Case各跑一轮相同初始化，也不因为准备中消耗通用三次失败预算。母Thread仍需要一次真实初始化turn；前述空Thread实验明确不支持零turn伪模板。
7. instructions-only Template初始化关闭Web Search与Data MCP：这一阶段只加载本地指令，无需券商工具、全量registry或MCP进程。trade准备/执行阶段显式恢复其既定工具权限和当次scope；不将模板的禁用能力错误继承为交易无工具。

### D. Fork与首轮运行由同一个Capsule承接，补齐durable operation

主要文件：`codex_worker/thread_lifecycle.py`、`capsules.py`、`capsule_child.py`、`sdk_runtime.py`、`jobs.py`、`job_store.py`、`app.py`；调度端`codex_runtime/client.py`、`w3_execution.py`、`w3_threads.py`、`worker_receipts.py`。

**正常路径：**

```text
Scheduler持久化Case record与prepare身份
  → Worker登记operation，立即回执已接受
  → attach已安装资产 + 取得已预热/弹性启动的Capsule
  → Capsule用自己的AsyncCodex.thread_fork创建child
  → Worker持久化child/registry/operation READY
  → Scheduler按同operation取回child，绑定原record
  → 提交现有WorkerRunRequest
  → 同一Capsule对刚创建的child启动trade turn
```

1. **使用现有`w3_thread_operations`，不再建第二套任务队列。**将操作扩为“准备Case执行环境”，增加created_at/updated_at、step、错误类别、execution generation和可选capsule_id。canonical request保存parent、run_id、profile、asset身份和attempt scope；相同key参数冲突仍拒绝。
2. `operation_key`仍为`w3-fork:<record_id>`。Scheduler在首次发请求前，将record_id/key/prepare参数摘要写入stage.input_refs；即使thread_id尚无值也保留这条关联。case失败展示或lease回收不能让下一次恢复创建第二个record。
3. 增加认证的prepare接受/查询接口。查询只读JobStore操作行，不调用SDK，不排到生命周期锁后面。POST响应丢失先GET同key；没有登记才以原key、原参数重发。operation READY取回原child，CREATING为准备中，只有明确FAILED才走失败语义。

内部请求合同固定为`operation_key/record_id/source_case_id/parent_thread_id/run_id/attempt_id/phase/research_pack_id/reference_pack_id/case_overlay_sha/profile/capability_contract_version/cutoff_at`，新增schema仅服务内部准备协议，不加入Agent交易输出。返回`operation_key/status/step/thread_id/error_code/error_message/updated_at`，必要时带进程内capsule提示；READY在存储上可沿用COMPLETED，客户端不要依赖诊断文件推导状态。GET对完整key做URL编码，认证规则沿用现有内部服务。

已有旧格式operation的canonical request不可被新spec覆盖。读取为legacy_fork版本，按原parent/cwd/model字段核对后接回child；资产绑定和scope准备另走幂等materialize步骤。不能因为新spec多出字段，就把这38条原操作判成参数冲突或重新fork。
4. 公共SDK现有`thread_fork`直接在执行Capsule使用；正常新操作不再先由另一个App Server fork、关闭释放writer，再让Capsule resume。SDK返回的child句柄留在该Capsule，首轮可直接turn，不再重复thread_resume。这个变更只消除交接，不删除所有权约束。
5. 在Capsule的line protocol加`prepare_fork/ready`，首轮job按child/operation绑定该Capsule；prepare已占有的实例不能分配给另一Case。Worker重启丢掉的capsule_id只视为内存提示，真实child由operation/registry恢复。
6. 启动一小批**未绑定Thread**的热Capsule，目标数量初版沿用现有launch_wave_size；被取用后按现有startup/CPU/内存治理补充。不是活跃case总上限，不按ticker机械各预留几个重进程，不等待正在运行模型的capsule，不引入另一个固定并发闸门。余量不足允许弹性冷启动，记录并纳入SLO。
7. 同Case运营继续用同Capsule。已完成Case的绑定Capsule不能随意换给别的Thread；默认仍按现有安全回收/TTL。不要通过跨Thread复用仍持writer的SDK客户端造成新冲突。
8. `prepare`只做文件与生命周期，**绝不启动trade模型turn**；现有job提交、幂等回执、fencing和TradeOutputService是唯一交易入口。operation READY不代表已形成模型job，更不代表已形成意图。

**失败与恢复：**

- 首次正常Fork没有不确定副作用之前，无需每次先`thread/list`扫一遍。只有响应丢失、进程重启或数据库记录与实际结果不一致时，才按固定cwd+parent对账；不能按相似标题选child。
- 准备中不是一般失败。HTTP读取超时、查询失败和operation尚在执行分开记录，不能用空ReadTimeout触发三次失败后彻底遗忘。长准备暂时yield后仍接回同operation；已有租约/控制状态继续生效。
- 给SDK生命周期RPC有限执行预算，初版以健康路径不足0.4秒为参照，单次RPC10秒、准备整体30秒作为异常处理预算，关闭/强制回收约5秒另计；预热不受业务模型600秒预算替代。它们不是健康路径允许耗时，也不能通过调大预算接受分钟级等待。
- 超时取消必须终止该prepare所拥有的App Server/进程并确认不会继续执行，再查询同key/固定cwd的既有child。只取消asyncio.to_thread等待者不够。已创建child则回收同一结果；未确认前保持reconciling step，不换key重fork。
- 正常新Fork无需独立生命周期helper；status/delete/旧协议恢复使用各自短会话或同Thread安全会话，不共享一把无界全局锁，不能关闭另一个正在执行模型的Capsule。
- 清理从前台Fork平面剥离，按现有后台executor分批推进；不在主loop里连续等待数百个status/delete。故障清理不能影响实时prepare；不用“更大锁+更长HTTP read”解决。
- 遗留API保持兼容：Worker先发布时旧同步fork端点仍返回旧thread结构，底层复用同一durable operation，并在其有限预算内返回明确结果/错误；新调度端使用新prepare/GET协议。不能直接把旧端点改成202而让旧客户端按thread_id解析失败。

### E. 去掉重复启动的重型初始化

文件：`sdk_runtime.py`、`mcp/data_server.py`、新增小型`tools/w3_factory.py`及必要工具名常量模块。

1. `sdk_runtime`模块顶层不再导入全量`tools.factory`，也不通过导入`mcp.data_server`获取两个工具名常量。把常量移到无重型依赖的小模块，其他调用点仍导出兼容名。
2. Runtime构造不提前建全量registry。首次已知request.node后，为W3只构建现有已授权的`ibkr.market_history` client、descriptor和contract；其他节点按原default factory按需构建，不改变其工具目录/权限。
3. Data MCP同样按W3 capability使用轻量factory，不能只是全量构建完后再过滤。IBKR descriptor/contract从同一权威定义复用，避免复制后漂移；不改provider行为。
4. Capsule预热只加载Python/SDK和无Case私密状态的基础设施；不能缓存前Case的cutoff、ticker、capability token、observation目录。Case准备/首轮用同一scope配置，避免为了立即resume重新生成不同配置并反复启动相同MCP。
5. 运营phase的attempt scope仍更新，观察记录和权限不得写入前一个attempt；后续关联Case复用Thread时也必须更新scope。缓存边界由profile/能力合同与attempt区分，不能为了减少握手沿用旧权限。
6. 先用探针验证剩余启动落在哪个SDK/MCP步骤，再决定是否需要更多进程优化。不引入Python forkserver、共享跨Case MCP服务、常驻券商连接池等新基础设施。

### F. Effect及时唤醒与正确的失败状态

文件：`service.py`、`coordinator.py`、`w3_execution.py`、`w3_threads.py`、`v2_read/projectors.py`、Case detail/wire/前端对应展示。

1. 保留已有路由提交后的立即派发，把它收敛成共享的合并唤醒入口，覆盖运营Effect和恢复后新到期Effect。路由通知、coordinator周期补偿都使用现有coordinator effects executor，不再与`projection_outbox.flush`共用service executor；保留service executor负责投影刷新。单独使用service的测试/回测按原入口直接派发，不能隐式启动生产后台调度。
2. 唤醒只能在业务事务提交后发；不能在执行器等待中持journal锁；重复通知合并，不为每个case新增dispatcher或积压一队Future。未到available_at的重试仍按due语义处理。
3. 预期将健康`Effect创建→stage接受`压到数十至数百毫秒；如果仍慢，用journal.begin与executor边界定位，不先增加Case准入额度。

派发批次先启动已领取的W3线程，再推进同步非W3副作用，保留各Effect原有幂等与依赖约束；不让归档/Delta等网络动作占住W3入口。coordinator的周期补偿触发放到reconcile之前，避免日历追赶延误已到期工作。无需缩短所有ticker轮询周期，也无需新建常驻dispatcher服务。实现时给唤醒记录提交→pump起点的queue span，结合本轮已有claim边界验收；目前claim之前的executor等待仍是从创建时间推算的混合区间。
4. 准备/模型阶段的终局失败同时保存非空code/message、失败step及operation关联到主Case、W3 route、stage；空异常文字以类型和所在操作补足。
5. 读模型顺序：已提交交易判断→RESOLVED；未提交且终局失败→FAILED；可恢复失败→FAILED_RETRYABLE；有实际running job→RUNNING；真实等待准备/派发→PENDING。不存在最终结果不能覆盖FAILED。
6. 运营失败只显示运营失败，不倒改已提交交易。相关phase状态更新须触发所属Case summary重投影，可复用主Case写入/现有outbox依赖，不能等另一条消息“顺便刷新”。API详情返回失败阶段/错误；前端区分准备失败和模型失败，沿用已有业务状态枚举。

## 5. 38条历史失败的接回办法

实现后生成独立恢复manifest，包含Case、原Effect、原record、operation key、parent/cwd、已完成child、原pin/截止时间及当前状态。对照Runtime和Worker，不按当前owner_case_id为空就断言没有旧record。

1. 先接回一个样本：验证原operation COMPLETED、参数/parent/cwd一致、原child未关联其他Case、原Case无已提交决策/意图；绑定原record/stage并用原Effect恢复。
2. 其余同类按原身份接回，不能批量生成38个新Fork。只有原child确实不存在时，才按明确的恢复代次处理，保留旧operation证据。
3. Case输入、版本pin、语义日、模型profile和历史会员沿用原冻结值；动态刷新仍只在现有允许阶段进行。现有过期/授权/控制/去重条件继续限制真实意图，不能为了补研判延长旧交易窗口。
4. 只重建读模型可先修正FAILED展示，不顺带伪造主Case成功。真正恢复每条保存前后记录、原因与最终job/stage/意图状态。
5. 不重跑W1/W2、不清空旧记录、不恢复已提交阶段、不将晚到child视作一次成功研判。第一条接回验收后再运行其余清单，出现身份冲突停该条而非更换key蒙混通过。

## 6. 验证与发布顺序

### 质量与故障覆盖

- Pack：相同版本只派生/下发一次；改变任一部件只创建对应新版本；混合并发、重启、旧pin、partial安装与重复绑定；不存在symlink越界；API不能修改共享context。
- Template：研究/O2/O3更新不调用模板模型；AGENTS/Skill、profile/能力合同变更会生成新Template；模型分类Luna/max、HIT Sol/medium不变；旧母Thread不偷换语义。
- 动态状态：新增意图、成交回执、PIN取消、WATCH到期和72h跨边界对新Case可见；已接受Case的冻结输入不被后台cache改写；运营仍刷新状态。
- Fork：多operation并行、一个挂住不挡其他；HTTP响应丢失、Worker重启、Fork落盘后丢响应、准备完成但模型job尚未提交、Capsule消失；均接回同child，任何case最多一个真实首轮job。
- 超时：验证服务器进程已停止后才结束异常准备，不能在Case失败数小时后再创建child；RPC正常和异常都释放/保持正确writer；旧独立helper路径的释放规则保留。
- 交易：prepare不调用模型；trade-first两阶段、宿主校验、过期/消费/去重、CLOSED选择及离线回测仍用原合同。新代码版本的回测创建新run，不能resume旧代码指纹的baseline。
- 展示：准备失败、模型失败、可恢复、运行、已提交+运营失败逐项验证；38条历史Case重投影不再显示PENDING。

### 性能验收

定义主指标：`交易stage提交 − 原Effect创建 − 该Case全部实际SDK交易turn区间并集`，失败尝试和异常恢复另外完整保留；turn之间的重试等待仍算非SDK开销，不能拿首次至末次turn的整个跨度扣除。Template模型初始化仍算非交易开销，不能从主指标偷偷扣除。跨进程SDK duration与本地span须分别校验口径，存在缺失则标partial，不填0；turn_start与SDK在途有重叠时取区间并集，不重复计入启动预算。

健康工程预算：Effect唤醒/接受约0.1–0.3秒，固定资产/动态overlay约0.2–0.8秒，prepare/Fork与环境绑定约0.3–1秒，首轮启动/提交接收约1–2秒。它们是设计预算，不是已证明的分段p90可相加。

验收至少覆盖热/冷Capsule、已有/新资产、Fork/Resume、普通/HIT/RELATED，以及两个ticker各6Case突发；不增加小全局闸门换取资源平稳。用同一Case完整耗时分布判断p90≤5秒，冷回退不排除，启动和新版本切换单列但也进入总体统计。达不到时根据探针继续消除剩余成本，不直接把目标放宽为几十秒。

未形成最终结果的失败Case单列失败率和已等待时长，不能记成0秒成功或从样本覆盖报告消失；缺失span保留partial。不得把文件安装、身份解析等确定性准备移交Agent来缩短表面的“非SDK耗时”；业务输入和Agent研究负担至少保持现状，另检查SDK总耗时没有因这次改造反向增长。

### 交付分组

1. **探针代码与测试：本轮已做。**71项相关测试通过，包含8项新增探针/故障测试。可独立发布，先观测自然流量，不影响后续新协议；本轮未部署。
2. **事件映射/资产Pack/Template解绑：**先实现本地及隔离性能验收；热路径移走重建工作。
3. **Worker prepare+同Capsule Fork与执行+轻量启动：**Worker先部署兼容端点，再部署Scheduler启用新协议；一起验收并行与重启恢复。
4. **唤醒/失败状态修复：**与新调度端同步发布，随后接回38条历史记录，按原权限和时效验收。

这是一组W3内部定向改造。对外业务判断合同保持，内部协议可以直接改到位，不维持两套长期编排；兼容路径只用于发布滚动窗口与既有冻结Case。
