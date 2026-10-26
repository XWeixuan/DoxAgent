# Message Bus 生产恢复与发布核对（2026-10-26）

截至北京时间 18:12 的运行快照及随后真实查询进程复验：消息源列表、消息列表和统计已恢复，九个 ticker 都在采集；正式 API、Bus、投影器正常运行。恢复后完整页面请求 57/57 成功，最长 1.863 秒；没有把容器 healthy 直接当成业务验收。

## 此前如何发布、本轮如何整合

核对“消息源修复与开发工程-2”的最终发布记录及 `software-quantum-hardware-sources-20261026.md`：该会话在隔离检出中构建 `e2b308bc`，通过 `storage_release` 发布正式 `v2-message-bus`、`v2-content-enrichment` 两个服务，执行 66 个订阅配置并验证幂等。隔离的是构建输入；不是用临时容器长期承载消息管线。随后该功能已随完整 main 合入本会话的发布。

另外，本会话上一轮 `85a97829` 发布确实使用过一个退出即删除、无网络的一次性数据库迁移容器；它只执行迁移，不提供生产服务。这与正式服务部署分开说明。

本轮先核对本地、origin/main、新加坡仓库及所有运行组件；起始三者都是 `2581a5b4`。本地两份生成 schema 的状态标记最初没有实质 diff，没有覆盖、丢弃其他工作。新增修复均提交并推送 main，远端按提交快进；正常 source-release 构建从完整提交归档取得输入，复用已核准的依赖层。

本轮对 API、projector、Bus、BODY、scheduler、control、initialization、Site Access、Web 共 **9 个受影响正式服务**进行了构建/更新。使用标准发布锁、正式 Compose service、原持久化卷及配置；没有创建临时生产服务。读模型修复命令在正式 API 容器内执行，并以原 projector 写锁排他保护。未变化的交易/研究执行服务和 Chrome Supervisor 没有无故重启。

| 最终组件 | 代码提交 | 说明 |
| --- | --- | --- |
| API | `2c3729e4` | 完整契约、物理快照读取、生产读队列配置 |
| Bus、projector | `5290f5cb` | Runtime 打开修复；projector 正常命令使用 `--limit 500` |
| BODY、scheduler、control、initialization、Site Access、Web | `46a9d7dd` | 整合此前全部 main 功能并重新构建的版本；后续修复未改变这些组件使用的路径 |
| Chrome Supervisor | `f2f0bcd6` | 本轮无相关源码变化，运行实例保持原样 |

报告和诊断证据的后续归档提交不改变运行代码，无须仅为文档重新 build。

## 根因与修复

1. **消息已经发布，但消息摘要投影被契约拒绝。** 原始 Bus 的 `stream_items` 今天仍有数据，Read 的 `message` 却停在周五；Bus source checkpoint 仍推进。用正式 projector 重放实际失败事件，复现 `MessageSummary/: additionalProperties`：投影器输出 `content_update`，后端生成 schema 没同步这个字段。生成脚本只更新选定的类型，漏了 `MessageSummary`。已补上类型并重新生成 schema，不放宽 additionalProperties 校验。
2. **失败没有自动修复。** `ProjectionWorker.repair()` 存在，但正常 `project` CLI 循环没有调用它。已接入每五秒一次、有时间预算的重试，保留源事件、异常 pin、原始失败及水位，不绕过 provenance。重试先处理较新的事件。必要的当前消息恢复另用幂等脚本完成，不依赖历史失败逐个跑完。
3. **源列表与状态查询超时，挤占整个读请求池。** 初始日志连续出现 sources/status/messages 的 3 秒 SQL 中断和 1 秒队列拒绝。技术 poll 投影写了未被 store 使用的 `sort_key`，实际排序仍落在 ID；窗口查询因此读入大量旧记录。已修正为 `sort`，按原生 dispatch/observed 时间建立明确索引、分当前/历史物理表查询。兼容旧 sort 的记录，不删旧证据。轮询状态窗口仅选统计标量，不解码完整 checkpoint；正文执行统计按文章键索引查询。
4. **精确快照读取仍重复扫描历史。** 单项耗时通过后，整页实际路由仍失败。逐卡计时发现 source/ticker/poll 的 `get()` 经兼容 UNION 视图对旧版本排序；Yahoo、SEC 卡片可单独耗时数秒。改为同一读事务先查物理 current 精确身份；其版本晚于固定水位时，才用历史主键找一条前驱。分页历史分支也使用直接 `valid_to` 范围，保留原快照语义。
5. **健康请求在执行前被拒绝。** 修复 SQL 后，新查询进程的同屏四请求仍有冷启动波次触及 1 秒队列上限；这批失败并未算成功。生产队列预算改为 3 秒，写入正式 Compose 默认、主机私有 env 和当前发布 manifest；仍为 **2 个读 worker、16 条等待队列、3 秒 SQL 执行上限**，没有靠扩大并发或放宽慢 SQL 掩盖问题。
6. **重建时 Bus 争 Runtime 写锁。** 第一轮 Bus 容器启动三次失败，栈明确在 `RuntimeJournal.__init__` 的重复 DDL，而非 OOM。已预置库改为不初始化地打开，新库保留初始化路径；真实持有 Runtime 写事务的回归通过。最后新 Bus 零重启。

