# W3 Case隔离、阶段指令与输入/输出职责重构实施方案

2026-11-15。状态：待实施。本轮只勘察和设计，不修改源码、测试、提示词资产、数据库或生产配置。

依据：用户本轮五项要求及两次补充、`dev_plan/DoxAgent_W3_Input_Context_Optimization_Proposal_20261115.md`、四badcase报告、线程复用审计。以下是推荐定稿做法，不能当作已上线行为。

## 1. 本次最终行为

1. 每个新Case从纯公共指令模板准备自己的物理线程，不再匹配或继承其他Case线程。Trade与Operations、同Case恢复仍共用原身份。
2. AGENTS是共同身份/边界，唯一Skill是Trade方法；`w3/operations.md`只在Operations本轮任务JSON前全文注入。三份资产共同版本化冻结，但Operations资产不提前进入模板、Trade提示词或Trade工作目录。
3. W1最终拥有NEW/OLD；W2最终拥有HIT/RELATED及Condition身份；W3仅拥有LONG/SHORT/NO_TRADE。Operations只维护PIN/WATCH，不输出novelty、policy、delta_candidates。
4. W1R2、W2及现有入口必要环节完成即派发W3。W1判NEW的Case同时走既有W1R3事实抽取，**W3不等待W1R3**，也不等待事实入库。OLD不因进入W3自动被当NEW抽取。
5. 按输入提案裁剪模型可见投影；后端保留完整业务状态和控制权。`w3_research`新版只提供D1/D2研究及既有行情入口，不可旁路读取Policy/Reference。

不增加服务、队列、常驻进程或新的调度状态机。不改变W1/W2模型/提示词、W3模型参数、交易执行规则、WATCH/PIN容量和原有W3入口条件。W3内部非SDK P95≤5秒仍为验收底线，Trade不得因新增事实分支排队。

## 2. 勘察确认：不能只改提示词和两个Schema字段

| 现实现 | 关联文件/入口 | 必须一起处理的原因 |
| --- | --- | --- |
| 同ticker按Policy优先、Event次之查空闲CASE线程，复用后滚动续48h；关联只追加 | `w3_threads.acquire/link`；`w3_execution._thread`；`w3_context.sync_event_links` | 删除调用后还需去掉关联写入、O2同步，以及恢复时依赖的旧记录保护 |
| 现有线程注册表同时承担模板、Case归属、租约、过期清理 | `w3_threads`、Worker `thread_lifecycle`、Ready相关模块 | 不能把整张注册表当“复用代码”删除 |
| 发布校验、Case冻结和模板物化只识别AGENTS/Skill | `execution_bundle.publish`、`service._freeze_inputs`、`w3_assets.instruction_assets`、`w3._ready_configuration/_run_phase_async` | 仅在目录加operations.md会被当前白名单丢弃，或错误提前注入 |
| v5运营输出有novelty和delta；NEW要求事实，OLD要求合法Event引用 | `w3_contracts`、`w3._validate_phase_job`、`w3_execution._commit_operations` | 不能只允许空数组，否则旧校验和业务提交仍会失败 |
| Router对进W3的Case不生成EMIT_DELTA | `router.route_runtime_case`、`service._enqueue_route_effects/_execute_r3` | 删W3事实输出后必须接上W1R3，否则事实消失 |
| 运营同时写临时事实、coverage gap、OLD+HIT badcase、fallback archive | `w3_execution._commit_operations/_fallback_archive` | 这些不是PIN/WATCH，需按W1/W2结果移至确定性提交路径 |
| Read/API使用effective_novelty/effective_policy；v5仍可由运营改NEW/OLD | `case_outcomes`、`v2_read/projectors`、`api_v2/case_detail` | 新版应始终回到W1/W2，历史仍按原契约显示 |
| research对象不仅来自components，还来自state/events/history/evidence/snapshot_catalog | `w3_research_store.bind/objects/call` | 从catalog隐藏Policy并不能关闭read/raw/search等旁路 |
| 当前历史冻结的是72h意图和分析决策，成交快照未用于成员筛选 | `w3_context.freeze_history/history_payload` | 新版48h实际成交不能直接改字段名或用EXECUTION_ACCEPTED筛选 |

