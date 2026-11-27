# O2 正常窗口收敛、Provisional 边界与 HISTORY_RETIRED 精益修复方案

日期：2026-11-27。状态：按本轮审查意见完整重写，**仅方案，未执行开发、数据修改、服务操作或发布**。本文替代上一版的实施设计；事故证据及已执行应急操作引用前轮记录，不代表本轮重新核验的生产状态。

## 1. 最终设计与边界

本轮采用一条完整链路：

> Case Fact 首次落库时确定唯一 Provisional 可见日 → W1 只读取该日事实 → 正常 DAILY/既定 Sweep O2 按 ticker 接管历史 PENDING → 只确认真实结算的 Fact → 正式 Library 更新 → 没有真实正文消费者的旧版本正常退休。

确定的业务调整：

1. 取消正常维护窗口之外的 SUPPLEMENTAL O2，包括按 Case 完成通知反复创建完整维护任务的路径；保留 DAILY、既定 Sweep 维护和这些正常任务的必要失败恢复。
2. O2 的事实成员集合独立于原 Case 语义日和 Sweep ID，接管同 ticker 在实际冻结点之前已持久化的合法 PENDING 候选。W1 的查询范围不随之扩大。
3. 原语义日 D 的事实，仅在首次落库时符合边界条件，允许一次归属 D+1；已有未结算旧事实不跨日搬运，维护失败不触发再次顺延。
4. 先解除 W3 已冻结输入、Operations 本地提交对历史正文的不必要依赖；只保护仍需读取正文的精确消费者，不因 Operations 未结束就保留全部旧版本。
5. 成功模型结果优先恢复原本地提交，不重跑交易研判、不重复付费或业务动作。

不恢复逐 Run Clone、不扩大无引用历史“最近七天内最多三版”、不恢复完整历史分支，不引入新队列服务、全局资源管理或复杂跨库事务。W3 Prompt、Skill、输出合同、交易规则、SDK、模型/参数及单/双任务模式均不属于本轮改动。O3 当前开关和待维护义务保持原语义，不通过本次上线重新开启 O3。

## 2. 当前代码中需要改变的真实机制

本轮只读核查本地现有实现；生产发生率和时间引用前轮落盘证据。

| 当前机制 | 代码位置 | 本轮决策 |
| --- | --- | --- |
| dirty_case 后台扫描，Case/父维护结束后派生 supplement:* | coordinator._schedule_supplements/_schedule_o2_supplement/_schedule_case_supplement | 去掉新增补充 O2 的生产端及相应周期扫描，不改名保留日内维护 |
| 迟到消息还可能产生 drain:*、supplemental:* Sweep，末尾统一建 maintain:{sweep_id} | coordinator 入站及 _sweep 收尾 | 保留必要消息处理，只让既定 Sweep 派生正常 O2，不能漏掉此绕行入口 |
| 先按 Case 日/Sweep 选成员，再读取这些消息的候选 | bounded_inputs.predicate/members/records、maintenance._frame | O2 候选改为 ticker PENDING 冻结；O3 等其他输入不因此无条件扩大范围 |
| 原消息＋候选序号形成稳定 candidate_identity，O2 atomic ID 不含日 | repository._candidate_identity、daily.RuntimeDeltaBatchAdapter | 顺延不换消息、不换 Fact 身份、不复制候选 |
| Provisional 按 ticker＋trading_date 读取，同日 PROCESSED 仍可见 | repository.visible_provisional、service._visible_provisional | 保留单日等值查询，不改成“本日＋历史未处理” |
| 候选写入日固定采用 Case 原语义日 | prepare_provisional/allocate_provisional_in_transaction、W1R3/旧式 W3 编译 | 首次落库时确定一次可见日，保留原 Case 日和事实时间 |
| v6 W3 研究资料按冻结 D1/D2 复用，旧式 Ops 编译仍取 Event 编号上界 | w3.PublishedW3ContextProvider、w3_execution._commit_pin_watch | 不重新加载无关历史库；水位与正文退休解耦 |
| 历史 keep 依赖当前版本与不完整 Case 引用 | resource_refs.py/history_exit.py | 按真实正文读取者修复，删除普遍延长 Ops/维护家族保护的设计 |

