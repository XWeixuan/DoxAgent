# Message Bus 显示滞后与维护列表过滤修复

## 现象与证据

2026-10-19 14:26:50 UTC 现场：Message Bus owner 持续采集、运行状态页持续有 W1/W2/W3；前端 Message Bus 使用 Read V4 的 `native:poll_states` 和 `message`，不是直接读取 owner。

- Read Bus head=3,022,738，checkpoint=3,007,063，相差 15,675 个传输坐标；MU 投影轮询时间约 13:10 UTC，实际已是 14:26 UTC。不是时区、浏览器缓存，也不意味着业务采集停止。
- 同时 research、initialization、runtime 已追平各自 head。因此运行状态页能显示更新，不能据此证明 Bus 投影时效正常。
- 只读重放 100 条 Bus mapper 的剖析：90 次 ticker health 汇总耗时 8.33 秒，470 次 `ReadStore.get`，约 480 次独立连接。健康状态重复点查放大了 SQLite schema/连接开销。
- 调度每源处理约一秒后让出、每轮再固定休眠一秒；叠加当前状态同步开销，长期消费速度不足。14:28:21 backlog 进一步扩大，证明不是纯粹一次性启动积压。
- 前次全量发布回执还记录了授权头事件逐条遍历三万余 Policy、每个方向反复开连接的长停顿；本次一并消除同类连接放大，保留授权与消费业务语义。

## 实现边界

1. ticker health 使用一次当前状态快照，合并本条事件的新增/更新/删除，再计算状态；保持 ack、暂停、来源启用和失败语义，不读 CDC 历史。
2. SourceOutbox 为每个有界批次复用一个只读连接，不开启跨事件事务，不预加载大正文；仍逐条读取、映射、提交检查点，异常仍进 gaps。
3. productive tick 立即进入下轮，空闲或 SQLite contention 才休眠；实际事件处理的一秒配额不再被 capture/head SQL 耗尽。满批积压时允许至多五秒追赶 burst，同时保留批次数量限制和轮转；未引入第二写者、跳游标或旁路读库。
4. 授权头/方向消费事件及正式激活按 ticker 一次读取方向 usage、head 存在性、未解决迁移集合；事件内共享，不作跨事件永久缓存。unknown、pending usage、迁移阻塞和 LONG/SHORT 独立语义不变。
5. 维护 UI 默认隐藏没有显式验证文章的 `publisher:*`（含具名旧 Publisher），不再只判断 generic。source scope 与显式配置验证的订阅账户保留；待办和未完成会话始终可见，advanced 开关可以查看全部。不删除 Registry/Profile，不改变正文抓取归属。
6. 实际 writer 十秒采样进一步定位 `sync_business_facts` 增量查询扫全目录、`consumption_projection` 为方向型 Policy 做无效旧版判定、graph member 按 Case 全扫描。新增显式 `projection-indexes` 发布迁移：current(kind,valid_from)、contributions(metric,valid_from)、graph member 的 Case 索引、旧版 Policy coverage 部分索引。方向型 Policy 在 SQL 层排除，不改变旧版判定。DDL 仅在停单一 Projector 后显式执行，不放在 HTTP GET、业务事务或普通服务启动里。
7. 加索引后采样进一步发现无业务变更仍每轮争用 Runtime writer：Bus 轮询推进全局 Read seq，被错误当成业务快照需要重新落库；没变化的 source ACK 也反复 `BEGIN IMMEDIATE`。无 business records 且 hit membership 未变时不争 Runtime writer，membership 删除/更新仍即时同步。ACK 在 position/repair 集合变化时即时写入，无变化仅保留一分钟 heartbeat；失败不缓存成功，重启首次仍注册。
8. 连接 schema 缓存局限在单个 Projector tick：借出连接仍每次 BEGIN/COMMIT/ROLLBACK，归还时没有事务或快照，嵌套读取另借连接，tick 完成或异常即关闭。不是批量延后提交、不增加并发写者，不让 API 共享连接；避免每条事件重新解析大型 SQLite schema，保留逐事件落盘可见性。

修复只替换 Projector，并更新宿主机 Tkinter 脚本；不重启 API、Site Access、Supervisor、采集容器或 Chrome，不改轮询频率/30min 时效窗口，不重放业务消息。保留 active Read generation、所有内容引用和 checkpoint。

## 验证记录

待生产发布后追加；不能把容器存活当成投影已追平。验收须比较 owner 最新轮询、投影最新轮询、Message Bus API/消息流，观察持续自然流量下的 lag。

本地和生产诊断材料：`eval/message_bus_read_freshness_20261019/`。其中运行态完整清单只作本地验收材料，提交仅包含不含秘密的脚本、测试和摘要。
