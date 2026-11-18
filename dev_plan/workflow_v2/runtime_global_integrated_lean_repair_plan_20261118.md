# Runtime 非 SDK 延时全局复查与精益修复方案

日期 2026-11-18。本轮为代码审查、低开销补测与方案设计。实施基线为 b99d0dbea 的延时修复，加 b1aaddaa2、1b0494dae 两轮存储源头减量。生产 Worker/Scheduler 实际运行 1b0494dae，当前本地 HEAD 37baf88d1。工作区已有其他会话的 changelog 修改。

**完整 Runtime P95≤10 秒尚未通过。推荐一次贯通“冻结材料→真实 READY→模型→成功结果提交”的精益改造，先删除固定等待和无效工作，再依据供给曲线决定库存。** 本轮没有证实能够单靠撤回某一个存储提交恢复目标，也没有精确锁定历史慢例每一笔跨进程 writer 的持有者。下文将已证实缺陷、资源放大及待验证区间分别标明，并给出实施过程中必须闭合的短期验证条件。

## 1. 证据和度量边界

主要依据如下。

- [原始修复方案](runtime_nonsdk_root_mechanism_lean_repair_plan_20261118.md)、[b99 首次生产验收](runtime_nonsdk_root_mechanism_production_acceptance_20261118.md)、[上一轮实施审查与回退排查](runtime_post_release_regression_audit_and_repair_plan_20261118.md)。
- git diff b99d0dbea 的19文件，以及 b99d0dbea→b1aaddaa2→1b0494dae 的存储路径。后续62bdc9b04为采集恢复索引补修，37baf88d1为发布记录。
- 当前生产结构/触发器标量读取；15秒、250ms间隔的内核SQLite锁采样，限最多4个PID非阻塞线程栈；原生产慢例回执、slot/operation和资源差分。
- 本地临时库的旧/新触发器同形实验、已登记单文件inventory重复校验实验。未向生产写入实验业务数据。
- 本轮13:08–13:10 UTC补测时，最新自然Case仍停留在12:39，属于低业务活动窗口。没有用安静窗口的“少锁”否定11:41慢例，也没有用此时资源数值替代高峰归因。

沿用完整 Runtime 原口径，从持久准入到 Operations 最终owner返回，扣除关键模型区间并集，DoxAgent本地工具计回；W1R3辅助事实抽取不抵扣W3内部等待，A/B并行区间不相加。内部退避、锁、重试、冷准备、SDK初始化及控制RPC均计入。未知区间保留为内部耗时，未完成和缺时钟单列。

| 同口径窗口 | 完整样本 | 完整Runtime P50/P95 | 完整W3 P50/P95 |
| --- | ---: | ---: | ---: |
| b99，07:06–09:07北京时间盘后 | 106 | 5.15/10.61s | 3.80/8.46s |
| 1b0494，11:40:40–12:39:55 UTC连续入组119例 | 116，3例当时在途 | 21.74/69.11s | 17.14/50.93s |
| 同版本12:00 UTC以后连续入组57例 | 54，3例当时在途 | 11.63/24.32s | 8.74/21.31s |

后两行引用上一轮已冻结队列，本轮没有冒充新的实时采样结果。重启集中恢复解释部分前段放大，12:00后依然超标。不同窗口负载不同，不能把差值全归为代码效应。

## 2. 两轮存储提交审查及取舍

性能优先，逐项保留有收益的减量，撤销确定的无效同步工作。新格式已经投入使用，回退必须保留双格式读兼容。

