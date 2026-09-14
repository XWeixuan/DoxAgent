# DoxAgent V2 数据库读写与容量治理实施方案

日期：2026-09-14  
状态：设计交付，尚未实施  
本地审查基线：`e911fa55`；工作区已有其他未提交修改，本方案不包含对这些修改的处理。  
范围：V2 业务数据库、原生变更捕获、读投影、API/SSE、工件归档、备份与部署；不涉及 V1、DoxAtlas，不改变交易决策和订单执行权限。

## 1. 目标、依据与冻结边界

依据：用户提供的 2026-09-14 远端诊断、当前 `src/doxagent` 实现、V2 API Contract、前端数据需求及 PRD Part 1 §4.6。远端 4,865,122,304 bytes、单次 Overview 约 7.2 GB 读取、每日约 30 万 commits 等数值来自本次提供的诊断，未在本轮重新运行生产扫描。以下缺陷及依赖关系已按本地代码核实；性能指标均为后续验收目标，不是已测结果。

目标是使空闲开机时间不再直接制造大量业务历史，使常用查询成本主要取决于当前业务集合和结果页，而非累计投影版本数；即使存在慢查询，鉴权、控制和健康检查也不能被同一个同步调用堵塞。

### 1.1 用户已确认的数据保留政策

| 数据 | 保留政策 |
|---|---|
| 正式研究版本、消息正文、Case 判定与归因、Policy/Event 变更、交易及成本凭据 | 长期保留；允许压缩、去重、冷热分层，不因年龄删除 |
| 被上述业务记录引用的原始证据，包括必要的模型输入输出 | 长期保留，保留固定身份与引用链 |
| 未被业务结果引用的重复抓取响应、失败网页、浏览器调试材料、纯技术日志 | 保留 30 天后清理 |
| 未结束任务、未修复故障、显式保全材料 | 保全优先；解除保全并满足年龄条件后才可清理 |
| 页面 MVCC 和 SSE 传输历史 | 至少满足现有 24 小时承诺及有效引用；不等于长期业务审计 |

“未引用”必须由登记和引用关系证明，不能因为某文件未出现在一个表里便判为无用。已失败 Case 的原因、输入和实际尝试仍是业务审计材料，不属于可随意删除的失败网页。

### 1.2 不变的业务语义

- 固定 view 下的列表、指标、详情使用同一读水位；不将历史详情回退到 current。
- 正式版本、Event/Fact 固定快照、首次激活证据、消费事实、幂等键、交易账本继续真实可追溯。
- 保留周末 semantic-day 窗口及交易日指标的区别；不改变 BE-11–14 的 Sweep 结算边界。
- 数值未知不填零，覆盖未知不改 COMPLETE，成本运算不退化为二进制浮点。
- 本期不以牺牲 24 小时翻页或 SSE 重放来换取容量，不新增用户恢复按钮。

## 2. 本地审查结论与改造落点

| 位置 | 已确认问题 | 设计要求 |
|---|---|---|
| `persistent_runtime_v2/journal.py::set/tasks/transaction` | 无条件 UPSERT；读取使用写事务；可选条件 OR 和全历史 fetchall | 读写连接分离；语义差异更新；专用活跃任务查询和 keyset 分页 |
| `persistent_runtime_v2/coordinator.py` | schedule 每轮保存；多个路径枚举历史 tasks/values | 状态改变才落库；按 ticker、状态、sweep、命名空间查询，避免全历史回扫 |
| `v2_control/service.py::_ack/tick/reconcile_initializations` | ack 更新时间导致重复捕获；已完成初始化仍进入对账路径 | epoch 确认与存活心跳分开；变更触发加低频补偿对账 |
| `ticker_initialization/repository.py::expand/_save_node` | expand 保存全部节点并生成事件，即使计划未改变 | 仅保存新增/变化节点；真实计划变化才递增状态与发事件 |
| `v2_read/outbox.py` | UPDATE trigger 未比较变化；整行 JSON receipt；没有回收 | 字段级捕获合同、轻量事件、持久消费水位及归档协议 |
| `v2_read/repository.py::ingest` | 无条件版本化、双份 changes payload；相同贡献再次版本化；空映射仍生成 commit | 原子去重、延迟分配业务 seq、分离源消费与公开版本 |
| `v2_read/health.py::project` | 每次相关输入重算 ticker/navigation；`next(..., store.get(...))` 默认参数提前求值 | 依赖变化触发、批量读取、真正的 incoming 优先查找 |
| `v2_read/projector.py::_coverage` | head/checkpoint/时间变化产生随机事件 ID，持续版本化 coverage | 观测覆盖写与业务覆盖证明分离；只发布语义改变 |
| `v2_read/lifecycle.py::consumption_projection` | 每次 runtime coverage 变化遍历全部 Policy | 按受影响 Policy 和覆盖区间增量处理，保护负证据语义 |
| `api_v2/views.py` | 创建 view 扫 ticker 历史、MIN(commits.at)、读取历史 runtime_tasks JSON | 独立小型目录和数据起点元信息；维护状态专用投影 |
| `v2_read/repository.py::get/page` | 当前和历史共用 valid_from/valid_to 查询；列表索引包含全部版本 | 当前表与短历史独立索引；历史点查定向定位 |
| `api_v2/cost.py/graph.py/runtime.py/bus_metrics.py` | 聚合范围不受控、N+1、多次重复归约、JSON 展开、ALL 无界 | 显式维度、增量桶、节点条件边聚合、批量归约 |
| `api_v2/events.py/policies.py` | 列表前执行大范围 EXISTS/JSON 计算和排序；LIMIT 不限制前置工作 | 存储排序锚点、关系与过滤索引，列表先找身份再取正文摘要 |
| `api_v2/app.py/streaming.py/graph.py/cli.py` | async 路由/SSE 调用同步数据库；一个 Uvicorn 进程；响应封装也重复读 view | 全链路隔离，不只替换一条 SQL；请求级上下文复用 |
| `v2_read/maintenance.py` | GC 一次少量删除、commits 永久保留、缺少生产常驻调度；rebuild 重放全部历史 | 持续 GC、检查点恢复、分代迁移和可验证归档 |
| `production_v2.py`、生产 Compose | 每次启动迁移备份全部库并 integrity_check；没有备份轮转/容量治理服务 | 普通启动检查与实际迁移分离；备份、深校验异步治理 |