`Case.status=COMPLETED` 不等于 Operations 成功，但“Operations 还没结束”也不等于“还要保留整份旧 Event Library”。两者分别判断。

## 3. 正常 O2：固定窗口、全 ticker 接管、有限冻结集合

### 3.1 允许的入口

- daily:{ticker}:{D} 对应的正常 DAILY。
- 原日历调度的 sweep:{ticker}:{day} 所对应维护，沿用原窗口、身份和调度。
- 上述正常任务的原身份重试、进程恢复和已受理 Job reconciliation。

不得新增每 Case 维护、supplement:* O2、因积压存在就触发的日内维护，或把补充任务改名为 SWEEP/repair 后继续调用。

既定 Sweep 与动态迟到 Sweep 按原父任务的调度来源和标记判别。drain:*/supplemental:* 的消息和 Case 可以正常结束，但其候选归后续既有正常窗口，不再各建 O2。只禁 O2 派生，不取消这些消息的 W1/W2/W3。

正常调度不因 PENDING 数量改变频率。O2-only 就绪以事实是否已落库为准，不额外等待所有历史 Case、Trade 或 Operations 终态；原窗口有限等待只用于窗口内已有事实生产任务，超时后冻结已存在事实，迟到部分走第 4 节。不得无限延长窗口。O3 的独立真实输入等待不因 O2 改动擅自移除。

### 3.2 两个时间不能混用

- **C：预定窗口 cutoff。** 用于原日历、Sweep/source 范围与调度；不改写已有任务 inputs，不改变新闻准入。
- **F：实际 O2 输入冻结点。** 决定本次能看到哪些持久候选，也是本次 O2 snapshot/校验的 as_of。

正常维护可能在 C 后才冻结并接管迟到事实。若输入已含 C 之后的信息却仍声称截至 C，会产生时间矛盾。O2 的 snapshot、runner cutoff、时间校验和对应 upstream 截点统一使用 F；原任务计划 C 只作调度信息。原 Case/消息/Fact 时间不变，不读取 F 后新候选，不用后续 head 悄悄扩大重试输入。

### 3.3 一次冻结，重试不吸收新成员

复用 Task checkpoint 与每 ticker MAINTENANCE 租约，不另建调度器。

1. 在原正常维护冻结事务记录小 header：F、候选行高水位、对应正常关闭日/窗口、base、规则版本。事务内仅处理标量和 fencing，不装载正文。F 和高水位必须在取得写入权后采样，不能用事务外已经等待过的旧时间代替冻结点。
2. 按索引分页选取同 ticker、PENDING、未被原准入规则排除、位于该冻结高水位/时间范围内的候选。**不附加 Case 日、Sweep ID 或 Case 全部结束条件。** 高水位与时间共同限定成员，不能凭候选 ID 大小推断到达顺序。
3. 保存一次候选身份清单及集合摘要，必要正文沿既有 Native 引用冻结；重试只读原清单/页，不重新查“现在所有 PENDING”，不多份保存大正文。
4. 按现有 runtime_signature 规则对整个冻结集合分组，再构造模型输入；保留被代表的原身份供结算，不跨 ticker 去重、不使用文本近似。

候选表增加一条 ticker/PENDING/稳定分页次序的必要索引，复用现有读取页大小 100。正式发布安装兼容索引，不在业务请求或 W3 启动时创建。

**积压超过单次安全输入时：** 同一正常 Task 内按确定的最多 128 个去重事实一页顺序推进，复用 receipt/runner；不新建逐页 Scheduler Task、不增加并发。冻结集合固定，page_index 与页结果沿原 checkpoint 保存；运行身份由原 run＋页号确定，单页保持简单路径。每页沿原发布机制只写实际变化，NOOP 不升版；已完成页不重跑，后页不混新事实。页面真实 base 要冻结，不能拿前页最终 head 改写已受理请求。最终 activation 沿原 Control base/CAS 和恢复规则提交，原发布/ACK 顺序不变。

这不是日内反复取新事实的常驻循环：一个窗口只处理 F 时的有限集合。真实页调用、Token 与发布量必须统计，不能以 Task 数下降冒充全部成本下降。