审查以当前main工作区为基础；该工作区`service.py`等有其他会话未提交改动，实施需隔离工作树、比对最新main，不覆盖这些改动。方案不把本地所有内容都当作生产已部署版本。

## 3. 用一个新业务契约承载变化，不重解释旧Case

采用`persistent-runtime.w3.v6`，保留v4/v5解码与恢复；输入入口`w3-entry-v6`，资产布局`agents-skill-operations.v1`。不提高仍为3的W1/W2原生Policy编排revision，不把它复用成W3升级开关。

- 在`w3_contracts.py`新增`W3OperationsResultV6`，仅两个必填数组，`extra=forbid`；Trade继续复用`W3DirectionDecision`。
- `is_two_phase_contract`覆盖v4/v5/v6；`is_unified_contract`覆盖采用统一方向的v5/v6。另设一个明确的v6职责判断函数，例如`is_pin_watch_contract`，避免所有“统一方向”分支误以为仍有novelty。
- 逐个审查这两个现有predicate的调用处；尤其`policy_watch`、`case_outcomes`、`_trade_record`、Read/API、回测，不能只修改predicate定义后放任属性访问。
- Bundle保留完整三份资产、输出Schema和指纹、入口版本、研究可见范围版本`d1-d2.v1`。沿用现有不可变bundle/context manifest，无需新增独立版本服务。
- 新Case在既有冻结点选定bundle；一旦冻结，不随active bundle、磁盘提示词或研究库变化切换。
- `entry_enabled`当前是与单个ENTRY常量精确比较；实施须按冻结入口支持v5/v6，不可仅把全局常量改成v6，导致旧在途掉回文件式入口。研究准备缓存及回测入口同样按冻结版本分派。
- 历史已完成Case不回写、不补造v6输出；旧在途Case按原bundle/Schema/原Job消费。已绑定线程即使历史上混过其他Case，也不能为清理历史在恢复中另起线程。
- 禁止为**任何尚无原线程绑定的新Case**执行跨Case查找；旧在途只恢复其已经固定的record/thread，不再寻找别人的线程。旧关联表仅剩历史/兼容用途。

这是一次契约升级，不是为每项裁剪增加功能开关。未完成提示词对齐前，代码可合并、离线验证，但不得激活v6 bundle。

## 4. 独立线程：删匹配，留身份

### 4.1 创建和恢复顺序

重写`W3Execution._thread`的新Case分支为：

1. 读取本Case的Trade stage及`input_refs.record_id`；存在则按原身份恢复，检验record/thread/Case归属，保留冲突报错，绝不静默改绑。
2. 尚无绑定时取得公共模板，然后在**同一短事务**创建CASE registry记录并将record_id/operation_key绑定到Trade stage。使用现有stage主键和租约栅栏，使并发/崩溃重入只能看到同一记录。
3. 事务外沿用Ready/Capsule或既有prepare/fork。Ready slot必须是公共模板的干净子线程；slot一旦绑定业务Case，不得再回到空闲池供其他Case使用。沿用当前一次性slot和准备超时恢复，不新建池。
4. 将物理thread_id写回同一Case记录。准备结果未知时按原operation_key查询/恢复；只有确定不存在才按原规则重新准备。Job派发继续区分已受理、明确未受理、结果未知，不把超时/5xx当404。
5. Operations只能使用本Case Trade stage.thread_id。阶段结果已保存时直接校验、提交；业务重试不得新调用模型，Job恢复不得新发幂等身份。

不为每个Case初始化一套AGENTS/Skill模型回合；保留公共模板、现有预准备和线程资源生命周期，避免因隔离反而增加冷启动。

### 4.2 删除及保留清单

删除运行路径：`acquire`中的Policy/Event候选SQL、相关排序/忙闲选择；`_thread`的acquire调用及POLICY link；`_commit_operations`所有EVENT link写入；O2完成与public prepare对`sync_event_links`的调用。

清理`w3_context`的事件关联重映射函数前必须做调用审查：`event_matches`和`actual_policy_hits`仍被RECENT_TRADE标签使用，保留必要的纯身份计算。`resolve_event_subjects`当前既算规范Event身份，又查询/INSERT线程关联别名；应保留按冻结版本解析身份及已有resolution缓存，删除其中线程表JOIN、alias查询/写入。`sync_event_links`只为这些线程关联批量同步，连同O2/public prepare调用删除。不能把“取消复用”误做成“删掉所有Event匹配业务”，也不能只删显式link而漏掉解析函数的隐式写入。