| 实际改动 | 性能/正确性审查 | 推荐处理 |
| --- | --- | --- |
| b1aaddaa2：PublishedDocument正文改本地gzip引用，repository兼容读取 | 减少DB大正文，但get_published_document立即解压、转文本并Pydantic；W3之后再encode/hash/parse，材料cache命中前仍先读完整bundle | 保留落盘格式，撤回W3热路径“查元数据即全量物化”的实现，见4.2 |
| b1aaddaa2：Native小对象单文件压缩 | 少文件、少fsync有价值；单文件本身不构成已证实退化 | 保留 |
| 单文件接入inventory：每轮ContentFiles.verify | 已登记对象仍重新解压/hash，3轮8对象产生24次全文校验；新增的重复工作已证实 | 撤销对已登记未变资产的每轮全文校验，保留新对象登记校验及业务读取校验 |
| Native UPDATE引用差量触发器 | 同形实验减少SQL工作和逻辑写入；仍重复扫描NEW JSON，但无证据比旧版更慢 | 保留差量策略，先加无变化护栏，不回到整组删除重插 |
| repository._upsert无变化不写 | 减少触发器/outbox，正确性取决于业务字段比较完整 | 保留并回归不可变对象身份 |
| Runtime round_inputs改引用；reference baseline优先复用；reference settle只改状态 | 减少重复冻结和物化，均有可见消费者 | 保留兼容读取，审查消费者是否又经row factory全文解码 |
| SourceOutbox JSON字符串capture、Native marker和退出 | capture持久证据仍必要，read/写触发器和消费者可能重复解析同一内容 | 保留原子捕获，减少无变化事件和热路径全文读取；不删除证据 |
| 1b0494：detach后删除、archive文件转换 | 减小长文件工作持锁时间，引用复活和发布先后顺序是必要保护 | 保留，严格测试“新引用/尚未提交put/崩溃”竞态 |
| collection_plane小时归档和owner分页退出 | 替代整小时无界物化；新SQL含状态/JSON谓词，启动CAPTURED扫描已实际卡住并由62bdc9b04索引修复 | 保留已修索引，验证真实查询计划；后台归档页要计实际检查量，不能只计删除量 |
| D3去重复view/final副本、staged projection从发布文件读取 | 减写，但候选发布多一次文件读取；属于维护/发布消费者，不是每个W3必要前置 | 保留正确消费者，文件读取离开写事务，禁止成为实时准入依赖 |
| Runtime collect_resources新增D3退休、Job事件裁剪 | 退休后同身份重发必须禁止；多库读写和文件退出增加后台工作 | 有界页、页间让出；从实时成功返回链拆开，仅留下可靠退出意图 |
| Worker retire_settled_contexts | 加入prepare_assets循环后执行，可能延后下一轮巡检；即时补货本身已有独立入口 | 保留owner退出，确认失败不会让即时refill失去唤醒 |
| workspace snapshot去重复遍历/agent_loop副本 | 减少扫描/复制，W3与其他workflow边界需保持 | 保留 |
| Profile/registry退休，Observation成功原始副本减量，adoption文件兼容 | 多属其他流程，存在实际后台I/O；未证实单项造成W3回退 | 不扩大本次业务改造，按资源差分限制重复工作 |
| 独立compact/VACUUM和多角色发布 | 与上述常驻代码分开。已观察到/tmp/dox-50g-compact-db.py及临时vacuum DB；脚本会停止有关服务并核验文件使用者 | 使用已有发布/资源协调，数据压缩不能在无协同条件下与实时恢复竞争；本轮未停止该作业 |

### 2.1 触发器实验与生产结构

内存SQLite，100次UPDATE，仅改变状态、引用集合不变：

| 每行引用数 | 旧版VM步数/逻辑变更 | 新版VM步数/逻辑变更 |
| --- | ---: | ---: |
| 0 | 8,900 / 100 | 9,200 / 100 |
| 8 | 158,500 / 2,500 | 98,300 / 100 |
| 64 | 1,205,700 / 19,300 | 708,700 / 100 |

这支持保留差量策略；不代表生产事务已达到SLA。json_tree在多个语句中重复求值仍可优化，但不能据“新触发器”直接认定回退。

当前生产Runtime capture仍对payload去updated_at后比较，worker_requests/round_inputs等namespace变化仍捕获到outbox；Native artifact触发器另行维护引用。Research数据库约1200万页、Runtime约972万页，4KiB/页。它们是分配页规模，不能当作热查询工作量或当前I/O速率。没有进行全表统计、在线VACUUM或索引重建。

## 3. 原修复完整性清单

状态以实际代码为准，下面所有未完成项均纳入第4–7节。

