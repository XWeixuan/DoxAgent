# 消息管线吞吐与长尾修复实施记录

2026-11-18（北京时间）。依据 [实施方案](../dev_plan/workflow_v2/message_pipeline_throughput_tail_repair_plan_20261117.md)。本轮修改、提交和发布限定在消息链路；并行 Runtime/W3 会话的修改保留，由其独立发布。没有改网站轮询间隔、相关性规则、准入窗口、Chrome/Profile 或代理。

## 已实施

| 方案 | 实际修改 |
|---|---|
| P0 文章组 | 准备块最多32个目标，已准备块全部使用；时间预算在块边界判断；按 article 合并不连续目标，消除准备后丢弃 |
| P0 连接与判定 | 准备使用现有 connection_scope；同块正文/source/terms 复用；负例同事务逐项CAS，仍各有独立终态/回执；整批异常回滚 |
| P0 正向提交 | regex 正向结论并入发布事务，覆盖新发布、已发布复用、unchanged、测试及进阶拦截路径，不新增Stream重复 |
| P0 公平/忙闲 | 前台按 due_at 升序，保留生产/测试顺序与后台有限推进；忙时合作式让出，空队列250ms退避；源的60秒等原轮询参数不变 |
| P0 阶段账 | 每分钟固定大小内存计数摘要：prepare/used、块数、批量CAS数、read/prepare/writer等待及墙钟、线程CPU、BEGIN/事务/commit；慢项每分钟最多一条标量时间线，无逐消息诊断落库 |
| P1 旧入口 | 新平面一个30秒级恢复巡检，显式兼容producer即时唤醒；保留旧平面原五ticket/250ms行为。清wake在ready读取之前，避免丢唤醒 |
| P1 BODY候选 | 读取移出writer，提交重检租约/状态、publisher容量及recheck开关；停用recheck和恢复检查使用对应局部索引 |
| P1 维护 | 维护/接纳历史清理降低到60秒；空测试维护不取写锁；空恢复候选不进writer |
| P1 WAL | Collector/Bridge各自的长生命周期store持无活动读事务的WAL锚，优雅关闭；同步工作单元内连接复用，写连接FULL。未跨线程共享写连接，临时只读store无锚 |
| P2 低价值工作 | publisher身份补全限于四类消息模型，移除配置等非消息验证；删除正向PENDING过渡写，保留终态投影和每ticker负例审计 |

没有增加服务、线程池、外部队列、业务表或跨轮全文缓存。仅增加两条局部索引。没有取消幂等、terms/binding/result_ref/revision围栏、模型期限、完整正文要求和最终发布准入。

P2中physical/logical cycle的重复大字段压缩仍属后续：cycles.data_json中的listing验证、winning_request_id、首响及终态证明由observability和hourly maintenance消费，units.head_json另供health/control使用。本轮没有把它们混作可直接删除的快照。可先复用现有引用机制，但须另做跨消费者迁移，不混入已验证的热路径修复。

## 提交与发布

- `8b6026556`：核心P0、索引及初步P1。
- `4957f4859`：独立P1连接/WAL与候选读取。
- `00046c87f`：兼容入口恢复巡检。
- `b940e012e`：恢复静态StorageLane接口及摘要可见性。
- `84fa85404`：复用出版物的判定回执、空闲检查、原兼容模式保持。
- `2dd5f5d64`：区分选中、准备、处理时间，避免把完成时刻当首次选中。

使用现有storage_release固定提交构建/发布。索引通过正式collection migration安装，要求Collector/Bus/BODY同时参与；首次只选两角色被框架拒绝，未绕过，随后按三owner迁移。P0、P1及补发分段保存，不作为同一纯版本观察窗。

本轮只替换消息角色；Chrome Supervisor保持原实例。Site Access被并行会话更新至ec5355dbb，不能把它的变更归入本轮消息方案。没有重建浏览器身份或丢弃原页。首次缓存/旧日志清理仅释放约4.6MiB，其余未使用构建缓存为0；未删除业务库、卷、Profile或回滚镜像。磁盘余量仍需独立关注。