范围不止 4.87 GB 读库：原生 receipt、Runtime 大 JSON、初始化节点工件、正文、归档与备份都进入容量台账。永久保留的真实业务数据仍会增长，本方案不承诺总磁盘永不增长；承诺消除按心跳次数增长的重复副本，并明确长期存储预算。

## 3. 架构选型

### 3.1 采用本地 SQLite 分层，不在本次迁移 PostgreSQL

本次故障由无效写入、通用历史模型与无界同步查询共同引发。换数据库不会消除重复 receipt、大 JSON 多份复制或 N+1。本期保留 Runtime/Bus/Initialization 等数据库边界与唯一 executor，重构读侧；不引入 Redis、外部消息队列或远程数据库依赖。

只有在完成治理后，实际多写者竞争或容量/并发验证仍不达标，才另立 PostgreSQL 迁移项目。该条件不是本方案延期完成的理由。

### 3.2 逻辑存储分层

| 层 | 内容 | 访问方式 |
|---|---|---|
| 业务执行库 | 活跃任务、控制状态、交易事实、幂等记录、恢复状态 | 现有领域仓储，短事务、明确写者 |
| 热读库 | 当前摘要、短期 MVCC、聚合桶、SSE 小事件、有效 view/pin | API 查询工作进程；单投影写者 |
| 业务历史索引 | 消息/Case/正式版本目录、时间线、历史身份映射 | 有界索引访问；不保留每次投影的技术副本 |
| 内容工件 | 正文、完整模型材料、正式 JSON、证据、归档 receipt | 内容寻址文件、固定大小分块，按身份懒加载 |
| 操作观测 | worker 心跳、源检查时间、队列深度、查询统计 | 覆盖更新的少量行或有轮转的日志 |

第一版热读库内保留摘要和历史目录，避免过早拆出跨库事务。内容物理分离；观测不走通用 MVCC。后续按真实体积将只读历史目录分片，无需改变业务 ID。

## 4. 写入治理：在源头、捕获、投影三层止损

### 4.1 业务仓储

建立每种状态的语义字段清单：控制 epoch、状态、路由、错误、最终结果属于业务变化；lease_until、最近探测时间等属于运行观测。不能全局删除所有时间戳，真实发生时间和首次激活时间必须保留。

- `RuntimeJournal.set` 使用规范化编码，只有 payload 不同才 UPDATE；schedule 无变化不写。
- `_ack` 按 ticker/epoch/consumer 持久确认一次，状态改变才更新；last_seen 单独覆盖写，不制造新的业务确认事件。
- 租约续期仍然持久化和 fencing，但 renewal 不触发面向前端的全量领域投影；实际超时、失败和状态切换必须捕获。
- 初始化 expand 仅插入新节点，冲突仍报错，不重存成功节点；progress 只有展示值改变才更新。
- 对账改为待对账索引加变更触发，保留低频、分页补偿扫描；不能因为关闭高频扫描而失去故障恢复。
- `tasks()` 拆成只读点查、活跃任务、到期任务、指定 sweep 成员、历史分页；移除读路径 `BEGIN IMMEDIATE`。事务内需要 fencing 的读取继续使用原连接。
- 查询的过滤字段实体化；例如 `sweep_id`、namespace、status、due_at，不依赖每秒展开历史 JSON。索引与 SQL 同步重写，避免 `(? IS NULL OR ...)` 让索引失效。