| 项目 | 状态 | 本方案处理 |
| --- | --- | --- |
| PriorityGate只选合格队首、取消先释放 | 已完成 | 保留，动态绑定和CRITICAL回归 |
| W3与control-only关闭三项插件市场 | 已完成，缺完整物理写入收益证据 | 核验实际子进程config和文件差分 |
| max_subagents=0和scope兼容 | 已完成 | 保留，真实profile变化仍Resume |
| v6不读Policy/Reference及citation | 已完成主体分支 | 清除残留通用prepare/派生文件工作，旧契约保留 |
| v6 D1/D2独立内容身份 | 不完整 | Case pin外壳与共享组件分开 |
| 同步JSON/hash/compact离开W3 loop | 不完整 | 统一准备一次，网络await留在原loop |
| 材料Future完成立即恢复 | 未完成，仍5秒退避 | 接入现有等待/续接 |
| 冷启动详细跨进程时钟 | 不完整 | 补admission、pipe、RPC与CPU等待，慢采样 |
| 持本地preparation task直接等待 | 已完成 | 恢复无本地owner才查原operation |
| collect先裸候选后引用检查 | 已完成主体，页大小与预算未闭环 | 按实际检查量与writer hold调节 |
| reconcile schema_version缓存 | 未完成 | 复用进程内schema说明 |
| inventory目录遍历有界 | 未完成 | 实际目录项预算＋既有cursor，无新层 |
| expire读取/解析移出writer | 部分完成，后续detach继续改善 | 对齐所有分支并保留publication保护 |
| 双结果批保存 | 已完成主体 | 同owner复用stage/request；部分失败先存成功 |
| Worker timing＋终态减写 | 已完成快路径 | 不删paid completion和失败清理保护 |
| worker_receipts/owner重复读取编码精简 | 不完整 | 成功结果链全程收敛 |
| submit/loop/callback/SQL/恢复时钟 | 不完整 | 现有trace字段补齐，无新持久状态 |
| 显式timeout=0真正零等待 | 已完成 | 保留，不扩大预算 |
| per-turn fast/default | 已传参，实际tier与失败回退验证未闭合 | 审计协议与canary |
| GC水位回填不build COLD | 已完成 | 保留；按真实使用恢复 |
| WAL keeper无读事务 | 已完成Worker | 监测WAL；不能外推其他库也解决 |
| thread not-loaded后同身份查询收敛 | 已完成分支，未知RPC恢复证据不足 | 只确定不存在才收敛 |
| 库存时间线、4/6成本决策 | 未完成 | 本轮补基础供给重建，实施后有限对照 |
| 分批及高峰端到端验收 | 未完成 | 第8节明确验收条件 |

## 4. 优先删除材料准备中的无效工作和等待

### 4.1 用已有Future恢复原Effect

修改w3.py的prepare_case及service.py的特殊错误处理：

1. 第一次按原pin准备时保存已有Future，异常携带等待对象及“就绪后重新执行当前步骤”的类型；不把材料返回值当交易结果。
2. service复用_phase_waiters/续接基础设施和原Effect身份，登记后安装done callback。Future已完成、登记竞态、取消均须可收敛。
3. 当前owner在等待期间保持必要租约管理，完成后用原fence恢复并重新读权威研究引用。过期owner回调只能唤醒原Effect，不能写业务结果或直接交易。
4. 无本地Future的跨进程恢复走现有持久身份重查；有本地完成通知的正常路径取消固定5秒延期。
5. 对template preparing、已完成的材料/Job和capacity释放分别检查同类tick依赖；真实Provider恢复按原语义等待。

收益是直接消除每次命中该分支的约5秒内部等待。上轮INTC已闭合5.11秒，该异常当前日志又出现6次，不能将其称为偶发可忽略。

### 4.2 不再为读取元数据物化完整正文

存储改动后get_published_document读gzip全文、转字符串，再交给W3重复编码和hash。保留其对现有调用者的完整返回契约，在repository增加或复用轻量元数据读取入口供W3使用。W3固定D1/D2元数据与本地/远端正文分开：