保留`runtime_v2_w3_threads`与`runtime_v2_w3_stages`：模板兼容key、record/thread/workspace、owner、lease token、过期时间、准备结果、阶段状态均仍必要。无需另建Case线程注册表或再存一份映射。

`runtime_v2_w3_thread_links`停止新增/更新，但本次不在线DROP、不批量删历史、不VACUUM。移除其运行查询和关联同步；待旧Case自然收尾后可单独清理废索引/DDL。用于旧单阶段执行兼容的注册表/恢复代码不做无关大删除。

### 4.3 生命周期边界

新Case的expires_at固定为首次Trade stage.accepted_at +48h，作为既有运营重试/清理窗口；恢复、租约续约、PIN/WATCH动作均不延长该期限。`last_received_at`对新Case只记录最初接受时间，不再承担热点路由排序。owner与租约仍按原逻辑续约/释放，固定expires_at不等于到点杀死正在执行的SDK。

沿用现有“检查Worker活跃状态→处理未完成owner→DELETING→SDK删除→DELETED”流程。已取得成功结果优先消费，即使期限到达也不丢弃；没有结果且过期才按原运营终态规则失败。不得通过删除原线程来强迫在新线程重试。已完成线程按固定期限清理，无需新增常驻清理任务。

## 5. Operations提示词资产与确定性注入

实施时新增`prompts/persistent_runtime_v2/w3/operations.md`占位文件，不撰写业务运营规则；AGENTS/Skill正文由负责提示词的对话修改。本轮计划文档不实际创建或改写资产。

具体接口：

- `ExecutionBundles.publish/load_manifest`为v6要求三份非空资产，全部纳入bundle摘要。Case冻结白名单包含operations.md；文件清单/hash校验覆盖它。
- 模板共享资产选择与Case冻结资产选择分开：`instruction_assets`/`_ready_configuration.instructions`的模板输入仍只有AGENTS和Skill；Case冻结是三份。
- `_run_phase_async`的v6 Operations prompt严格构造为：`frozen_assets['w3/operations.md'] + '\n\n' + compact(task)`。Trade只追加自己的任务JSON，模板只注入公共AGENTS/Skill。
- operations.md从已冻结Case取全文，禁止在运营时重新读活动目录、调用工具去找文件、或重新加载最新bundle。修复重试沿用冻结请求；同一Job原结果消费不再注入或调用模型。
- 在Trade阶段不物化operations.md到可读工作目录；到Operations才按需物化，模型本轮已获全文，不要求再读文件。全量旧AGENTS/Skill混杂运营规则的问题不能由此自动修复，必须同时换成提示词方的新三件套。
- 既有模板key继续包含公共资产、契约和兼容要素，v6不能误拿旧混合指令模板。只更新operations.md可生成新bundle而复用相同公共模板；每Case依然取自己的冻结运营资产，不从模板读取运营版本。
- 占位文件需有明确开发标记；v6发布校验拒绝空内容/原样占位标记。占位不是“提示词已完成”，不额外设计复杂就绪状态机。
- 周末sweep依然只在其任务上附加既有sweep指令，不改成全Case公共运营指令。回测冻结/override提示词也要支持第三资产，不向已冻结旧数据集偷偷补本地新版。

## 6. W1/W2与交易方向彻底解耦

### 6.1 输出契约

Trade保持现有四字段：

```json
{"decision":"NO_TRADE","reason":"…","prior_expectation":null,"expectation_delta":null}
```

`decision`枚举LONG/SHORT/NO_TRADE；LONG/SHORT的prior_expectation、expectation_delta必为非空字符串；NO_TRADE仍允许二者为null或非空字符串。四键均显式输出，保持现有长度和非空白校验。

v6 Operations严格为：

```json
{"pin_actions":[],"watch_actions":[]}
```

继续复用`MessagePinAction`与`PolicyWatchAction`内部Schema，不创造新的动作协议；拒绝novelty、policy、delta_candidates、decision等多余顶层字段。SDK原生Schema及本地解析必须同版；保留已有有界输出修复、逐动作校验与问题记录，不吞取消/租约丢失等控制异常。