### 3.4 按 Fact 确认，不按 Case 整批确认

- 以真实 resolution/mapping 和验证后的发布结果确认，只有明确结算的 candidate_identity 置 PROCESSED。
- 沿用现有去重代表与 resolved_signatures 覆盖规则，保留真实代表关系；不能因同 Case、同页或同日整批确认。
- unresolved 和 F 后到达的事实保持 PENDING，后续正常窗口接管。
- 缺原始 source/无法构造合法输入的事实记录具体缺口、保留义务；不悄悄确认，也不能阻止同窗口其他合法事实推进。
- O3 关闭时 trade/badcase/coverage 等记录仍保留原 O3 义务；跨日 O2 接管不是 O3 已消费。

PENDING 候选及构造 atomic 所需的原消息 ID、原 source/occurrence 时点等最小源证据，必须在正常接管前仍可读取。沿既有生命周期判定保留这些真实义务，不为等待 O2 保留整个 Case 大 Context、旧线程或 Workspace；若原 source 已确实缺失，明确列缺口，不拿今天的时间补造。

历史 PENDING 的维护属于迟到归档，不是新日催化。Event/Fact 首次获知、发生日期、原消息和 Runtime atomic 身份继续用原证据。

## 4. Provisional：首次落库一次确定可见日

### 4.1 保留单日存储和查询

复用 runtime_v2_candidates.trading_date 作为 **Provisional 归属/可见日**。原 RuntimeCase.trading_date 不变；仅对顺延行补可选 origin_trading_date、boundary_ref 小元数据，说明原归属和正常关闭边界。不建第二套候选表或跨日影子行。

candidate_identity、Case ID、originating_node、runtime_signature、消息时间、Fact occurrence_date/subject_time/assertion_state 不变。created_at 是原事实首次成功落库时点，重试不刷新；稳定 atomic ID 不含可见日。E# 继续是既有局部展示/召回别名，不能把顺延实现成新建业务事实。

W1 仍按 `WHERE ticker=? AND trading_date=?` 查询，不加历史日 OR 条件、不扫全 ticker PENDING。内部审计字段不扩进 Agent 的 Markdown 事实视图，不改提示词或 W3 输出 Schema。

### 4.2 一次性规则

设 D 为 Case 原语义日，T 为候选**首次成功持久化时点**，N=semantic_day(T)。使用现有美东 02:00 语义日与 boundary()；D+1 是下一自然语义日，不是下一开市日或 UTC 加 24 小时。

先按稳定身份查重；存在则原样返回，**不重新执行归属计算**。

| 首次落库情形 | 可见日 | 处理 |
| --- | --- | --- |
| N=D，尚未越过覆盖 D 的正常最终 O2 冻结边界 | D | 原逻辑 |
| N=D，已越过覆盖 D 的正常最终冻结边界 | D+1 | 一次顺延；仅该窗口实际存在时适用，不能把任意 O2 开始当日关闭 |
| N=D+1，原 Case 已在 D 受理 | D+1 | 语义日边界迟到，一次顺延，不取决于原 O2 是否发布成功 |
| N>D+1 | D | 仅作历史 PENDING 等正常 O2，不包装成当前日事实 |
| 已有 D/D+1 候选因 O2 失败仍 PENDING | 原归属 | 不移动、不复制、不再注入后续日期 |
| Library Bridge/已发布事实 | 原路径 | 不作为新边界事实再次顺延 |

关闭 D 的边界来自原日历 DAILY 或既定 Sweep 维护。Sweep.inputs.day 与 Case 原日可能不同，须按原 window/cutoff 对应关系确定；不能直接拿 Sweep ID 日期当 D，也不能使用已取消的 SUPPLEMENTAL 作为边界。

仅新事实首次插入适用。已在 D 落库、可能已参与 D 的 W1 或 O2 的事实，不因后来维护阻塞搬到 D+1；迁移不把历史旧 PENDING 批量改成今天。

### 4.3 冻结与写入在同一 SQLite 顺序上闭合

F/header 登记与首次候选写入沿原 Runtime SQLite 短写事务排序：