- 快速读取pin对应的artifact metadata和SHA；在正文读取之前查共享组件身份。
- 本地压缩正文用现有read_io读字节、解压和校验，业务parse一次。异步远端storage继续用原client await。
- 不为了少一次hash取消可信边界校验。相同已验证的不可变字节在一次owner准备中可复用验证结论。
- v6组件身份包含ticker、D1/D2固定run/artifact SHA、renderer/契约版本；Case pin引用另存原Event/Policy版本。旧pin key只读兼容，不能覆盖历史冻结引用。
- 共享同内容准备Future，失败/完成有界释放；复用现有映射，不新增缓存服务。不同Case的token、Thread和运营状态仍独立。

回退对象是W3同步全量物化策略，不回退gzip格式和已发布数据。该修改减少文件解压、模型构造及重复准备，实际分位收益需在同负载下测量，不能预报固定数秒。

### 4.3 v6准备路径一次构造

_install_context、_run_phase_materialized中的任务JSON/schema/hash/compact和snapshot目录清单，集中在既有I/O机制中一次构造。先确认worker request和本地文件的消费者；若模板占位替换、legacy Reference文件在v6最终prompt中无消费者，跳过那段构造及重复写入。必要冻结/审计保留一份，不能以“最终prompt不含路径”推断所有文件都可删。

## 5. 写入与成功结果消费一起优化

### 5.1 对持锁者的本轮补证结果

本轮250ms间隔15.37秒内核锁采样，对Runtime/Research/Worker的WAL shm byte120及主库RESERVED锁匹配PID，仅捕获1次Worker writer；随后非阻塞栈没有唯一闭合其正在执行的回调。Runtime高业务活动已结束，此结果不能证明Runtime无竞争。

因此历史COHR的秒级gate和BEGIN是已证实等待；具体跨进程持有者仍未知。此前journal.reconcile_interrupted读事务的5.67秒“hold”不能叫写锁持有5.67秒。

实施首批直接在现有事务边界记录慢写的库名、PID、operation、acquired/commit-return、fence标量；read事务明确标write=false。按阈值输出，禁止逐SQL/正文日志。若复现秒级等待，用同窗内核writer身份＋线程栈或现有callback身份闭合，不增加常驻诊断服务。

### 5.2 事务中只做有必要的原子变化

COHR最慢例内部82.56秒，Trade最终owner13.54秒，Operations前owner9.03秒；gate/BEGIN/retry覆盖数秒。对其实际链路按以下次序减法：

- receipt/input_refs/hash/结果编码在writer外一次完成，同owner已有stage/request标量直接复用。
- 两分支成功结果仍一笔短事务保存，各自Job不可变；一个失败时保存另一个已付费成功结果，再传播控制/业务异常。
- stage接收、共识代表绑定、交易提交三种恢复职责先明确。仅同一fence、无外部I/O且原子职责相同的重复更新合并，禁止无条件拼成巨型事务。
- 成功Worker只保存可靠receipt和终态；同Case resident继续存在时不写仅用于中转的CLEANING。真正退出/失败继续确认。
- Effect/control无变化不投影，owner内减少重复Case全文读取；跨Future重新取权威状态，不能用旧Case覆盖并发W1R3。
- ACK、历史副本裁剪、resource release的可靠意图可在必要提交中写入，实际退出由已有后台页处理。阻塞成功结果消费的同步ACK/退出应移出返回链，不能丢退出请求或提前宣称物理释放。

已核实repository.save_effect在取得write_lock之前已执行content.encode，journal.set也已先编码，不能把两处重复改造列为新增收益。具体剩余减法优先放在w3_execution._commit_pair：当前分别读main、A/B两份stage、selected stage与record，构造consensus时又各取两次分支stage。由当前owner一次批取main/A/B标量，复用本次结果，fence内仍重核不可变结果和提交资格。减少的是重复读解码，不能跨owner缓存权威业务状态。

claim_effects已有只查pending/running索引的预探测，且事务内重核，必须保留该修复。control.dispatch每轮仍开事务插票，需核验同一identity有效ticket是否可只读短路；涉及epoch变化时仍原子校验，不能跳过停机/移除控制。