### 4.2 Capture 协议升级

为每个捕获表登记：业务字段、运行观测字段、事件身份、删除语义、重建方法、保留分类、外部内容引用。UPDATE trigger 使用 NULL 安全差异条件；只变更观测字段不生成完整业务 receipt。

变更业务状态的事务必须同时写入 outbox。不要将 outbox 写入改为“业务提交之后尽力发送”。INSERT/DELETE 及真实状态变化不得被采样合并。

迁移后的事件至少包含：`source_epoch/source_seq/table/entity_id/operation/schema_version/recorded_at/payload_ref`。小状态可以内联；大 payload 用固定内容引用。业务审计事件和供投影追赶的 transport receipt 分类记录。

原生 `source_seq`、消费者 checkpoint 与公开 `read_seq` 是不同坐标：无效原生事件被跳过，也必须推进消费位置。保留每源 epoch、durable head 和 retained floor；回收后不能用现存行的 `MAX(seq)` 推断历史最高位置。

各类数据具体按以下方式裁剪捕获，不能直接停掉整个 native 表投影：

| 原生数据 | 捕获与物理存储方式 |
|---|---|
| runtime_values.schedule、control ack | 语义状态变更事件；心跳独立覆盖更新 |
| runtime_tasks、enrichment jobs | 小型任务状态、lease、owner 与大 inputs/receipt 分离；租约写保留但不复制全部任务内容 |
| runtime_v2_cases/turns、worker requests/receipts | 当前执行状态保留结构化字段；不可变输入、轮次结果和模型材料按版本引用，不每次保存完整累积 JSON |
| initialization_nodes/runs | 状态摘要与不可变 result/worker invocation 明细分开；按实际修改节点捕获 |
| standard_messages/raw_messages | message revision 与正文内容地址分开；首次发现、去重 hash、原始时间、准入及引用身份保留 |
| trade_intents/receipts、te_* | 真实业务事实全部保留；只对确定相同的重复持久化抑制，不合并不同 attempt/fill |
| model_usage_events、调用凭据 | 调用身份与金额更正保留；大诊断附属材料按业务引用分类 |

大字段迁出业务库必须提供版本化编解码器：先支持旧内联格式与新引用格式双读，再切写，最后离线迁移旧行。领域模型拿到的语义数据保持一致；只有按指定任务/Case 加载时才解引用，禁止 `tasks()/values()` 为了列状态批量还原所有大材料。该步骤与只改读投影是两个独立工作项。

### 4.3 投影原子去重

在一个读库写事务内执行：

1. 检查源 epoch/事件身份、恢复 gap 和实体水位。
2. 比较候选记录的业务 payload **以及** sort、parent、day、source_id、route、search 等查询字段；忽略仅由投影生成的 row_revision/revision。
3. 无变化：只推进消费/实体水位及必要 gap 状态，不关旧版本、不插入新版本、不生成 SSE。
4. 有变化：分配一个公开 read_seq，原子写当前状态、历史区间、贡献与聚合、必要流事件和检查点。
5. contributions 比较该实体完整贡献集合，包含 day/dimensions/value；仅对真实改变做旧值扣除和新值加入。相同数值但日期/维度改变不能跳过。

投影中的 read-modify-write 必须看到本事务先前结果。批量处理可以共享连接、合并健康派生，但不得跨真实领域事件合并掉 NEW→FAILED→RECOVERED 等业务过程。mapper 中当前存在独立 `put_content()` 写入，迁移为预写不可变内容加提交引用，失败产生的孤儿由宽限 GC 处理。

无变化不再创建 commit 后，`highwater()` 改读小型元表；幂等保障来自原生消费水位、实体水位及受保护的 gap，不依赖永不删除的 commits。乱序 repair 不能覆盖更高实体水位，且不能因为跳过了旧实体更新就错误地清除未修复依赖。

## 5. 热读 schema 与快照查询

以下为逻辑 schema，实施时使用明确版本化 migration，不在 GET 或普通启动中建表。