- 候选先提交：在本次冻结高水位内，按实际成员处理。
- 关闭 header 先提交：后到候选能读到该关闭边界，按上表一次确定归属，不落入“未入冻结集合、却留在过期日”的盲区。
- 未提交的写入不算已落库；重试先查身份，第一次成功提交后归属固定。候选归属判断与首次 created_at 也在取得写入权后确定，避免排队跨过 02:00 却仍使用排队前的日期。

边界 header 合并原维护冻结 checkpoint，不加每秒轮询或后台搬运。候选分配只查对应固定正常关闭 owner/日标量，复用原事务/counter 查询；不扫描维护历史、不读 Event 正文、不增加 Trade 等待前置。W1R3 和旧式 W3 事实编译使用同一规则，保留原编译所有权去重。

### 4.4 下一日可见不等于发生在下一日

- 保留 occurrence/source 原时间，展示日期不改成 D+1，不因归属变化把旧消息认成新催化。
- 已冻结 W1 输入不回填；后续新 Case 才读取该日增量，沿原固定前缀和缓存规则。
- 同日 PROCESSED 可见语义不变：D+1 基线先冻、O2 后结算，顺延行在 D+1 仍用于去重。
- 若事实已经在 D+1 冻结 Library 基线中，对**顺延行**复用 frozen_fact_contents/fact_content 的既有精确内容判据过滤重复展示；用已加载固定集合，不加逐 Case 查询，不扩大成所有旧事实的新全局去重。
- Bridge 只补相对固定基线的新可见内容，原 Runtime 候选已在时不造副本；root 切换/旧内容恢复也不等于新催化。

明确取舍：旧事实长期未发布，后续日期 W1 不持续看它，义务由 O2 PENDING 承接。本文不承诺在禁止跨日注入的同时，让任意多日前未结算事实永久出现在当天 W1。

## 5. 存量 SUPPLEMENTAL 与正常失败恢复

先切断新增入口，再按 ticker/状态/真实 Worker 回执小页收敛，不能把 200 个旧任务迁成 200 个恢复任务。

| 旧补充任务 | 处理 |
| --- | --- |
| PENDING/HELD，无已受理或未知 Job | 管理退役，候选保持 PENDING 归正常窗口；释放无用旧 frame/base 保护 |
| FAILED，无在途/未知 Job，无可提交成果 | 同上，不再因历史错误自动恢复而重开补充模型调用 |
| RUNNING/派发结果未知 | 保留原 Job 身份精确查询，不假定未受理、不重派；已受理阶段收真实结果，阻止后续新增付费阶段 |
| 有可验证 bundle/publication，只差校验、activation/ACK | 尽可能在原 base/CAS、lease 和身份下完成本地结算，不再为收尾调新模型 |
| 已完成 | 不重跑，候选/映射遵循原真实结算结果 |

沿用 receipt.maintenance_retired：未完成且无在途 owner 的任务采用既有 FAILED＋管理退役原因，失败预算不加一，不伪装 O2 SUCCEEDED/PROCESSED。自动恢复、健康统计、父维护/Selection 等待和资源退出识别该标记，真实失败记录保留。过程资源只在确认无 Job/未知结果/下游引用后走原有界回收，不另建永久 HELD 池。

正常 O2 不再依赖 case_maintenance_owner 决定历史事实能否入选，旧关联仅作追溯，候选行状态是权威。dirty_case 的 O2 扫描与生产写入一并去掉；若仍有其他真实消费者，仅移除 O2 分支，保留既有 O3 pending 标志/业务记录，不吞义务。

失败恢复只针对正常维护，沿原任务、冻结成员/页和成功阶段恢复；不扩张输入、不逐日重置预算。已有 paid/frozen 的正常任务按原计划完成，下一正常窗口再接管新增积压；不得在已受理 key 下换输入。补充任务的已保存成果本地结算是一次性收尾，不是继续允许新增 SUPPLEMENTAL O2。

## 6. HISTORY_RETIRED：先解除依赖，再保护必要正文

### 6.1 消费者与最小依赖

