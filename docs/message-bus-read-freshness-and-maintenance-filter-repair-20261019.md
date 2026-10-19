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

- 本地最终组合回归 **227 passed**。覆盖 Read/API、Policy 方向授权与未知状态、快照重建、真实 outbox、SQLite contention、逐事件可见性/回滚/嵌套连接、登录维护筛选。更新了上一轮 publisher routing 修复后过时的 Reuters 测试断言。
- 最终产品提交 `8806fa32b7f4d87eb9dc287b8b38ba9098962801`，Projector 镜像 `sha256:19c675045ff8cc2b6a59401ee957251bf840635b07d2e88de25574168a365d78`。通过 canonical release ledger 升级单一 Projector，逐文件 hash 与 Git 一致；保留 generation/checkpoints，完成实际追平，不是只重启即称恢复。
- **22:59:19 CST** 自然流量采样：research=12,461/12,461，initialization=138,930/138,930，runtime=2,009,398/2,009,398；Bus 已投影到 3,029,079，不低于先采样的 head 3,029,078（读取间仍有新写入）。Bus 积压归零，未重置/快进 checkpoint。
- 同次采样，9 个 ticker 的最新 poll `last_attempt_at`、`updated_at` 与 owner 一致；9 个 ticker 的最新消息 `stream_published_at` 与 owner 精确到微秒一致。MU 最新消息 22:57:52、INTC 22:57:57；BE 最新仍为 22:26:48，owner 同样如此，不制造新消息伪装恢复。
- **23:01:02 CST** 再次复查，Bus checkpoint=3,029,441，不低于 sampled head=3,029,440；Runtime=2,009,535/2,009,535，另外两路仍追平。恢复后继续自然采集，未重新积压；不是碰巧取得一次 head 相等。
- 生产 API 的 ReadContext/SourceStatus/MessageBaseline 同源 builder 在 MU/INTC/BE 上通过。此次不声称实点用户已登录网页；未改前端或重新发布 Web，页面原有每分钟刷新与 SSE/15min view renewal 保持。
- 真实维护库存 **179 identities → 默认 39，隐藏 140**。ChartMill/Fool/TheStreet 默认隐藏，Reuters/Barron's/WSJ/Seeking Alpha/MarketWatch 保留，advanced 能显示全部。旧 Tkinter 进程需关闭重开以加载新代码；不需要关闭 Chrome 或重新登录。
- 23:01 再验期间自然发现的新 Publisher 使库存增至 **185**，默认仍只显示 **39**、隐藏 **146**；三项漏网例子继续隐藏，证明不是删除或只屏蔽一份静态名单。MU/INTC/BE 的状态 builder 均已返回 23:00:24 的自然轮询成功时间。
- Chrome Supervisor `617b4e1d...`、Site Access `6f24c349...`、Message Bus `786cdb63...` 本轮没有重建；无 OOM、重启次数均为 0。宿主机余量约 55 GiB，未清理业务数据、Profile 或镜像。
- 原有 **45 条 Runtime PROVENANCE_UNVERIFIED gap** 与来源登录/challenge 状态未修改；Bus 没有 gap。本轮解决投影时效，不把待人工鉴权来源伪装成成功轮询。

本地和生产诊断材料：`eval/message_bus_read_freshness_20261019/`。其中运行态完整清单只作本地验收材料，提交仅包含不含秘密的脚本、测试和摘要。