| 表/结构 | 主要字段与约束 |
|---|---|
| `read_meta` | generation、schema、last_seq、first_business_at、retained_seq_floor、projector_version |
| `object_current` | PK(kind,ticker,id)，version_seq、摘要 payload_ref/小 payload、查询字段；每身份至多一行 |
| `object_history` | PK(kind,ticker,id,valid_from)，valid_to、历史查询字段、内容引用；仅保留受保护区间 |
| `business_revisions` | 正式业务 revision/run/snapshot 身份、内容引用、原始时间、来源证明；长期保留 |
| `read_commits` | read_seq、projected_at、source coordinate；仅保留快照/重放/修复所需记录 |
| `stream_changes` | seq、ordinal、stream kind、ticker、entity_id、before/after version refs、action；不复制整份 payload |
| `contribution_current/history` | 实体、指标、日期、维度、精确值，支持更正 |
| `bucket_current/history` | 指标组、ticker、日期/ALL、显式维度、精确值；版本语义与 view 一致 |
| `capture_proofs` | source epoch、表集合版本、覆盖区间、水位、gap 边界；不等于心跳 |
| `views/cursors/retention_pins` | owner/scope/generation、seq、必要状态、expires_at；独立到期索引 |
| `source_health` | 每源少量覆盖更新行，checked_at/head/checkpoint/error |

### 5.1 当前读取与历史点查

未指定快照的内部读取直接命中 `object_current` 主键。固定 seq=S 的点查：current.version_seq≤S 时可使用 current；否则从历史按 `valid_from DESC LIMIT 1` 找最近版本，再检查有效期。必须先选最近版本再检查删除区间，防止“找不到当前版本”时意外复活已删除对象。

当前状态小表先覆盖 ticker、navigation、activation、initialization summary、source/binding status。大原生 JSON 不进入这些表。

删除要保存有版本坐标的 tombstone 或等价删除边界；不能用“没有 current 行”区分从未存在与已删除。典型历史点查索引为 `(kind,ticker,id,valid_from DESC)`；当前列表按实际路径建立 `(kind,ticker,sort_key DESC,id DESC)` 及来源/父级专用索引。历史区间回收使用 `valid_to` 索引。不可把上述全部索引无差别复制到每个长期原始 payload 表。

### 5.2 快照分页

不能因为 view 刚创建就无条件读最新 current：创建 view 后到列表请求之间仍可能有更新。

按 S 查询两个互斥集合：current 中 version_seq≤S 的记录，以及 history 中 valid_from≤S<valid_to 的记录；所有筛选和 keyset 条件作用于对应版本。分别排序取 limit+1 后合并取最终页，前提是该路径已证明每身份至多一个有效版本、两个集合互斥。不能先按当前字段过滤再找历史，也不能先 LIMIT 再做业务筛选。

热索引按对象类型定制，不再对每类大 payload 建同一组通用索引。历史索引只服务点查及有限快照窗口。页大小限制仍保留，但验收同时检查扫描行数及临时排序量。

### 5.3 Read Context

- Overview 读当前 ticker 目录，固定其成员与 seq；单 ticker 页面只校验该 ticker，不枚举全目录。
- `first_business_at` 独立持久化，不从即将回收的 commits 推断；迁移时使用已有业务证据建立，不拿重建时间代替。
- maintenance_pending 使用按 ticker 汇总的小投影，不扫描历史 native runtime_tasks。
- 同一请求复用 view、generation、coverage 和授权上下文；`respond()` 不重复查询。
- view/pin 注册与所读 seq 的保留确认原子完成。若采用独立查询工作进程，注册仍由热库受控短写事务完成，不能先读旧 seq、让 GC 删除历史、最后才登记 pin。

## 6. 页面查询与聚合重写

| 页面/能力 | 实施方式 |
|---|---|
| Overview/navigation/status | 小目录与当前状态点查；多个 ticker 批量读取，避免每项重复打开连接和全库健康检查 |
| Runtime metrics | 一次取得所需指标组及前后两个窗口，复用完整性证明；不逐指标独立查询 |
| Graph counts | 显式 metric group/枚举值，按 ticker/day/node 索引；最近处理时间维护专用当前值 |
| Graph node paths | 将 Case–node–edge 成员关系实体化；维护“包含指定 node 的 Case 的全部路径边”聚合，不能只统计接触该 node 的边 |
| Cost summary/nodes | scope/provider/model/node 显式列；先分页节点身份，一次 GROUP/精确归约该页全部节点，消除每节点 totals 重扫 |
| Cost trend | 日/月聚合层级按 max_points 选择；不加载全部 invocation 后压缩；跨月边界使用准确的边界日补齐 |
| Message Bus metrics | 消息、正文尝试的关联与分母定义在投影时登记；按来源/类型/路由/日期汇总，保留尝试时间与消息时间的现有区别 |
| Policy | shell membership、生命周期、消费、命中等实体关系索引；按 ACTIVE 或生命周期候选集合选身份后取摘要 |
| Event | occurred_at 排序锚点在投影时计算；生命周期变更索引先选候选身份，避免每请求 Python SQL 函数加全目录排序 |
| Research/Expectations/Reference/Case Detail | 保留固定身份读取，完整内容迁为引用，不重复镜像所有原生大 JSON |