| 消费者 | 真正需要 | 保护/释放 |
| --- | --- | --- |
| 当日 W1 基线、相对基线的事实可见性计算 | 精确 root/version 正文 | 保留当日必要基线，不被第 4 次发布淘汰 |
| 跨日尚未冻结 W1R2 详情的 Case | 原 W1 日基线正文 | 保留这个版本，不只保另一个当前 activation 版本 |
| W1R2/轮次输入已完整冻结的 Case | 已有冻结证据 | 重试复用，不因重建输入再次拉库；最后真实读取者结束后释放 |
| 当前 v6 W3 Trade | 冻结输入、D1/D2 资产及独立运行资源 | 不新增旧 Event/Policy 正文要求，不因字段仍带版号就 pin 整库 |
| v6 Ops 本地提交 | 保存结果、原提交状态、必要稳定 ID/控制元数据 | 保留 Stage/paid receipt/幂等/必要原线程身份，不因此延长 Event 正文 |
| 旧式事实编译 | 原消息/候选、历史编号水位 | 按 6.2 解耦，不为取整数恢复大正文 |
| 正常 O2 已冻结/受理任务及必要恢复 | 校验/发布实际使用的 Event base | 仅现存正常 owner 的精确 base，结算/明确 rebase 后释放 |
| 未冻结正常维护、已退役 SUPPLEMENTAL | 无旧正文消费 | 不注册历史 frame/家族 pin |
| 未结束消息生产 Sweep/旧兼容路径 | 尚需读取的实际输入 | 输入已冻结或 Case 已接管时不重复保整库；缺证据仅补确切依赖 |

Case.version_pin 与 frozen_inputs.w1_daily_index 可能不同 root/version，不能混用。按实际读源登记，一个字段不能假装覆盖两套依赖。

### 6.2 编号水位与正文退休解耦

max_published_event_numeric_id(V) 只需保留的 canonical 出生身份与 created_version<=V。对确有发布证明、后来正文退休的 V，允许读该水位；未知、未发布、未来版本仍拒绝，不用当前最大值代替。

published_events/details/reference_view 等正文读取仍严格 HISTORY_RETIRED 校验。出生身份缺失则报错，不猜号。解除正文 pin 时仍须保留有验证定位的稳定出生身份/发布小证据，或复用原已冻结水位；不能把小证据随整库/旧分支一并清掉，也不为它长期保留大正文。测试分支/retained-native 出生语义与 V 后新增对象，不能一并关闭 Repository 保留检查。

这直接消除本次已保存 Ops 结果提交的已证实不必要依赖，不替代 W1/O2 的正文保护。

### 6.3 收敛引用实现，不叠加管理层

继续用 runtime_case_resource_refs、日冻结记录、正常维护 receipt 和原迁移进度。

- 新 Case 在原保存/冻结事务登记**实际 W1 正文读源**，使用已加载的日 pin；trigger 不再解码 Native 大对象。后续状态保存不得把准确日 pin 覆盖回 case.version_pin。
- W1 轮次输入完整冻结、重试不再读原库后，在原提交事务释放对应正文依赖。继续保留未结束 Case 真正需要的记录，删除 Event pin 不等于删除 Case/结果。
- 不预设增加上一版的 Case semantic_day 列、第二套宽泛资源列或 COMPLETED/payload 更新触发器。少量旧路径若真同时读两版，先复用/补齐它需要的确切冻结材料，不能把兼容扩大为正常 Case 的额外成本。
- 正常维护 pin 与 F/base 同时登记，复用 checkpoint；未冻结任务不 pin 旧版，退役补充任务不参与保留集合。
- 旧数据按主键小页、锁外补 header，仅迁移当前日基线、实际正文读者、正常维护和少量尚在收尾的已受理旧 Job；删除大量补充 Task 的长期迁移/保护设计。
- 不给 W3 热链新增读取、完整 Context 编解码、独立写事务或治理 RPC。必要标量登记替换/合并原引用写入，取消冗余状态投影。

回收前检查必要引用和 native head，覆盖冻结、日切、激活、登记/释放交错。不能把缓存 keep 当强一致，也不能宣称二次查询就解决跨库竞态；须证明来源 owner 先保护、目标冻结/接管后才释放。未通过的确切路径先不退休，不引入全局跨库事务框架。