### 6.2 最终业务来源和提交

v6的`effective_novelty`无条件取W1 final，`effective_policy`取W2 final，不读运营结果或旧w3_result覆盖它们。旧v4/v5保持原解释。

`_commit_trade`继续原子完成方向/交易输出、Trade阶段提交和Operations effect入队，完全不等W1R3/Ops。对v6 NO_TRADE在同一事务确定性写现有ArchiveRecord，result_basis用W1/W2/NO_TRADE、novelty_source=W1；无论NEW/OLD、HIT/NO HIT都可有NO_TRADE归档。归档只表示本次没有交易，不把NEW改OLD、不把HIT改NO HIT。保留幂等和已有记录检查，不生成重复archive。

方向为LONG/SHORT仍走现有交易资格、闭市候选、意图过期和提交规则；不新增NEW/HIT门槛，也不把HIT当自动交易许可。执行拒绝/未成交不倒写W3方向为NO_TRADE。

| W1 | W2 | W3方向 | 允许的含义 |
| --- | --- | --- | --- |
| NEW | HIT | NO_TRADE | 新事实且Policy命中，但W3认为不应交易 |
| NEW | NO HIT | LONG/SHORT | 独立方向成立，无需编造Policy命中 |
| OLD | 任意 | LONG/SHORT | 仅限按既有LOW/WATCH等规则已进W3的Case；OLD不作为方向否决 |
| 任意 | 任意 | 任意 | Operations不得回写三项判断 |

保留原W3入口：正常置信OLD+HIT无WATCH仍不进W3；RELATED全false归档、对齐缺失/非法保留NEW进W3等规则不变。

### 6.3 运营提交瘦身及日记录

为v6单独进入轻量commit分支，内容只剩：校验原线程/阶段身份、幂等应用PIN/WATCH、保存两数组及动作问题、标Ops COMMITTED、释放owner、排队ACK。沿用现有一个短事务和写前编码；去掉v6引用范围校验、W3临时事实分配、最大Event ID读取、线程关联和novelty fallback archive。

现有日记录按W1/W2确定性产生，不能随删Ops字段消失：W1 NEW且W2不HIT的coverage gap，以及W1 OLD且W2 HIT的badcase，在v6 Trade提交时写入（利用现有Case/日记录幂等）。coverage gap.result改为带v6版本的`w1_assessment / w2_assessment / trade_decision`业务快照，不伪造运营novelty/delta；BadcaseRecord引用W1原reference_ids与W2原HIT身份。`maintenance.py`与Document3 feed显式接收这一版本，旧记录继续原格式。日记录不是W3重新裁决，不因NO_TRADE把Policy命中抹掉。

Read/前端/API/MCP同步显示W1终态来源、W2终态来源和独立W3方向；v6不再显示“W3新旧/命中复审理由”，也不能把缺少运营novelty映射为结果未记录。新两数组运营结果仍是完整运营结果；空数组成功也应COMMITTED。历史详情照旧，无全量投影重建。

## 7. W1R3承接事实：并行，不成为新门槛

按用户补充采用现有W1R3，不保留运营抽取、不新增W3研究阶段。

- `router.route_runtime_case`对v6 W1=NEW生成现有EMIT_DELTA，**包括primary=W3**；对OLD不生成。归档/非W3原路径不变，旧契约W3仍由旧Ops抽取，避免双写。
- `_enqueue_route_effects`沿用已有W1响应身份，将ROUTE_TO_W3与EMIT_DELTA作为兄弟Effect一次保存；保持既有W3直派优先，绝不先同步调用R3或等其Future。W3线程、租约和Worker Job与R3无关。
- R3使用已冻结W1R2会话/previous_response_id和原R3提示词/模型；只抽事实不改W1 verdict。保存`w1_extraction`并用`originating_node=W1_R3`的既有分配幂等键入provisional。
- 新的W3 case input/research冻结不能依赖本Case R3产物；R3完成后不重建本Case manifest/context_ref，不把“自己刚抽的事实”变成当前Case的既有事实。后续Case通过既有provisional读取它，O2结算可见性保障继续保留。
- 对v6且primary=W3的Case，从主流程完成状态聚合中排除辅助EMIT_DELTA，无论Trade是否已提交。现有`_refresh_case_completion`虽有Trade提交优先路径，但Trade提交前会因任意FAILED Effect先标Case失败，必须修改这一处，不能只测“Trade先完成”。R3失败仍留Effect/attempt错误与原重试，节点详情照实可见；不重跑Trade、不撤销意图、不等待R3才显示方向。非W3路径和旧契约原聚合语义不变。
- 调度不加新池或持有W3锁等待R3；现有实时W3直派与续接保留。另一个具体风险是`_effect_executor`为8线程，而background在途上限也是8，若8个R3同步等待模型，会挤掉同池的ACK/投影flush等短任务。复用现有background领取容量，实施时将该池内background Effect body在途预算改为7，至少留1个槽给短控制任务；只调整后台领取常量，不加队列、不扩池、不限制实时W3。验收须阻塞7个R3时W3仍立即派发且投影/ACK可完成，不能只验证SDK能启动而忽略结果展示。