指标使用明确单位及 Decimal/确定精度整数；禁止用 SQLite 浮点 SUM 处理 USD。distinct Policy/Case 不能把日 distinct 简单相加；使用业务身份成员关系与精确去重。常用有限维度预聚合，不枚举全部维度的指数级组合。

`ALL` 对普通指标走全期桶，列表仍 keyset 分页；关键词筛选不能直接套用未筛选桶。保留现有子串匹配语义，建立可验证的候选索引并最终精确匹配，不能悄悄改成全文词匹配。极端关键词/复杂全期精确聚合进入有期限、去重的后台查询任务，页面显示计算中并局部取得结果；不返回假零或不完整的“精确值”。此扩展只用于确实无法在普通预算内完成的路径，需要同步 API DTO 与前端状态。

## 7. Coverage 与 Policy ACTIVE 保护

将 source_health 的观测时间、队列位置与业务 coverage proof 分开。每次 head 变化不再生成整个 capture_coverage 对象；proof 记录已证明闭合的区间、表集合和 source epoch，历史闭合区间不会仅因当前临时落后而消失。

读取时按固定 view 的消费事实及 proof 边界计算状态；缺少当前所需覆盖仍为未知。首次激活在完整消费覆盖区间内且该 view 水位前无消费才可判 consumed=false；不能用“曾经完整”推断任意后续水位仍未消费。

增量触发只来自首次激活、新消费、相关 gap 开闭、覆盖首次跨越待证明边界；对待证明 Policy 建索引，不随每秒观测遍历全部 Policy。已有消费事实优先，effective=false；历史 admission 不得因重建、BACKFILL 或 source epoch 切换改成“刚激活”。

用户已见的 ACTIVE 生命周期修复、历史未知、MESSAGE_MONITORING 不消费规则必须作为迁移对比项。

## 8. API、SSE 与超时隔离

### 8.1 请求执行模型

API 事件循环仅负责异步网络、鉴权、路由和 SSE 连接管理。所有同步 DB/工件读取、DTO 组装和大 JSON 解析移入受控查询执行层；覆盖 middleware、respond、stream next 等隐蔽入口。

初始部署采用两个独立查询工作进程，每进程一个在途业务查询，有限排队；大导出/复杂后台聚合使用单独低优先级进程。进程数只是可调整起点，不能随 CPU 数无限扩大以放大磁盘争用。SQLite 连接在所属进程/任务内建立，不跨进程传递；写入通过明确的短事务通道。

普通查询初始总预算 2 秒，复杂详情 5 秒；队列等待计入预算。使用 SQLite progress handler/interrupt 配合连接生命周期管理。外层 HTTP 超时不能留下失控查询；取消后仍不退出的只读工作进程可回收，D 状态 I/O 未结束时占用仍计入容量，不无限补进程。查询取消不得打断业务 executor 的写事务。

超限返回明确、可重试错误；页面保留已加载内容，禁止无限立即重试。`/auth/config` 不访问业务库；liveness 不执行数据扫描，readiness 检查有严格预算，不能用自动重启反复触发全库备份。

### 8.2 SSE

- 只为 message/case/graph 等实际订阅对象生成流事件，技术 native 变化不写大 before/after。
- 保留 before/after 版本引用，保证 REMOVE 和跨筛选变化可恢复；引用对应版本在重放窗口内不得 GC。
- Graph cursor 从完整 Case payload 改为身份与 revision，避免每个客户端每次事件再次存一份列表；必要时引用受保护的服务端基线。
- 同 ticker/订阅组共享变化探测，客户端保留独立 owner/scope/cursor；空闲连接不各自每秒扫描历史。
- 只计算变化节点及边；是否合并传输事件需保持有序、可重放和最终状态，不能合并掉审计事实。
- 每次扫描按提交边界和字节双重限制；一个大提交不可静默截断。单提交超预算时使用稳定 ordinal 分片，或返回契约规定的局部 reset。
- 基线与流起点同水位；旧 Page 不随 SSE 移动。24 小时内断线重连、重复、scope rolled、过期均维持现有语义。

## 9. 内容工件、引用和保全

新增内容登记及引用表：`artifact(id,sha256,size,codec,location,class,created_at,verified_at)`、`artifact_ref(owner_type,owner_id,artifact_id)`、`retention_hold(scope,id,reason,released_at)`。引用不跨授权暴露；内容 hash 不是绕过 ticker/owner 校验的下载凭证。

文件先写临时位置，校验 hash、flush/fsync 后原子发布，再提交数据库引用。崩溃最多留下未引用文件，不允许数据库指向未完成文件。删除采用 MARKED→重新检查引用/保全→移入隔离区→最终删除，记录 manifest；并发新引用必须与回收状态互斥。

正文与模型材料使用固定大小压缩块或可定位块索引；现有 content byte offset 保持未压缩内容坐标，不能为了取末尾一块解压数百 MB 全文。hash 对原始规范内容计算，压缩算法变化不改变业务身份。