D3 保留当前正式及原真实历史证据 pin。O3 关闭、v6 冻结输入足够时，不为全部 Ops/维护补建整份旧 D3 pin；D3 回收直接查引用表，字段语义调整需验证兼容，但不重构无关 D3 历史系统。

## 7. 已有故障结果与恢复版本

前轮发现 56 个 Ops HISTORY_RETIRED 失败 Stage 均有保存结果（MU 35、COIN 19、NBIS 2）；恢复了 98 个版本标记，27 个未恢复。这是前轮观测，不是本轮最新生产统计。

1. 验证原 Trade COMMITTED、Ops Stage/result、成功回执、原 Thread/冻结合同和 applied 幂等凭据一致；未知在途状态先 reconciliation。
2. 只需水位的结果走解耦后的本地提交，不先恢复整库或等新模型。
3. 个别确缺正文的旧对象，定位原材料与真实依赖，验证后仅恢复所需部分，不切最新版、不伪装完整历史。
4. CAS 恢复原 Effect/Stage 消费，在原事务完成提交/ACK；重复执行不多写候选、watch/pin、意图或消费代际。保留原失败审计，不全面清零预算。
5. 区分 COMMIT、上下文、派发和 SDK 错误，成功结果后的本地异常不再默认显示 DISPATCH；不吞取消、lease lost 或未知派发。

98 个版本不自动成为永久 pin，无引用部分仍按三版/七天退出。27 个未恢复版本仅在真实消费者需要时修复，不为“恢复率 100%”补全旧分支。原 ACTIVE 视图一致不证明 inactive/redirect/全部关系及任意查询完整，按消费者补证。

## 8. 与旧方案、存储方向的取舍

依据：[存储治理 V3](../../docs/global-disk-lifecycle-repair-plan-20261123-v3.md)、[11 月 24 日实施回执](../../docs/global-disk-lifecycle-implementation-20261124.md)、[存储修改与 W3 延时复核](../../docs/w3-nonmodel-latency-storage-change-review-20261124.md)。

855082c31 同时收紧代码及 V3 §16 的无引用范围，最新方案确实采用七天内最多三版。缺陷是“无引用”判定遗漏消费者，不应恢复七天全部版本掩盖。

| 旧版本文内容 | 新版处理 |
| --- | --- |
| 当日及活跃旧日 W1 基线 | 保留，限尚需读取正文的确切依赖，不长期 pin 所有旧日 |
| Ops 未结束就延长完整 Case/Event/Policy 历史 | 删除；本地提交材料与正文分开 |
| 大量补充维护的迁移和 pin | 删除；关生产端、退役队列，仅收尾已受理 Job，正常窗口接管事实 |
| Case 额外 day 列、宽泛 payload trigger、统一增加维护 Policy pin | 不预设实施；优先既有输入/事务，减少状态与重复写入 |
| 成功结果补提交、历史水位解耦 | 保留，直接修复项 |
| 恢复版本按依赖处理 | 保留，不永久扩大范围 |
| 日切持续把旧 PENDING 注入 W1 | 禁止，O2 backlog 与 W1 单日集合分离 |
| REALTIME/CLOSED 延时混为同源 | 分开调查，不以旧 Sweep 排队解释页面 REALTIME 指标 |

保留 no-clone、稀疏 revision/关系更新、Native CAS、O2/O3 分阶段确认、第一获知证据、低频有界治理、原租约/fencing/幂等。有限窗口的精确 pin 是有效引用，不是扩无引用保留期。

## 9. 修改位置、顺序与验收

本节供批准开发后执行，本轮不落地。

