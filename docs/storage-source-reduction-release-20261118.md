# 存储源头减量生产发布回执

本轮发布此前已经完成的开发项，不将方案中尚未开发的条目认定为完成。应用基线为 `1b0494daeb9474c2f23de1e17100bd9ae8a50788`，包含 `b99d0dbea` 的 W3 延时修复。发布现场补充的采集启动索引修正为 `62bdc9b047cd`。本地 main、origin/main 与远端代码已同步该修正。

## 发布范围与顺序

先实际发布并验证 `b1aaddaa2` 兼容版本，写入开关关闭，再发布最终应用版本并开启 `DOXAGENT_PUBLISHED_CONTENT_REFS=1`、`DOXAGENT_CONTENT_SINGLE_FILE=1`。两个开关保存在发布 owner 的正式 Compose 配置中。回滚读端已经验证可读取旧分块、新单文件和压缩发布正文，避免回滚后无法读取新文件。

17 个角色包括 Projector、API、Control、Delivery、Initialization、CDECR、O4、Executor、Site Access、Initialization Guardian、Worker、Scheduler、Collector、Message Bus、Content Enrichment、Read Maintenance 和 Transport Maintenance。Guardian 的动态 Repair Agent 镜像同时更新。未修改 Web、Chrome Supervisor、MCP Tunnel 和网络代理。停止态 `v2-migrate` 的正式配置更新到兼容镜像，本轮不执行全量迁移或数据库备份。

Read、Research、Initialization、Bus、Runtime 共 99 个 Native UPDATE 触发器已升级为差量引用维护，DDL 核验中业务行变更为 0。源 capture 库的大规模过期索引、历史正文转换、重投影和业务存量深度清理没有随此次发布执行。

## 现场发现并修复的发布阻塞

采集器首次健康检查通过后，配置刷新停止、转为 unhealthy。只读线程栈确认其阻塞于 `recover_previous_owner()`：旧的 status 索引无法跳过大量已完成 CAPTURED 历史，初始恢复没有完成，刷新和采集循环尚未启动。首次健康检查读到了旧进程留下的短期有效配置回执，不能代表新进程已开始采集。

增加 `cycles_unfinished ON cycles(status,run_id) WHERE finished_at IS NULL`，现存库执行一次索引安装，未来新建库在正式初始化时安装。安装耗时 330.77 秒，业务行变更 0。实际查询计划使用新索引，19 条中断周期候选查找仅 2.64 毫秒。保留已验证分页的 CAPTURED 身份，仍按原逻辑补齐中断终态；没有删除历史或改写采集授权。Collector 恢复 healthy，11:52 UTC 已有新 capture 被正常 ACCEPTED、待接收为 0。

另外，Scheduler 的既有停机上限为 1,200 秒。本轮收到退出信号后仍不结束，第一次结束已等待的旧实例，第二次显式使用 60 秒停机上限。持久化任务由新实例恢复；未修改 W3 业务判定、并发和交易规则。

## 验证依据

- 发布前 48 项针对性回归通过。采集模块原有 21 项通过；新增万条历史规模回归及重启恢复保护回归通过，覆盖小执行步数预算内恢复、已完成历史不变和中断证明保留。
- 兼容版、最终版分别在容器内用临时目录验证旧小对象分块、新单文件、大对象分块、范围读取及压缩发布正文的 SHA 校验，未为测试写入生产业务库。
- 17 个角色逐一核对实际 image ID、源码提交、两个生效开关与兼容回滚引用；API、Worker、Site Access 健康端点均 HTTP 200。初始验收后继续补验，发现并修复了上述 Collector 问题。
- 自然业务观察包含 W1/W2 成功处理、恢复后新采集记录正常 ACCEPTED。既有 SQLite 锁等待仍可见，W3 完整模型处理和日均写入下降不作为本轮已证实结果。
- 既有 Runtime 历史投影缺口保持 17,207 条，未删除或伪造消费坐标。切换后的动态投影水位及最终健康、空间结果见随附验收 JSON。

北京时间 19:54:45 最终验收通过：17 个角色均匹配预期镜像、开关与兼容回滚引用；22 个容器运行，API、Worker、Site Access 均返回 HTTP 200。Collector 实际镜像为 `62bdc9b047cd`，其余本轮应用角色为 `1b0494dae`；Collector 回滚为兼容的 `1b0494dae`，其他角色回滚为 `b1aaddaa2`。停止态迁移工具的配置也采用 `62bdc9b047cd` 镜像。

Collector 新实例启动于 11:54:04 UTC，配置刷新至 11:54:40，11:54:45 的新 capture 已 ACCEPTED，实际 `captures.status='PENDING'` 数量为 0。Bus `12123198`、Initialization `163162`、Research `92608`、Runtime `4999900` 四路 checkpoint 与该次采样 head 一致，source error 均为空。Projector 曾自动重启一次，最后继续正常推进；没有据此前一次启动成功掩盖该记录。

根盘当次可用 `19,749,273,600 B`，约 **18.39 GiB**。兼容发布、正式发布和采集补充发布均已完成自动资源回收，回执 errors 为空。新镜像构建和业务写入同时消耗空间，本值为实际剩余量，不将三个清理回执的阶段增量冒充整次净释放量。本轮没有执行新的业务存量深度清理。

核验文件位于 `artifacts/audits/storage_release_20261118/`：`final.json` 保存按角色结果与水位，`recovery-index.json` 保存索引耗时和查询计划，`collector-stack.txt` 保存阻塞现场，`codecs-and-triggers.jsonl` 保存格式和 DDL 核验。含访问配置的发布 manifest 与恢复配置只保留在远端受限目录 `/var/lib/doxagent/releases/storage-source-release-20261118/`，未复制入仓库。

## 完成边界

已交付功能与尚未开发项继续以 [开发回执](storage-source-reduction-development-20261118.md) 的逐项清单为准。SDK 全树扫描、共享 W3 Context 历史实体收敛、历史 Event 根引用迁移、最小 `.init` manifest、剩余 Read 字段与历史转换等不能因此次发布而自动视为完成。

本轮自动发布资源退出会清退无引用旧镜像与构建缓存，保护当前容器、兼容回滚、动态模板与稳定依赖。净可用空间包含同时发生的业务写入影响，不能与某个文件删除量直接相加；日均增长改善仍需运行数据验证。