新材料入库即分类；既有材料逐项扫描引用并登记。不明用途文件先保留、报告，不默认归入 30 天清理。业务原始输入/输出可脱离热 SQLite，但 Case/版本详情仍能按身份透明读取。

文件存储和引用表没有跨介质原子事务，必须以“先可靠发布文件、后提交引用”及孤儿宽限解决。备份 manifest 同样拥有内容引用/保全；数据库备份仍可引用的工件不能先被删掉。归档数据若移到另一磁盘或服务，先验证可读和 hash、更新位置，再释放原副本，不能把单纯移动文件当作完成灾备。

## 10. 持续 GC、归档与恢复协议

### 10.1 读侧回收

常驻 maintenance worker 每分钟尝试短批次回收，独占 maintenance lease，低优先级运行。采用时间预算（初始单事务 50–100 ms、单轮 2 秒）而非固定只删 500 行；自适应批量以扫描效率和写锁等待为反馈，不与投影抢占磁盘。

分别计算：MVCC floor、SSE floor、有效 view/cursor pin、未解决 gap/repair pin、shadow/bootstrap pin。SSE 前镜像需要 seq-1 或显式 before version，必须保护。到期索引、valid_to 索引及 seq 索引支持分批回收。

保留窗口从本地可见/投影时间计算，不能只按源事件 recorded_at：旧事件今天被投影后仍须能够重放。GC 删除 changes 后才判断版本是否无引用；清理 commits 前确认 view、cursor、SSE、修复和 as_of 查询不再引用，并将最低可读水位持久化。

游标不得通过无限续期意外将任意古老快照永久钉住。实施时明确父 view 生命周期；现存仍有效 token 的引用全部保护，不能在迁移中缩短已经签发的有效期。超限活跃连接/新 view 使用准入限制，不驱逐有效快照换容量。

### 10.2 原生 outbox/receipt 回收

回收条件同时满足：所有登记消费者已确认、无待修复 gap、无重建/备份 pin、归档或可恢复检查点已验证、超过 transport 保留窗口。第一版 transport 热保留 7 天作为运维恢复余量，不等同于永久业务证据保留；有业务引用的 receipt 内容转入长期工件及来源索引。

检查点包至少含：source epoch/head、当前必要原生状态、业务历史身份目录、entity/contribution 水位、投影版本、首次 admission/消费证明、分类 manifest、未完成任务及内容引用。恢复以“检查点 + 后续增量”进行；当前 `rebuild()` 从头播放全部 receipt 的方式仅保留为兼容恢复工具，不能作为未来唯一灾备路径。

删除 outbox 时维护独立 durable head；`SourceOutbox.event(seq)` 支持归档定位，使 history import 和审计仍能验证原 receipt hash。不可访问所需历史时明确缺证，不伪造原始 INSERT/BACKFILL 关系。

### 10.3 30 天诊断材料

按创建时间到期，同时满足无业务引用、无活动任务引用、无保全、无待归档状态；last_seen 不应被重复抓取无限刷新而导致永不到期。保留首见/末见计数摘要以供运维，但不重复保留等价正文。清理前后核对数量、字节及 hash manifest。

### 10.4 备份与物理空间

普通重启只检查 schema 和配置；仅确有 migration 时备份受影响库。日常一致性备份与深度 integrity_check 由独立维护任务运行。备份轮转建议日备 7 份、周备 4 份、切换前备份保留到回滚窗口结束；删除备份前确保至少一份可恢复备份及长期归档完整，保全优先。

SQLite 逻辑删除不等于文件立即变小。稳态可配置增量回收并小步执行；存量首次压缩走影子库。监控热库、历史工件、WAL、空闲页、备份各自大小及增长率，不能只盯主 DB 文件。

## 11. 存量迁移与回滚

### 11.1 分阶段切换

1. **立即止损版本**：原生无变化抑制、投影去重、当前点查、Read Context 修复、同步查询隔离与预算。先减少新垃圾和恢复可访问性，不先跑全库 VACUUM。
2. **建立回滚与引用目录**：在一致性备份上做容量与引用盘点，核对业务记录、Policy proof、Case 固定版本；线上只运行小范围元数据检查。
3. **创建新 schema 影子库**：导入当前状态、长期业务目录、仍受保护的 MVCC/流历史、views/cursors、generation/seq 和来源水位；消除等价版本与 payload 重复。
4. **追赶增量**：迁移开始即保护来源 outbox。相同 source coordinate 在新旧投影幂等应用，不让 GC 越过 shadow pin。对比真实业务不变量，不能仅比较对象行数。
5. **受控短切换窗口**：暂停 API 新 view/控制命令、投影和必要业务写者，等待在途事务完成，取得跨库一致 source 水位向量，追平影子库。关闭旧 API DB 连接后原子更新 alias，再启动对应 API/投影版本。
6. **验收后开启回收**：先观察 no-op 比例和读预算，再启用读侧 GC；归档恢复验收后才启用原生 receipt 和诊断文件删除。