| 顺序 | 主要代码 | 最小交付 |
| --- | --- | --- |
| 1 | coordinator.py、maintenance_recovery.py | 关补充生产/复活入口，区分正常与动态 Sweep，退役/在途收尾及等待边界 |
| 2 | bounded_inputs.py、maintenance.py、daily.py | ticker PENDING 冻结、F/header、逐事实结算、同正常 Task 有限分页和原结果恢复 |
| 3，与 2 同批 | repository.py、内部 ProvisionalFactDetail、W1R3/旧式 W3 编译 | 首次可见日、原归属元数据，单日查询和稳定身份不变 |
| 4 | daily_index.py、W1 输入复用、resource_refs.py/history_exit.py | 必要日基线/旧日保护、锁外补 header，去掉多余历史依赖/重复写入 |
| 5 | Event Repository/provider、W3 消费提交 | 水位解耦、保存结果幂等补提交、错误阶段归因 |
| 6 | 原正式迁移/发布/验收入口 | 读写兼容→新正常窗口→旧补充收尾→验证引用后恢复回收，不夹 SDK/模型修改 |

**冻结规则、候选可见日和正常接管须同批对齐**，防止先关补充却仍按旧 Case 日选候选。已有 paid/frozen 正常任务按旧计划完成，新未冻结窗口用新规则；旧 PENDING 不改身份/日期。旧补充在自动恢复入口关闭后小批收敛，业务义务不随 Task 退役消失。

| 必要场景 | 必须结果 |
| --- | --- |
| CLOSED Sweep 数百个 Case 陆续产 Fact | 窗口外新增 O2 Task 为零，动态 Sweep 无绕行，正常窗口仍执行 |
| 多日/不同 Sweep 的旧 PENDING | 下一正常 O2 可按 ticker 接管，不等旧 Case 全结束，按原身份真实结算 |
| F 前后提交/冻结交错 | 前者在冻结集，后者按边界归 D+1；无丢失、双成员或截至时间矛盾 |
| ET 02:00、DST、周末休市 | 原 semantic clock，D+1 非下个开市日，Sweep/Case day 不混用 |
| D+1 首次迟到、D+2 长尾 | 前者仅 D+1 可见，后者仅 backlog，不改发生时间或包装成当日新事实 |
| O2 连续失败 | 已有事实不滚日、不扩原重试集合，业务义务仍可追溯 |
| 插入/回执/日切重启、重复回调 | 原身份、可见日、created_at、页/Job 不变；W1R3 与旧 Ops 不双写 |
| D+1 先冻后发布/先发布后冻 | 前者该日 Provisional 仍可见，后者顺延行不重复展示，Bridge 不造副本 |
| 多页/跨页同签名/NOOP/部分 unresolved | 冻结集合有限，去重在分页前，已成功页不重跑，只确认真实覆盖，不盲升版 |
| 旧补充各状态及 unknown | 无新增付费阶段，unknown 不当 404，本地成果可收尾，不伪成功清候选/O3 义务 |
| W1 日基线/旧日正文与多次发布 | 必要版本可读，固定前缀/顺序不变；需求结束后可释放，无引用第四版可退出 |
| 已冻结 v6 W3/saved Ops | Library 更新/退休不阻断本地消费，无新增模型/Thread/交易决策或重复动作 |
| 历史水位/正文退休 | V 后 ID 不进旧 V 水位；未知/未来/未发布不放行，正文仍严格拒读 |
| 资源/事务 | 无全历史 Case 展开、每 tick 全局 scan、额外追溯、全局锁/服务；实测持锁、页大小、请求量/Token，不只看 Task 数 |

REALTIME 非模型延时沿完整 Runtime P95≤10 秒目标独立验收，Trade/Ops/完整链边界一致；CLOSED 排队另统计。页面 REALTIME non_model_seconds 截至决策生效、不含 Ops，不能混用。资源、Provider 等待、CLOSED 逻辑名额只是调查方向，未证实的不写成本轮开发根因、不盲改并发或扣除内部等待。

## 10. 历史应急与本轮状态

前轮曾暂停 Transport Maintenance，恢复 10 个当日基线与另外 88 个通过 ACTIVE 导出比对的版本标记，27 个未恢复；这不证明全部历史状态完整。按用户要求，前轮五个产品代码草稿已撤回，未发布事故修复。

本轮只更新本文，不重新核验或改变生产暂停、版本标记和任务状态，不运行真实模型，不执行开发、迁移或发布。后续获准实施先刷新现场，按新责任边界生成最小清单，不能直接重新套用已撤回草稿。