接受的业务变化：W3不再纠正W1误判；W1判OLD的Case即使独立交易也不由W3补事实。这是职责收敛，不应偷偷保留“低置信运营再判新旧”的旁路。

## 8. 输入投影：落地为显式白名单

保留完整内部payload，新增或拆出明确的v6 Agent投影构造器；不可在共享Policy模型上原地pop字段，也不可通用递归正则删除新闻正文里的词。

同一v6投影同时用于User JSON及Agent可读的task/context文件，不能只裁聊天文本、却把完整旧payload写入task.json。完整PolicySet、Reference、W2详细reason等只留后台冻结/审计存储，不再通过v6工作目录、研究pack索引或旧research_path意外提供；不是重做服务器通用沙箱隔离。

### 8.1 共同外壳

保留`phase/input/output_fields/context_ref/instruction`；移除模型User JSON的entry_version、contract_version、context_manifest_sha、direct_objects。内部Case、Job、日志、SDK Schema及manifest继续保留它们。版本和hash从User JSON消失不意味着工具完整性回执必须消失。

instruction由编排给出固定简短说明：研究入口可选，按任务选D1/D2/Web/现有其他工具；只在实际读到相关且未完成页面时继续cursor，不设固定必查顺序。模板任务可使用其最小ready外壳，不注入Case业务数据。

### 8.2 Trade字段定稿

| 字段 | v6投影规则 |
| --- | --- |
| ticker/cutoff_at/message/w1 | 原结构；w1仍由现有events承载证据，不扩展为全Reference View |
| additional_tags | 保留既有信息标签数组，不变成入口或执行开关；RECENT_TRADE与recent_trade统一使用48h实际ENTRY成交口径 |
| trading_context/case_type/watch | 从Trade外显删除，内部保留 |
| w2 | 仅policy_hit、policy_related、confidence、policy_matches；删除上游reason及内部身份字段 |
| policies | 仅title、cutoff、transmission、activation_conditions[{condition_id,criterion,calibration:{reference_state}}]；全Policy内部仍冻结 |
| pins | 保留现有limit/used/items |
| recent_trade | 见下一节；替代原recent_trade_intents |
| contexts | 只留D1/D2的name、cutoff_at，未知截止仍null |
| alignment | 保留status/error及实际存在的hit_has_new_fact/related_has_new_fact；不把缺失补false |

W2映射：`policy_matches.hit=[{policy_title,condition_ids:[...]}]`，related同形但未有可靠Condition时`condition_ids:null`。一个Policy可以命中并涉及其他相关Policy，不能互相覆盖；仅RELATED不编造Condition。

匹配身份始终用冻结Policy ID在后端解析，然后投影标题。标题重复时仅在本次展示中给冲突标题加稳定序号，例如`原题〔1〕`，排序按内部稳定ID；policies、policy_matches、Ops policy_targets共用同一映射。源Policy标题和ID不改，不新增向Trade泄漏内部ID的别名。缺失必要HIT身份应沿用冻结输入缺失错误，不能编造或静默改NO HIT。

Policy cutoff读取**该Policy真实生成/有效维护的可追溯时间**，不借用PolicySet时间。当前契约可直接看到的主要是PolicySetVersionMetadata.published_at，不能据此给所有Policy赋同一时间；实现适配器只从已有单Policy来源/维护审计提取，缺失为null并在后台记录缺失来源。不能为填满时间另建一套D3维护状态机，也不能回测时读最新维护记录越过冻结版本。非null时间必须有来源版本与目标Policy佐证。

