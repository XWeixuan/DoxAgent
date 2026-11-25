# COHR 运行状态页缺失数据修复与验收（2026-11-25）

## 排查并修复 COHR 今天及上一交易日运行页数据缺失

已恢复后续新消息处理并部署修复。页面为空的直接原因是消息任务在创建 Case 前失败，Runtime 源库本身也没有对应记录；四个 Read 投影检查时间正常推进且 head 等于 checkpoint，因此重建投影不能生成缺失的历史 Case。

COHR 与 NBIS 的每日 Known Event Index 编译需要沿 Event 发布历史确定事实首次获知时间。11/23 紧急归档移除了两个仍被发布历史引用的祖先库；已有日索引暂时遮蔽问题，后续日索引首次编译时触发 `FileNotFoundError`。任务失败发生在 Case 创建之前，故前端没有可展示的数据。

### 恢复与代码修复

- 从 `D:\DoxAgent-Cold\20261123-emergency\events-00200-1795430537050439400.tar.gz` 恢复 COHR v1 和 NBIS v2 的两份祖先 SQLite 文件。在发布锁及资源退休锁内逐份核对字节数、SHA256、SQLite quick_check 和发布版本后恢复，不覆盖现有文件。恢复完成时间为 2026-11-25 13:56:57 UTC。
- COHR 恢复文件 SHA256：`7ae6448f113437371d99fa83c0a37406fa7c2e73b30a6f4303cca92baa77f789`；NBIS：`5761699a6eba973f907198c855e66762c71755b96331df551c7fed8255ed7077`。总量 806,912 字节，无额外清理磁盘。
- 修复首次获知证据迁移：包括旧迁移 checkpoint 在内，跳过没有新增发布的冷分支；真实发布祖先仍必须存在，不伪造时间。
- 新分支与保留快照按选定版本继承已完成的首次发布时钟和事实内容准入证据，避免复制后丢失自包含证据、重新依赖全部旧分支。
- 对当前 COHR v40、NBIS v51 完成有界证据迁移，分别保留 1,080/1,702 条事实证据、40/50 条发布时钟。11/24 与 11/25 的过滤后 Known Event Index 在迁移前后 SHA256 完全一致；没有改写历史获知时间。

用户明确选择“仅恢复后续新消息”。未重试历史失败任务，未重写历史 frozen inputs，未修改交易或 W3 路由。诊断时发现 COHR 149 条、NBIS 225 条历史任务在 Case 创建前失败，这些任务保持原状态；上一交易日的空白不会因本次修复自动补齐。

### 部署与回归

实现提交：`9fdc39319dbb2a7e7f2e75532e83342bf795d2d8`。

仅对实际在用的 Transport Maintenance 与 Scheduler 镜像覆盖三个 Event 证据相关源文件，部署前核对原文件与父提交一致，部署后核对三个文件 SHA256。通过现有发布 owner 注册镜像、保留回滚状态并顺序切换；Scheduler 使用既有正常退出宽限期，没有强制取消模型请求。

| 服务 | 部署镜像 |
| --- | --- |
| v2-transport-maintenance | `sha256:ba67acb7015c76455ec2016861fdbced9d2148bd8d1f2e44afc4d23dac2640dd` |
| v2-scheduler | `sha256:e34885d60647e177eeda79023613f9336bbc9bd769f83de4a0ed54f3cb240083` |

生产发布回执：`/var/lib/doxagent/releases/event-evidence-9fdc39319dbb/`。

定向回归 **32 passed**：`test_event_admission_recovery.py`、`test_w1_daily_index.py`、`test_runtime_resource_refs.py`、`test_runtime_resource_exit.py`、`test_runtime_orchestration_execution.py`。覆盖旧迁移 checkpoint、已退休无变化分支、事实内容回退时的首次获知时间，以及分支/保留快照证据继承。`git diff --check` 通过；未将已有格式问题表述为全库 lint 通过。

### 真实新消息与页面验收

截至 **2026-11-25 14:11:24 UTC**：

- COHR 当日源库和 Read 均有 **3 条新 Case**；NBIS 均有 **6 条**。均来自恢复后的正常消息流，没有人工历史回放。
- COHR 的三条 Case 在真实 MCP 查询中均为 COMPLETED。已登录的实际 Chrome 页面显示处理量 3、来源/W1/W2 链路各 3、完成列表 3，热路径 14.85 秒、W3 均值 43 秒。没有使用前端 fixture；未修改前端代码。
- 恢复后未观察到新的缺失祖先文件错误；11 个活跃 ticker 的真实过滤后 Known Event Index 全部可编译。
- 四个投影均无当前错误且 head=checkpoint：Runtime 5,396,545，Research 143,451，Initialization 195,523，Bus 13,284,242；检查时间为 14:11:24 UTC。
- 当时磁盘可用 37,810,794,496 字节，约 35.2 GiB。原有 Runtime 历史 gap 17,218 保留，不声称已补齐历史数据或完成长期容量验收。

实际页面：<https://agent.doxatlas.com/ticker/COHR/runtime?period=CURRENT_TRADING_DAY>。

## 复核消息源目录首页超时

本次真实 DoxAgent MCP `sources` 首页调用成功返回 5 项，`refresh_error=null`。另行使用生产 MCP 容器内现有只读 ApiClient、唯一查询 URL 进行冷读取，**28.393 秒返回全部 99 个消息源**，`refresh_error=null`，快照时间 2026-11-25 14:16:06 UTC；未出现 `QUERY_TIMEOUT`。该样本仍有较长耗时，不代表全天性能验收；本次未扩大修改已有 API/MCP 超时实现。

## 证据

紧凑回执位于 [`eval/runtime_projection_recovery_20261125`](../eval/runtime_projection_recovery_20261125/)：祖先恢复、首次获知迁移、发布 build/deploy、源库与 Read 审计、实际 MCP 返回和浏览器可访问性观察。时间点样本只证明本次观察范围，不代表全天延迟或长期稳定性验收。