改动位置：`frontend/v2/scripts/generate-schema.mjs`、`api_v2/{wire_schema.json,news_timeliness.py}`、`v2_read/{repository,projectors,projector,cli}.py`、`message_bus_v2/factory.py`、`docker-compose.v2-production.yml`。重要变更均追加 `changelog`。

## 消息与历史数据恢复

新增 `scripts/ops/repair_bus_message_projection.py`，默认预览；`--apply` 取得既有 projector 写锁，针对源库已经发布、读模型缺失的消息重新生成摘要和相应贡献。短暂停止正式 projector，在正式 API 容器中执行，finally 恢复 projector；不修改原始标准消息、Stream、checkpoint 或下游消费位置，不重新发布，不改变 ARTICLE_ONCE。

当次识别 373 条缺失，恢复 371 条；两条当时尚无上游 stream 投影，随正常追赶完成。最终再次预览 **missing=0、failures=[]**。其他缺失已先由自动重试恢复，因此 371 不是整个故障期间的全部消息数量。

18:12 快照四个 source 均追平：Bus `6610308/6610308`、Runtime `2488680/2488680`，research/initialization 同样 head=checkpoint。这表示新源事件已追上，不表示历史失败账目清零。

仍保留 **23,597 个历史 Bus ValueError 事件**和 **56 个既有 Runtime PROVENANCE_UNVERIFIED**。初始 Bus 失败为 44,688，已持续减少；不能把尚未逐条复验的历史 ValueError 全部认定为同一个原因。缺失当前消息已经单独对账为零，历史技术失败继续有预算地重试；未删除失败记录来制造健康或覆盖率。

## 生产验收与资源

- 正式部署的查询进程、真实生产 Read/Bus 数据及真实 API 路由复验：九 ticker 的当天 context、ALL 消息 context、sources/status/metrics/messages，再加 overview context/metrics/tickers，共 **57/57 返回 200**，最慢 **1.863 秒**。其中总览返回九 ticker，消息流均有实际记录；INTC/MU/BE/NBIS/ONDS 的最新记录已是今天，RKLB/SNOW 的最近发布在周日，不伪造今天的新消息。
- 这是调用现有“父进程已认证 principal → 查询 worker”通道的验收，没有关闭或修改生产认证。浏览器连接工具不可用、也没有可接管的本地 Chrome，因此没有冒称已直接检查用户登录页面的截图。Web 实际首页、healthz 和 API readyz 同时通过，全部读池可用、无排队。
- 完整冷进程/真实页面测试不是一次成功：保留过 `36 请求/27 失败`、`36/8`、`57/7` 等中间结果；最终补齐查询机制和队列配置后 `57/0`。见同目录各轮 JSONL，不使用仅成功样本验收。
- 18:12 前五分钟九 ticker 都有有效列表记录，共 **1,330 次 capture**，各 ticker 最新采集均在当时近几秒；近两小时 **53 条实际发布**。Bus 新实例及 API、projector 均零重启，OOM=false。
- 收尾另核对下游：当天读模型有 **80 条实际 Case**（BE 14、COHR 2、INTC 36、MU 12、NBIS 14、ONDS 2），最新 NBIS Case 在 10:12:00 UTC；采集之外，消息处理也在推进。这是当天 Case 口径，与前述“两小时 53 条发布”不是同一窗口，不能直接作转化率。
- 近两小时正文结果有 **37 FULL、18 FAILED**。说明 BODY 在工作，但本轮恢复不等于正文稳定超过 80% 或全部高风控来源访问恢复；失败样本保留，未降低 FULL 质量门槛。
- 服务器实际 **8 核/16G**，无旧 4 核硬配额；快照可用内存约 **4.73 GiB**。一秒 CPU 样本 busy **72.09%**，load 仍约 11，说明仍有资源竞争；不把它当长期均值。磁盘约 132 GiB 可用。CPU 及组件内存快照与时间见证据。
- 原有 Chrome identity revision 全部保持；运行时按既有 publisher 机制新增了四个原先不存在的 publisher identity，没有覆盖原身份。Supervisor 容器 ID/启动实例相同，四个 CHALLENGE target/所有权均保持，不清 profile、不删挑战页面。

发布后按标准资源回收清退 **7 个过期/作废应用镜像标签**，包括实测仍会查询超时的 `fbbe9a5f` 读服务回滚版本；其回滚指针先明确作废。仍被正式容器使用、有效回滚或模板依赖引用的镜像保留。没有删业务卷、配置、正文、Chrome 身份或任意系统 volume；没有临时服务容器遗留。

测试：最后完整相关后端回归 69 项通过，Runtime 启动及 decision/factory 相关 5 项通过；前端 schema 生成、类型检查和正式构建通过。另修正此前仍假设“最多 16 ticker 必须拒绝”的过时测试，与已实现的分批语义一致；不改动生产分类结果。F 类静态检查及 diff 检查通过。

证据目录：`eval/production-recovery-20261026/`，包含各轮实际请求结果、最终运行快照、CPU 样本、只读诊断及可复现恢复脚本。私有 env、发布 manifest 和生产凭证继续只留主机私有目录。