### 5.3 把write_result计时补完整

原result.loop_handoff包住write_io，不能叫纯handoff。最新472个样本submit→loop P95约9ms，而loop内整个写P95约747ms，二者处理方向不同。

添加submit、loop_started、queue_admitted、callback_started/finished、loop_resumed、owner_return现有trace时间；SQL gate/BEGIN/hold/commit保持嵌套关系。无Case的span通过Effect/Job关联，避免最大慢例被“缺case_id”漏掉。计时只用于定位，验收仍取真实owner结束。

### 5.4 Transport Maintenance补齐遗漏

- schema/table/PK定义按schema_version复用，迁移后失效。仅用于schema描述，不缓存业务可见性。
- inventory先查询有限digest及登记状态，已登记且身份/路径状态未变不再次read/verify正文。新对象仍验证格式、SHA；读取时仍验证。重发布/隔离复活依原文件身份核验。
- 目录枚举以实际处理项和时间为界。优先用已有publication登记和index取候选；遗留孤儿扫描复用原cursor，不能在每轮sorted海量prefix全部内容后再LIMIT64。若分目录流式扫描暂无法有界且正确地推进，先固定少量prefix一轮扫描并明确最大历史规模，设计验收前不能宣称已真正有界。
- reconcile payload读取、JSON/native_hashes、manifest都在writer外。短事务核对选中原值和cursor，变化留待重查。
- collect/expire每页只重核当前引用、hold、pin、epoch和所选身份，退出状态原子提交；最终删除沿既有detach/claim/recheck保护。页间释放writer，按原背景资源规则yield。
- 新Native差量触发器保留。先加payload及identity均无变化时不执行的护栏；只有实际trace证明重复json_tree成为热点才做更进一步变换。新引用创建仍与业务写在同事务。

inventory实验8对象3轮校验24次，证明可以取消一类实际无效工作；本地毫秒结果不能当作COHR十几秒提交的全部解释。

## 6. READY供需和冷启动的整体最优

### 6.1 本轮补的库存重建

复用上一轮完整slot/operation标量，Worker11:39:33–12:39:55 UTC：

- 270次绑定，185 MISS、85 HIT，MISS68.5%。MISS bound→READY P50/P95/最大7.77/18.89/35.26秒。
- 273份新slot，其中87份在绑定前READY或尚未绑定；后台预建created→READY P50/P95/最大 **2.93/6.24/13.34秒**。
- 273份operation创建→Fork派发P95 **11.42秒**，Boot P95 **9.54秒**，这里混合后台及绑定构建，不当作纯Native CPU。
- 按已知ready_at→bound_at、仍未绑定者到采样终点重建，约510/3624秒没有可见预建库存；该值是未知退役信息下的保守上界，不冒充精确空库率。本批新slot未见RETIRING，状态仅USED/BOUND/READY/BUILDING。
- 对齐Worker起点的分钟桶最高32次领取。单后台构建P95超过6秒，突发消耗会快于供给；库存4的某个快照无法代表高峰命中率。

当前warm_target明确设为0，通用warm不再额外留无用配置。不能因为W3 profile与通用Capsule不同而盲目恢复另一套常驻warm。保留真正可认领的私有READY才有价值。

### 6.2 优化顺序

先完成第4、5节，减少基础准备和资源竞争，再观察：

1. 同slot完整记录创建、admission开始/通过、subprocess、首条boot、SDK_READY、Fork发送/子进程RPC开始/结束/父进程收到、slot发布。
2. 对超过工程预算的准备才采Python/Native子树CPU、调度/缺页和cgroup I/O；不能把wall减Python CPU的差叫全部磁盘等待。
3. 核验插件override、max_subagents和recovery profile在真实父/子进程生效；必要MCP四工具及Web保持。Native日志sink仅有文件级高写证据才处理，不改底层大架构。
4. 记录后台unbound builder实际服务区间、绑定升级及补货完成→下次开始。绑定冷构建不计为后台构建者占用；即时refill不受reconcile/retire/GC完成限制。
5. 检查资源reap只回收可退役资源，普通压力保护所需基础4份，严重内存压力保持保护。退役先让实际resident退出再更新可领取状态，避免“逻辑READY”。
6. 用实际到达曲线及新后台服务时间做4/6重放，再单变量生产试验。若6份不能覆盖持续吞吐缺口，拒绝只扩大库存。有限双后台流水线必须显示净资源/吞吐收益才启用，实时MISS继续直派。