### 8.3 48h actual-fill历史

`recent_trade`只包含同ticker、本Case cutoff之前48h内有正数量**ENTRY实际成交**的历史Case，部分成交算。每Case一项，形状仅`{message,w3_decision}`；message沿用历史消息投影，w3_decision保持四字段。EXIT成交、外部/手工单、仅分析方向、已接受但零成交均不算。

时窗统一为`[cutoff_at-48h, cutoff_at)`，用入场成交time判定；排除本Case。按最近有效入场成交时间、Case ID稳定排序、同Case去重。cutoff沿用当前消息总线判断截点，不用组装当下时间替代。

实现从交易执行权威记录沿`te_executions → ENTRY te_jobs → te_attempts → te_fills`关联到原Case。复用`_effective_fills`的成交修订family/correction语义，不对原始exec重复计数；先按account/ticker/con_id和有界历史候选过滤，再批量读有效成交，禁止每条历史都全账户扫描。优先复用既有索引，确有查询计划证据再加一个必要索引，不引入缓存层。

在首次Trade输入构造时一次冻结成员、用于入选的成交证据和最终投影。后续重试不因后来成交而扩充；原始成交发生在cutoff后绝不入列。延迟对账的过去成交可以在首次冻结时作为当时系统已知证据使用，但不回写已冻结旧Case；回测只用数据集原时点证据，缺失不读取生产当前累计filled_qty补齐。当前te_fills无统一“首次观测时间”列，本轮不声称能精确重建任意历史时点的知情集合。

RECENT_TRADE信息标签与recent_trade共享上述48h实际ENTRY成交的冻结成员集合、cutoff和成交有效性规则，不再另查72h历史意图。标签仍要求**当前Case与该集合中至少一个历史Case实际命中的同类对象相同**：真实Event身份相同，或Policy HIT的稳定Policy身份相同；仅RELATED、用于Prior的Event引用或文本相似不算匹配。身份匹配证据在后台保留，不增加recent_trade的模型可见字段。

因此recent_trade为空时不得生成RECENT_TRADE；列表非空但无上述身份匹配时也不生成。部分ENTRY成交可参与匹配，未成交意图/订单、EXIT成交及截点后成交均不可。两者首次构造时一起冻结，重试不因后来成交或历史状态变化而漂移。标签仍只传达信息，不改变路由、交易资格或触发重复交易拦截；旧已冻结Case不追溯改写。

### 8.4 Operations输入

仅submitted_decision、output_disposition、watch、pins、policy_targets。去掉candidate_eligibility、allowed_reference_event_ids、reference_rule和research_path等旧文件路由外显；通过context_ref按需研究。

policy_targets为精简`[{policy_id,title}]`，使用本Case冻结W2关联Policy及运营快照中可取消WATCH的并集；不再内联完整Policy。SET仍只允许本CaseW2确认的HIT/RELATED且存在于冻结projection的Policy；CANCEL只针对可见快照中那一代WATCH。两种集合不相等，不能把“可以取消一个旧WATCH”误当成“可以SET任意旧Policy”。

Trade的WATCH不注入，但Operations快照可在首次进入运营时刷新；随后重试仍用同一`w3_operations_input`。继续现有并发下的STALE_OR_NOT_VISIBLE、容量2、不自动续期、同目标CANCEL优先等规则，不以Trade方向作为新的WATCH开关。

## 9. w3_research访问裁剪：真正关闭对象来源

在v6签名context manifest中固定`research_scope=d1-d2.v1`，来自Case bundle，不接受Agent自行传scope。保持旧context token与旧契约恢复；旧在途为完成其novelty仍可按旧范围读Reference，这是有期限的兼容，不允许新v6借旧token升级权限。