## 验收中发现并修正的问题

线程CPU计量最初把StorageLane._execute从静态改为实例方法，生产SpaceNews防御路径仍按类调用，出现缺kwargs的TypeError。已恢复静态接口，实例计量改为可选关键字，新增真实类调用测试。没有用重试掩盖该回归。

补齐已发表/unchanged快捷路径的regex回执：删除中间PENDING写后，仍必须保存实际正向结论。新增“旧文章从不相关重新判为相关，只恢复原Stream而不重发”回归。

慢项日志准确区分 selected_wall、prepared_wall、processed_wall，并标明deferred_decision；处理完成不是负例批量提交时刻。批量提交及其等待另有聚合计时。BEGIN、commit等是writer墙钟的子阶段，不可与总时长再次相加；CPU也不是可相加的墙钟阶段。

## 隔离能力回放

同一服务器、单CPU上限、768MiB内存、network=none、临时独立数据库，100篇冻结真实正文、10个ticker、1000个目标。输入均使用固定负例定义，测试确定性BODY后路径；BODY已准备完毕再开始计时，不发网站或模型请求。临时容器自动删除，没有挂生产卷。

**最终采用启用SourceOutbox/CDC的结果**，不能省略生产触发器后直接外推吞吐：

| 版本 | 完成终态 | 时间 | 有效吞吐 |
|---|---:|---:|---:|
| 旧486931723 | 1000 IRRELEVANT | 94.147秒 | 10.62/秒 |
| 修复版本 | 1000 IRRELEVANT | 5.675秒 | 176.21/秒 |

约16.6倍墙钟改善；新版本达到隔离回放16/秒门槛。旧版本进程最后因门槛断言退出1，**1000个业务终态实际已全部完成**，属于能力门槛未过，不是执行中断。

修复版准备1000个、实际使用1000个，125个有限文章块/125次否定批量提交（32行分页会分割个别文章），没有平方级准备浪费。prepare墙钟1.842秒、process 0.238秒、decision batch 2.592秒，其余选取/控制/调度约1.003秒，总计5.675秒。此处三段互斥；不能与StorageLane中的嵌套阶段再次相加。

早期没有CDC的机制样本为旧74.17/秒、新410.19/秒，仅保留作对照，**不作为最终生产近似能力**。这些测试数据库小于生产，未模拟相同历史规模、宿主竞争、全部ticker复杂规则、正向比例或Jev成本；不将176/秒当作生产SLA。顺序测试也没有隔离宿主背景负载。

## 真实运行观察

截至UTC 2026-11-17 18:08:33（北京时间11月18日02:08:33），自17:53:25起的新文章cohort：

- 527个ticker目标：422不相关、80无公开正文、23发表、2进阶去重；当时全局PENDING/L2_DUE/WAIT_BODY均无积压。
- 综合BODY后首次终态P95 1.730秒，最大8.092秒；这一口径**包括Jev与发布切换，不是纯SQL等待**。
- 最大8.092秒对应Google/BE，BODY在18:02:01完成、判定在18:02:09完成，跨越18:02:02的Bus切换，维护窗单列。
- 五个Newswire确定性子集460个目标：BODY后终态P95 **1.595秒**，最大 **1.797秒**。该窗流量低于原13点高峰，不据此宣称同强度峰值长期达标。

最终补发之后还需按新起点单列后续观察；上述数据保留原窗，不因补发倒改。验收JSON包含原始标量行及角色实际版本；版本/源码核验和后续窗口另存文件。并行Runtime发布占用标准锁时正常等待，没有越过发布锁。

## 测试与证据