库存扩大可能节省准备，但也增加常驻PSS和预热CPU。当前证据足以说明供给缺口，尚不足以决定应永久6份或两个builder。

## 7. 调度、发布恢复与生命周期

### 7.1 六个失败Case逐例复盘

| Case后缀 | ticker | 创建UTC | 失败环节 |
| --- | --- | --- | --- |
| 65ed8c5 | INTC | 11:38:58 | MATERIALIZE |
| ea587dd9 | INTC | 11:38:51 | MATERIALIZE |
| 078635f4 | COHR | 11:38:40 | CONTEXT |
| be05726c4 | SNOW | 11:38:21 | MATERIALIZE |
| 9b12aa | MU | 11:38:02 | MATERIALIZE |
| 2d567b9 | MU | 11:38:01 | MATERIALIZE |

全部为“All connection attempts failed”，持久化stage没有Worker Job绑定，按其已保存workspace/run关联读到的Job集合为空。该结果表示没有找到已记录的请求，不足以证明请求从未被受理。COHR甚至尚未有分支record。六例均位于Worker替换时段，且在前置材料/文件API失败，不能仅修ReceiptWorker.run的404分支。

当前lookup_job_id仅明确404返回None、HTTP异常转w3_dispatch_unknown的主派发恢复逻辑是对的。遗漏是材料/工作空间/上下文RPC的基础设施故障最终进入普通3次业务失败预算。

修复设计：

- 材料/上下文API的连接错误、超时、5xx分成可恢复基础设施状态，沿原Effect/record继续，不增加普通模型输出失败计数。
- 对可能已受理的副作用API使用原operation key和原身份重查，无法确认时保留UNKNOWN；确定未受理才原幂等重试；明确404才考虑新派发规则。读API可原pin重读。
- 成功Job已有receipt优先消费，Provider Pause不能挡解析/提交。
- 保留现有每ticker6/12模型线程配置和W3实时直派；检查容量释放Future回调、原Effect重新可运行时的唤醒，不增加新全局并发门槛。
- 发布Worker前停止新增非必要背景派发，保留已受理身份，待替换后完成Job/operation可查询核验再恢复；服务通信抖动不能使Case永久FAILED。对6例的人工恢复另立执行步骤，实施需先核对交易终态及原Job，不能本轮直接改历史状态。

### 7.2 背景任务与资源

独立压缩脚本会停止所选数据库消费者并做VACUUM/校验/替换，属于发布操作，不应伪装成轻量后台页。使用已有资源协调和同窗指标决定窗口；实时恢复不应同时承受压缩、存储迁移、批量历史清理。

上一轮12秒差分观察到research Python写324MiB、Worker主Python109MiB、acquisition压缩125MiB；15秒cgroup差分API和Worker均有百MB级写入。进程打开某文件不能证明全部写来自该文件，晚于历史慢例的快照不能解释全部历史。需要同窗归属才决定具体后台降频/暂停，不能降低W3实时并发。

### 7.3 P1生命周期减法

保留GC只更新retained_until、不复建COLD；NULL水位在回填完成前保护，崩溃游标可续。Worker keeper不持读事务，关闭时释放，监测WAL；其他库不盲目复制新连接池。

旧Thread删除保持run/parent/thread和active Job核验；not-loaded需同身份查询证明不存在才能终结，网络失败保留退休意图。retire_settled_contexts、SourceOutbox/Native退出按实际owner有限页推进，历史扫描不得加入实时Case。若每轮metadata无变化，直接退出；资源文件编译在必要消费时完成。

## 8. 实施依赖、收益与验收