1. `_install_context`/公共准备只编译D1/D2；版本化research缓存key包含scope，不能命中旧全资料缓存。后端PolicySet/Reference冻结仍供W1/W2、内部执行与审计，不发往v6研究MCP。
2. v6 bind不写policy_usage、events、旧history/runtime state/private evidence到可查询快照，不生成Policy direct_objects。`snapshot_catalog`同样只含获准对象，不能留下旧ID/标题发现入口。
3. `objects`先按**component来源**过滤再组装，不只按kind过滤：source/raw_document是共享kind，必须只允许D1/D2来源。然后对catalog/search/read统一过滤；kind为空搜索也不能扫描被拒对象。
4. `read`明确拒绝policy、policy_usage、reference、reference_event、raw_document的policy/reference/d3别名及未知kind；不能让未知kind回落到policy。`view=raw/sources`和带cursor请求遵守同一限制，禁止来源解释元数据指回被禁原文。
5. v6 MCP保留D1章节、D2 Unit、二者raw/source及既有market_view。取消runtime_state/history/evidence的通用读取旁路；需要的PIN/成交历史已在阶段input按白名单内联，不能再从MCP取回被隐藏的W2理由、方向配置、WATCH或Policy ID。
6. Worker工具描述/目录说明同步改为按需D1/D2，保留Web、行情、IBKR只读等当前允许能力。确认其他data MCP也没有给W3研究Policy/Reference的路径；不更改W2/日维护访问能力，不做全服务全局禁用。
7. 现有context_ref固定了manifest，但HTTP层实际使用共享service bearer；`X-W3-Capsule`用于就绪观测，不是Case权限校验，不能误称已有严格调用者绑定。为避免v6拿旧context_ref重新读出全Policy，复用现有service_token HMAC派生一个固定的v6研究范围凭证，Worker按冻结entry版本选择它，服务端取“调用者范围∩manifest范围”；旧凭证只供旧在途兼容。这样v6即使持有旧Case/旧phase的引用也不能扩大范围，不增加表、每Job令牌或易破坏resident复用的动态header。缓存命中亦经过同一范围裁剪；本轮不顺带重构全套MCP身份系统。

不顺带重做上一轮已发现的文本分页协议；保留现有cursor及完整性校验。此次指令仅要求读相关且未完资料时续页，不鼓励Agent对片段body直接JSON.parse。分页结构优化另行有针对性实施。

## 10. 按文件执行清单与顺序

| 步骤 | 文件/模块 | 具体产物 |
| --- | --- | --- |
| 1 契约和资产 | `w3_contracts.py`、`execution_bundle.py`、`prompts/.../w3/contract.json` | v6两数组Ops模型、版本分派、三资产冻结/校验；占位operations.md，不激活 |
| 2 Case线程隔离 | `w3_threads.py`、`w3_execution._thread/maintain`、`w3_context.py`、`maintenance.py` | 删跨Case acquire/link/sync调用；原子create+stage绑定，固定生命周期，保留恢复与清理 |
| 3 prompt组装/资源 | `service._freeze_inputs`、`w3_assets.py`、`w3.py`及Ready配置 | 三资产冻结但模板仅两份；Ops全文前缀；v5/v6入口并存；Ops不提前物化 |
| 4 输入投影/历史 | `w3_context.py`、交易执行repository只读查询适配、context provider | 显式Trade/Ops白名单、Policy标题映射/cutoff来源、48h成交历史及RECENT_TRADE共享冻结成员 |
| 5 研究裁剪 | `w3_research_store.py`、`w3_research_assets.py`、`w3._install_context`、MCP工具描述/Worker绑定 | v6 scope、所有查询/原文旁路权限；不影响旧bundle |
| 6 事实移交 | `router.py`、`service._enqueue_route_effects/_execute_r3/_refresh_case_completion` | NEW兄弟EMIT_DELTA，W3不等待；R3独立错误/幂等，旧契约不双写 |
| 7 结果提交 | `w3_execution._commit_trade/_commit_operations`、`case_outcomes`、`policy_watch` | v6方向/Archive/日记录确定性，Ops只PIN/WATCH；保留legacy分支 |
| 8 消费者 | Read projector、API detail/evidence、前端映射、`maintenance.py`/D3 feed | 新来源与结果展示，版本化日记录，无“缺novelty=失败”；历史不改写 |
| 9 回测/发布 | `w3_backtest` runner/snapshot/report、bundle发布CLI与manifest | 新Schema、第三资产、独立Case线程和D1/D2范围；新数据集另冻结，不升级历史文件 |