多个 SQLite 的普通逐库 backup 不是跨库原子快照。在线复制只用于预热；最终切换必须以停写栅栏或等价一致水位协议闭合。

### 11.2 有效 view/cursor 的处理

默认保持现有 24 小时承诺：影子迁移保留旧 generation、有效 token 及其引用的原 read_seq/必要版本；单独用 storage/schema generation 表示物理布局升级。公开 read_seq 不重新编号，去掉空 commit 后也不得重用其编号。

受保护的 message/case 行及历史 revision 数值不能因归并发生变化；只压缩其共同 payload 存储，待 pin 到期再合并不再可观察的等价区间。新公开 seq 从旧最大值之后开始。

若发现无法完整保留旧 pin，切换应延后到该窗口安全结束，期间用止损版本服务；不能无提示强制使仍有效 token 失效。灾难恢复造成的数据代际变化才按既有 reset/410 合同处理。

### 11.3 回滚

保留旧库只读备份、旧镜像、切换 manifest 和 source 水位；业务执行库不回滚到旧快照，避免重复订单/任务。新源捕获格式要有兼容读取期。切换后已有新业务写入时，不能直接把旧读库指针切回：必须用兼容投影追赶到当前水位，或保持新数据、回退兼容应用版本。

有订单接管中时不得为了维护取消/重放订单；停新分析与订单执行恢复分开。迁移和验收不触发真实 Live 订单，也不通过重新执行历史研究构造验收数据。

## 12. 实施工作包与交付依赖

| 顺序 | 工作包 | 主要代码落点 | 交付门槛 |
|---|---|---|---|
| A | 观测与止损 | journal、control service、initialization repository、ReadStore、views、API query runner | idle 无业务版本增长；慢查询不阻塞 auth |
| B | 新热读 schema 与查询 | read migrations/repository、views、所有 API SQL | 当前/固定历史双路径正确；重要计划不扫历史大表 |
| C | 轻量捕获与证明 | outbox、projector、health、formal、lifecycle | 幂等恢复、水位、Policy 负证据通过 |
| D | 聚合/SSE | metrics、cost、bus_metrics、graph、events、policies、streaming | 精确指标与基线/重放一致；无 N+1 |
| E | 工件引用与历史目录 | artifacts、contents、领域大 payload、archive manifest | 旧 ID 可访问；引用完整；崩溃不悬空 |
| F | 检查点/GC/备份 | maintenance、production_v2、Compose、运维 CLI | 可恢复后才允许清理；处理速度超过增长 |
| G | 影子迁移与生产验收 | migration CLI、alias、运行手册 | 业务对比、pin 保留、回滚演练、资源预算通过 |

可以先交付 A 缓解事故，但不能把 A 当完整容量治理交付。各包使用独立可回退 migration，不全局重写领域 schema。新增实现建议放 `v2_read/migrations/`、`v2_read/retention.py`、`v2_read/archive.py`、`api_v2/query_runner.py`；具体文件划分可随实现收敛，边界与不变量不得改变。

## 13. API Contract 与前端配套

大部分优化不改变业务 DTO 和 endpoint；同步补充合同的内部可观察语义：

- read_seq 表示可观察投影版本，不承诺等于原生事件数量；no-op 不产生 SSE。
- view/cursor、源保留 floor、generation 与重建行为，明确 410/reset 原因。
- UNKNOWN/COMPLETE 与 proof as_of，健康观测不冒充业务完整性。
- 查询超时/过载错误及 Retry-After；禁止页面自动无限重试。
- 复杂全期查询若需异步，定义精确查询任务的 QUEUED/RUNNING/SUCCEEDED/FAILED、view/owner/scope 绑定、结果到期时间和局部轮询上限；结果仍基于原固定快照，任务必须登记 pin。
- 工件从 SQLite 迁出后，下载、内容 hash、byte offset、授权和历史身份保持兼容。

前端不通过缩短历史窗口或少显示失败记录规避后端问题，不新增全页高频刷新。普通查询显示原有结果，只有明确失败显示重试；计算中与未知数据分别呈现。

## 14. 必要验收与性能预算

不运行无关大回归。使用一套隔离的脱敏/本地复制数据覆盖当前单 ticker 历史规模，并制造有限的变化与错误路径；生产只做短、有界验证。

