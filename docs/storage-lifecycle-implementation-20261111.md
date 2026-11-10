# 资源生命周期优化实施记录

依据 [执行方案](doxagent-storage-lifecycle-optimization-plan-20261111.md)，2026-11-11。独立工作树 `D:/DoxStorageLifecycle20261111`，分支 `codex/storage-lifecycle-20261111`，以实际生产代码 b4f18aca 为起点，再合并最新 main 业务更新。原始 C 盘脏工作区没有被覆盖。

## 实现与边界

| 包 | 实际实现 | 保留边界 |
| --- | --- | --- |
| R1/R2 | 强制既有时间覆盖索引；剩余预算传递、独立阶段提交、持久游标、GC receipt；Acquisition 存在时停止旧 poll 双重投影，48h 技术头通过常规 ingest 关闭 | 有效 view/cursor 的 MVCC 前像、业务定义、精确 gap 保留；未因索引数量而盲删索引 |
| A1 | 小时摘要与物理退出分阶段提交；先取有限物理候选再查引用；游标绕过大量受保护前缀；每轮至多 128 unit-hour/2s，512 logical candidates/小时 | 原有精确 cadence 算法、SCHEDULE/DISPATCHED、PENDING captures 保护；异常超大单小时可能触发预算，应通过 receipt 诊断，未改成有损近似摘要 |
| D1 | 大 prepared/base/final/handoff 字段复用现有 Native CAS，减少反复 JSON 写入；发布结算独立于 PARTIAL 质量；结算退役后由 staged 权威成果恢复结果视图 | 保留发布版本、accepted-rearm/coverage/身份及审计；退休 run 禁止从已删过程输入续跑；不新建资产平台 |
| D2 | Worker 独立 5min owner；每轮 job 候选页256，最多退出32 workspace；已交接维护输入24h，generation retirement fence；大文件退出在短事务外 | 保留真实恢复 hold、source/adoption/citation 指向的产物、唯一输出；已校验相同 canonical output 才作为重复副本退出；W3完整上下文复制仍暂缓 |
| T1 | root 候选/引用范围索引、pending 续批；廉价过程退出不等待 W3 空闲，重压缩可让步；已发布维护 frame24h 后退出重复内容，保留 base revision/冻结选择/哈希 | Case/Effect/交易、活跃W3、被引用 Event 版本保留；原任务不可原地复活 |
| Native | 显式 owner exit 进入既有队列；继续全 owner 对账、snapshot lease、其他 artifact_ref/hold 与5min quarantine | 不把“退一个owner”当“删除共享内容”；老无主内容仍有7d orphan grace |
| Runtime CDC | producer 不再为 runtime_tasks 捕获 inputs/receipt，前端仍有状态进度，源任务保有原始上下文 | 修正了此前仅 reader 跳过、producer 仍写重复载荷的缺口；历史传输按既有 ACK/pin 退出 |
| I1 | 新 snapshot 写 initialization_id；旧 marker/attempt/唯一 run 前缀映射；有限页和游标记录 unknown | 当前、必要前驱、rerun pin、真实 manual-resume hold 保留；不擅自取消旧失败恢复责任 |
| S1 | W3 registry DELETED 的 fork 可退出；拆开已退休 fork 的模板继承边与真实 subagent 父子边；SDK全树维护6h且空闲运行 | 活跃模板/分支与真实子任务链保留，不把SDK全树查询放入热派发 |
| B1/B2 | 操作备份 helper 同次复用一致 SQLite 快照，验证新恢复点后退旧；Site owner 每10min 轮转16 profile，7d旧白名单缓存退出 | helper不等于完整Native/profile灾备；手工任意cp不自动纳管；浏览器身份和活动/人工恢复 profile 保留 |
| 发布/容量 | 复用现有release owner，精确纳管遗留镜像tag；容量目录分组独立轮转，超时保留上次完整值；worker/acquisition/read receipt 汇总 | 不全局prune、不删volume；现役/回滚/其他引用镜像保留；40/20GiB风险提示不自动停业务 |

## 验证

复用既有 Python 环境，所有新增测试临时数据位于 D 盘。`eval/storage_lifecycle_20261111/` 含 JUnit 证据：regression 95、pipeline 101、release-cases14、source-final70、native-final30、core-final86、freeze13 均通过；各组重叠，不能相加为独立测试总数。覆盖百万 Read 账本、10万保护物理记录、阶段失败已提交结果、MVCC前像、共享Native引用、D3回滚、已结算PARTIAL工作区、浏览器身份、Runtime冻结选择和producer薄回执。

旧 `test_bus_snapshot_rebuilds_message_search_from_thin_body_reference` 的 inline正文断言在干净 main ace2b960 同样失败；仅将测试适配既有 `@body`/content_search 合同并验证分块后的实际检索，没有借机改产品搜索。C盘最初临时目录不足的一项测试已转D盘完整重跑通过。Ruff F/E9 与 diff whitespace 检查在发布前执行。

## 发布与回滚

使用 `scripts/ops/storage_lifecycle_release_20261111.py`：逐角色校验当前镜像与源码，再以各角色实际镜像为底构建 COPY 层，保留已有业务补丁、环境和依赖。先构建；排空已接受模型任务后替换Worker，不取消模型任务，不改并发。通过现有 release health/promote/state 管理现役及前驱组合。源码 commit 与实际角色 hash 见最终生产回执。

D3 表示升级期间保留 `.d3-native-inputs-disabled`，待所有reader/writer上线后激活；旧镜像回退前须排空D3 writers，再在新镜像执行：

```sh
python -m doxagent.workflows.codex_document3.state_v21 --inline-for-rollback /data/research/research.sqlite3
```

该命令开启禁写新格式门并逐行恢复 inline，之后才能恢复旧镜像；已经退休的重复输入不复活，权威成果继续保留。此要求也写入宿主 release state。

## 生产验收（待补现场回执）

本节将在上线后记录实际owner推进、磁盘df差分和一次物理回收。此前应急释放40.105GiB属于上一阶段，不计入本轮修复收益。逻辑删除/Native释放/数据库freelist/df不得重复计算。

24h、72h、7d自然业务窗口尚未经过；上线短验证不代替长期容量验收，不为等待这些窗口增加常驻Codex轮询或暂停生产。剩余唯一业务成果仍会增长，W3完整资料重复物化为明确后续议题。