每个重要代码提交追加changelog。实施前取最新main隔离工作树，避免把其他对话未提交的service.py当本补丁发布；逐项对照生产已验收镜像。使用现有正式发布清单与角色锁，不再临时热改运行文件。

## 11. 必须通过的回归与生产验收

### 正确性

- 同ticker相同Policy/同Event的A、B：物理thread_id不同，即使A已完成也不得被B继承；A/B的Trade/Ops各自同线程。模板相同不算失败。
- 在create/stage绑定、prepare受理、Job返回、save_result、业务提交和ACK前后分别模拟崩溃；恢复使用原record/thread/Job/幂等键。未知网络结果不触发新派发；成功结果不被Provider Pause挡住。
- 新Case模板与Trade中无operations.md正文/文件；Ops完整前缀逐字等于冻结资产且恰在JSON前。修改活动operations.md后旧Case仍用旧正文；占位不能被误激活。
- SDK和本地v6 Ops只收两数组；额外novelty/policy/delta/decision拒绝；空数组成功。v4/v5仍按原Schema读取/恢复。
- NEW+HIT+NO_TRADE、NEW+NO HIT+LONG、已合法进入的OLD+SHORT各自保留W1/W2结果和W3方向；Ops失败不撤销方向；NO_TRADE在Ops未执行前已有终态记录。
- NEW进入W3同时仅一个EMIT_DELTA；挂起R3时W3仍立即启动。R3重试不重复事实，失败不重跑Trade；OLD不因W3产生Delta；历史v5恢复不再额外补R3。
- 动作并发：容量冲突、重复SET、过期、另一Case已更新后CANCEL、未知Policy ID、非关联SET；复用当前业务处理，不能因为输入精简放宽权限。
- Policy同名、Condition同名、HIT+RELATED混合、RELATED无Condition、未知cutoff和不完整alignment均有确定性快照。输入对象构造后原Policy/Case冻结内容不变。
- recent_trade：未成交接受单/EXIT/手工单排除；部分ENTRY成交入选；修订撤销及重复exec不误计；截点后成交排除；边界时间、同Case多fill去重、重试冻结不漂移。
- RECENT_TRADE：48h内部分ENTRY成交且Event/Policy HIT身份匹配时生成；仅有48–72h前成交、未成交意图、RELATED或Prior引用均不生成。验证清单为空必无标签、清单非空但不匹配无标签，并验证标签与清单共用冻结成员且不触发路由或交易动作。
- research：无kind catalog/search、关键词命中Policy、直接read、raw、sources、source对象、snapshot_catalog、旧cursor、缓存命中、v6凭证带其他Case/旧phase引用试图扩大范围、编译失败raw fallback各有越权否定用例；D1/D2与market/Web正常。Agent可读task文件及pack目录也不含旧完整payload旁路。
- 新日记录可供O3读取，Read/API同时展示W1/W2/方向；无需W3 novelty也不是“结果未记录”；旧Case四badcase快照及回测Gold不被改写。

### 时效与资源

保留原分段计时，Trade、Operations、完整Case的非SDK口径仍包括所有内部排队、锁等待、重试、退避；真实Provider等待和SDK回合单列。P95≤5秒不以放宽超时、减少实时并发、让R3等待变成“外部时间”达成。

重点检查独立线程增加后的Ready命中/冷启动、每Case准备时间、固定文件重复写入、R3并发对交易响应的影响。不给每Case新建常驻进程或做同步全库扫描；沿用现有Ready/Capsule资源回收。不得以追求指标让Trade等R3批量完成。

### 发布

先完成实现和离线回归；再将提示词方交付的新AGENTS/Skill/operations作为同一v6 bundle校验。占位未替换则不激活，代码可以保留兼容已上线旧bundle。

生产发布按现有正式流程核验Scheduler、Worker、必要的Read/API/MCP镜像与源码hash。新bundle切点之前的在途原Case继续原身份/旧契约，新Case用v6；不批量迁移或重跑旧Case。观察自然流量确认A/B隔离、Ops指令仅阶段注入、R3非前置、两数组成功消费和研究访问边界；回退只影响之后新Case选用bundle，已经受理的v6不能用不识别v6的旧程序消费。

本轮不进行上线、不运行收费对照重跑、不改提示词正文。提示词对齐简版另见`w3_v6_prompt_orchestration_alignment_20261115.md`。