| 顺序 | 最小可交付范围 | 可预期收益 | 资源代价与放行条件 |
| --- | --- | --- | --- |
| 1 | 材料Future即时续接、基础设施错误分类、必要细分时钟 | 每次材料延期去掉约5秒；避免发布期消耗业务预算 | 复用现有等待结构；验证fencing、已完成Future、停机和原Job身份 |
| 2 | v6组件身份、metadata优先、同步编译/编码减法 | 减少重复加载与准备，常态和P95均受益；秒级收益待同窗验证 | 不新增缓存层；旧pin、旧契约和同Case线程不变 |
| 3 | 成功结果链减写＋maintenance有界/取消inventory重复校验 | 减少writer队列和无效CPU/I/O | 必须取得真实持锁者/耗时分布；保留receipt、交易原子性及文件保护 |
| 4 | 冷启动详细成本减法、供给重放；必要时单变量4/6试验 | 覆盖缺货长尾，收益取决于供给和突发 | 不同时扩大库存/builder；无新增隐性排队 |
| 5 | owner生命周期和长期防回退 | 降低历史规模放大 | 只触及已证明热点，已有有效修复不重做 |

如果实施时已闭合writer热点属于特定新增存储实现，优先撤回该路径的同步扫描、全文物化或背景重工作，再评估更细优化；不得为了存储减量接受实时延时。但当前差量触发器实验支持保留，不进行整体存储回滚。

正确性回归必须覆盖：

- 相同D1/D2不同Event/Policy pin复用组件而各Case版本正确；旧key与旧契约兼容、同内容并发不重复准备。
- 材料Future完成前后竞态、过期owner、进程重启和关闭、Stage部分成功、lease丢失、paid receipt存在但终态未保存。
- 原Job三态、查询404/5xx、派发回包丢失，材料RPC失败不消耗模型失败预算，未知不会新建重复Job。
- Maintenance大目录/全受保护/零变化、晚到小key、原子put尚未提交引用、quarantine复活与崩溃恢复；目录/VM检查量及writer外编译可断言。
- 相同profile无Resume，不同profile合法Resume；每轮Trade fast、Operations default以实际协议/受控canary验证，不以UI标志推断。
- READY动态绑定、取消、资源压力、即时refill，所有MISS仍直接准备。

轻量防回退约束放在这些已有测试入口及正式发布验收中，不增加守护服务。对关键SQL用查询计划和progress步数预算测历史规模；对writer测试断言没有文件/压缩/网络；对Future用可控完成事件验证无需tick；对新版codec保留双格式回滚读端。

最终同版本至少100个连续完整实时Case并覆盖真实开盘高峰，列出全部入组/未完成/失败/缺时钟。完整Runtime非SDK **P95≤10秒**，Trade、Operations及P50单列，冷启动/内部锁重试全计入。原失败窗口仍保留。state/通知预期毫秒级，短事务几十毫秒，writer hold P95以20ms为初始工程目标，冷准备常态数秒；每个子目标不能替代端到端达标。

同时核验CPU/I/O PSI、实际Swap增量、Worker子树PSS、物理写入、WAL及文件增长；不扩大超时、业务失败预算或重试来制造通过。

## 9. 交付与未闭合问题

新增证据位于eval/runtime_global_followup_audit_20261118/：

- db-structure.json、latest.json：实际结构/capture/Native触发器和版本附近元数据。
- writer-owners.json：15秒内核锁和对应非阻塞栈，明确只捕到Worker，未闭合历史Runtime持锁者。
- trigger-probe.json、trigger_probe.py：旧/新差量触发器实验。
- inventory-probe.json、inventory_probe.py：已登记单文件重复全文校验实验。
- supply-reconstruction.json：基于既有完整slot/operation的供给区间和不确定性。

历史Case计时和进程I/O复用eval/runtime_post_release_regression_audit_20261118/。没有批量下载正文或再次支付模型测试。确切Native调度秒数、历史跨进程writer和per-turn供应商tier仍未闭合，实施验收中须补齐。本方案的完成条件包含这些证据和端到端性能，不能以实现几项局部优化后结束。