| 必测项 | 通过条件 |
|---|---|
| 空闲写入 | 重复同一 schedule/ack/节点/投影输入 10,000 次，不新增业务对象版本、贡献历史或流事件；源检查点正确前进 |
| 非 payload 变化 | sort/day/route/source 改变仍产生正确版本；删除、恢复、乱序 repair 不复活或覆盖错误版本 |
| 快照与 GC | 开 view 后更新、删除、翻页、清理：旧结果稳定；有效 pin 的 seq-1 与内容仍存在；过期明确 410 |
| SSE | 基线到建连间新增、断线重放、REMOVE、跨筛选、超大提交均无静默遗漏，无无效全图重复计算 |
| Coverage | 捕获前激活保持未知；覆盖后首次激活无消费可证明 false；新增消费 true；gap/重建不制造负证据 |
| 指标 | 日期迁移、重复与更正、跨日 distinct、筛选正文尝试分母、Cost 精确金额与旧业务真值一致 |
| 隔离 | 强制慢只读查询时 auth/config 与控制准入仍响应；取消不遗留无界队列或进程 |
| 存量迁移 | 正式版本/消息/Case/交易/成本及引用数、hash、时间、业务 ID 一致；受保护 token 可继续使用 |
| 回收恢复 | 从检查点加增量恢复；30 天未引用材料可删，被引用/保全/活跃材料不可删；归档失败不得删除 |

初始生产目标（在与当前服务器相当的资源上测量）：

- `/auth/config` 服务端 p95 ≤100 ms；Read Context/状态点查 p95 ≤250 ms；普通首屏列表/指标 p95 ≤500 ms。冷缓存另外报告，不能只挑热缓存结果。
- 普通请求 2 秒内完成或明确失败；常用点查/首屏不得读取数百 MB 历史，当前一 ticker 验收目标每请求磁盘读取 ≤10 MB。复杂内容下载不套用摘要预算。
- 全套必要请求同时运行，事件循环延迟 p99 ≤100 ms；队列长度有硬上限，控制和 SSE 心跳不被大查询饥饿。
- 等价内容导致的 objects/changes 增长归零；业务提交速率随真实变化量增长。空闲 1 小时后热库经 GC 的存活记录量稳定，允许有界观测行覆盖和 WAL 波动。
- GC backlog 在正常写入下可下降；监控 oldest reclaimable age、每轮扫描/删除行数、阻塞 pin、字节/日。积压超过 2 个保留窗口报警，磁盘余量不足以容纳影子库和回滚备份时禁止开始迁移。

所有预算作为验收门槛，若失败需定位查询计划、读取字节、线程/进程与写锁等待，不通过增加 Nginx 超时或取消完整性语义“达标”。

查询计划验收以热/历史路径分别建样：单身份十万等价旧版本、多个真实历史版本、删除后新建、空范围、深分页。记录 EXPLAIN QUERY PLAN 与实际扫描工作量；禁止只以“使用了某个索引”认定有界，也不能仅凭存在 SCAN 字样否定对十行小表的正常读取。扩容测试只需一组规模对比，确认历史版本数增加时当前点查与首屏不会同比劣化。

## 15. 运行与交付清单

实施交付必须包含：schema migration、源捕获字段清单、热/历史查询矩阵、聚合定义、引用与保全规则、检查点格式、影子迁移/回滚 CLI、Compose maintenance 服务、环境变量模板、备份轮转、故障运行手册和必要测试结果。

建议新增配置统一使用 `DOXAGENT_V2_DB_*` 前缀，覆盖查询并发/队列/预算、MVCC/SSE 保留、transport 保留、诊断 30 天、GC 预算、归档路径与磁盘水位。24 小时下限及长期业务保留不能被普通环境变量误改为危险值；启动时验证互相依赖的保留参数。

运维页面/日志输出：各层字节数及增速、真实变更/no-op 数、source lag 与 gap、查询 p95/取消、执行计划异常、GC backlog 与 pin、归档校验失败、最近成功恢复验证。日志只记录查询模板、计数与耗时，不记录 token、完整 prompt 或业务正文。

本方案实施顺序为：先止损恢复可用性，再建立新读模型与精确聚合，最后通过可验证的迁移和回收控制长期容量。业务数据的长期可追溯性与热查询的有界成本同时作为完成条件。

## 16. 技术依据

- [SQLite Partial Indexes](https://www.sqlite.org/partialindex.html)：现有 head 部分索引必须与实际查询条件匹配；增加索引本身不能保证历史范围查询使用它。
- [SQLite Interrupt](https://www.sqlite.org/c3ref/interrupt.html)：查询中断在最早可执行时生效，连接生命周期与事务回滚需要管理，因此不能把外层超时视作底层工作已停止。
- 本地业务约束：`DOXAGENT_V2_API_CONTRACT.md` 的快照、历史身份、24 小时 SSE 和 Egress 章节，以及 `DOXAGENT_V2_FRONTEND_PRD_PART1.md` §4.6。上述文档在实施时同步更新；本轮不宣称合同扩展已实现。