- 跨源/进阶基础组38 passed。
- BODY开关/存储/时效组44 passed。
- collection组21 passed；P1综合组40 passed。
- 兼容/进阶组45 passed；出版回归28 passed；最终出版/进阶22 passed。
- 最后兼容时效组29 passed，文章组最终6 passed；静态兼容组12 passed。
- 各组重叠，不相加；XML和脚本在 `eval/pipeline_throughput_repair_20261118/`。未运行整个仓库全套测试，既有RuntimeJournal失败不在本轮修复范围。

证据：benchmark-cdc-linux.json / benchmark-cdc-baseline.json、publication-regression.xml、p1-regression.xml、static-compatibility.xml、acceptance-*.json、verified-release*.json、phase-totals*.json；seeds为真实正文原始诊断输入，保留本地，不纳入产品Git提交。

## 明确的验收边界与剩余问题

1. 功能回归、隔离确定性吞吐及短期真实流量验证已取得证据；**尚未覆盖两个最终版本、与原高峰同强度的完整自然窗口，也未完成完整交易日资源验收**。不能把短期排空当长期稳定。
2. 本轮终点是正式Bus发布。原站发布日期到采集的延迟、网站正文获取、W1/W2/W3和交易执行SLA均不是176/秒这个指标。
3. Google仍观察到429及解链失败；IBKR出现数据源不可用和无callback_at批次。没有在本轮改变Gateway/账号或将这些源宣称恢复。
4. SourceOutbox大字段与长期历史压缩尚未迁移；磁盘余量偏低，需要继续按正式保留策略治理，不能靠删业务历史或取消耐久写换性能。
5. 后续自然验收仍须分列源输入量、完成量、重开/复用、队列面积与最老年龄；只在同版本相当负载下比较CPU/逻辑读/写入，逐阶段解释剩余耗时。

## 最终库存及后续观察

UTC 2026-11-17T19:06:31.478237+00:00，自17:53:25的新文章cohort终态状态为 {'NO_PUBLIC_BODY': 220, 'IRRELEVANT': 1541, 'PUBLISHED': 90, 'DEDUPLICATED': 5}；当时后处理与WAIT_BODY队列为空。正式Stream新插入 **89** 个，区别于delivery的PUBLISHED状态（含已发复用）。原始JSON保留不同分母，不把复用/直接短消息缺少BODY时钟的记录强行补零。

确定性Newswire子集 **1580** 个目标，BODY后终态 P95 **1.313秒**、最大 **2.479秒**。观察包含18/19点到稿，但18点为发布过渡窗，密度也低于原13点强峰；不把这些记录计作两个纯最终版本同强度高峰。前端历史均值可能继续含旧长尾，未删历史或改写时间戳。

最后源码核验：Bus **2dd5f5d64**；Collector/BODY **b940e012e**；每角色11个相关源码，**33项哈希均匹配对应提交**，进程运行、重启计数0。后两角色无需为仅在Bus执行的复用判定/空闲/诊断更新重复重建。所有代码已进入origin main，验收文档提交不代表再换一次镜像。

SpaceNews的静态调用兼容修复后，已读到18:48:45自然SHARED轮询succeeded、新error为空；这不等同于其所有文章全文均已验收。Google与IBKR问题仍按上文单列。

最后空间复核约 **7 GiB可用、99%使用率**，构建缓存清理为0；调用正式资源协调器后仍未明显释放。没有越过回滚/模板引用保护删除镜像，没有删业务数据。磁盘风险**未解决**，需要后续优先治理，不能将本轮吞吐改善称为长期存储治理已经完成。

### 消费者与投影核对

`v2_read/projectors.py`对live_deliveries按article:ticker投影，对stream_items/stream_members按正式发布身份投影；终态行和Stream保留，正向计算中间PENDING不再单独写入。`collection_plane/observability.py`读取unit_hours和physical_cycles，`maintenance.py`按小时归档并检查物理请求证明。因此本轮保留周期证明，只删除已确认无独立业务价值的正向过渡提交；没有直接删历史字段或把负例审计关掉。
