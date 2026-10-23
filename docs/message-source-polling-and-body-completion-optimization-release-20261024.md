# 消息轮询与正文补全优化：生产发布回执

日期：2026-10-24，北京时间。发布主机：`doxagent-sg`；仓库：`/home/ubuntu/doxagent`。

## 发布结论

代码已推送 `origin/main`，远端 main 已同步，7 个相关生产组件已发布并启用。最终业务代码版本为 `071b7658185b259579149d0b407a6554cc118e6b`。本回执后续为纯文档提交，运行镜像仍对应下表的代码提交。

发布与配置已完成，P0/P1 业务目标尚未验收。最终短窗口仍有明显资源延期和较长的实际采集间隔；不能以后台单次轮询变快或两个正文成功样本宣称优化目标达成。

## 实际运行版本与配置

|组件|构建提交|运行镜像 ID 前缀|最终状态|
|---|---|---|---|
|Message Bus / Content Enrichment / Scheduler|071b7658185b|273a2089e286|running，重启次数均为 0|
|Site Access|e8e4032eacad|4f5ed0970a03|healthy，重启次数 0|
|API / Projector|e8e4032eacad|b0f07a3c832d|API healthy、Projector running，重启次数均为 0|
|Web|e8e4032eacad|07ec4a0e8ffe|healthy，重启次数 0|

已启用：五个目标源组容量 2；Yahoo 的 MU/RKLB/BE/INTC/NBIS/COHR/ONDS/CEG/SNOW 九个 binding 均为 30 秒；五个 Site 及实际 scope 下十个 Identity 容量 2、准入等待上限 3 秒；Globe 已验证 HTTP 列表路径优先。全局 browser 4、BODY 常规 2、无 CRAWLER 等待时可借第 3 槽、每身份 BODY 1 的限制保留。最终配置工具 dry-run 返回 `changes: []`。

保留原 Chrome Supervisor 容器、启动时间、profile、Cookie 和出口；未新增身份或伪造 VALID。业务回填与指标迁移没有重新发布历史 Stream，保留 ARTICLE_ONCE。备份期间暂停的两个 maintenance 容器和 `doxagent-release-resources.timer` 均已恢复。

## 发布时修复的问题

1. **配置 CAS 自冲突（c58fb8ac）**：Source 更新会级联提高 binding revision；迁移改为先 binding 后 source，保留 Owner/CAS 校验。真实 Owner 级联回归 1 项通过。
2. **已发布文章查找全表扫描（9e1aa02f）**：补齐 ticker/source 精确条件以使用既有 `idx_mbv2_raw_identity`；生产 EXPLAIN 从 SCAN 变为索引 SEARCH。含 3000 条无关历史的 VM 步数有界测试在内，29 项通过。
3. **正文结果引用与重复写入（d517ef21）**：Observation upsert 同步结果引用、metadata 保持可查询；相同来源信息不再无条件重写 Raw/Standard；共享 distribution 获取前登记各 ticker 观察。机制 42 项、distribution/retention 10 项、分发 cohort 8 项通过。有界修复扫描上线后 846 个任务，344 条已有观察补齐引用、502 条无需更改、0 条新增，无网络抓取或消息重投。
4. **旧文修订导致 BODY Worker 退出（071b7658）**：生产 Yahoo 历史同文存在多个逻辑 alias，更新误选其他 Raw 触发严格身份门禁并使 Worker 重启。现在以可信已发布原 Raw 为锚点；预期的单篇更新拒绝回滚后终结为 `UPDATE_REJECTED`，继续处理兄弟任务，基础设施异常仍上抛。42 项相关回归通过。最后 Worker 自 01:41:12 启动至复核保持重启 0；两条原失败任务已终结，不再使进程循环退出，其历史结果是 `deadline_exceeded`，不能算正文成功。

开发阶段已有 357 项 Python 回归通过、1 项跳过；真实 Chromium 同 profile 双页与定向取消测试通过；前端 67 项及两个宽度检查通过。详见开发记录。生产双页取消专项请求受资源准入延期，未取得对应线上成功证据；自然诊断观察到 Yahoo backup 同身份两条活动命令，不能替代所有来源的稳定性验收。

## 上线后的数据与边界

最终版本审计窗口：北京时间 **01:41:41–01:49:19**，约 7 分 39 秒。以下均为窗口实际记录，样本不等量，无同期控制组；阶段存在嵌套，不能将耗时相加计算占比。

|来源|轮询样本|单次轮询均值|新 capture 间隔均值（间隔样本数）|延期次数|失败次数|
|---|---:|---:|---:|---:|---:|
|Yahoo|20|30.6s|199.7s（11）|0|0|
|Barron's|12|36.4s|196.5s（6）|10|0|
|InvestorsHub|8|46.8s|176.9s（1）|13|1|
|Investing|8|41.9s|165.0s（3）|17|0|
|GlobeNewswire|17|33.3s|174.5s（8）|7|0|

**实际发现频率仍明显慢于配置目标。** 新 capture 间隔包含调度、资源准入、浏览器/HTTP 执行及退避影响；不能把它等同于纯下载耗时。该窗口中新 capture 到首次接纳均值：Yahoo 约 1.8s、Barron's 约 9.0s、Globe 约 8.5s。Investing/InvestorsHub 部分字段接近零，需要结合 shared capture 与原始时点解释，不能作为零发现延迟证明。日志仍观察到共享 Bus 存储等待约 1–1.5s 的事件，且资源延期依旧存在，后续应继续围绕这些实际瓶颈调试。

该窗口首次观察文章仅 **2 篇，2 篇 READY/FULL**，不足以证明稳定 >80%。上线早期另一窗口为 192 篇、116 篇 FULL/SHORT_FULL、32 篇 pending、44 篇失败，其中混有历史积压与复查；不能直接与历史新文章成功率比较。历史任务终结、缓存命中和执行成功数均不等同于真实新文章完整正文成功率。Reuters/DIGITIMES 的身份及权益能力也未因本次发布自动恢复。

01:45:28 对账：Read 已投影 682 条正文结果、722 条文章观察、1319 条 BODY 任务；Bus transport head 4413661，Read checkpoint 4413651，尚有 10 个位置在途；Site outbox 6077 CONFIRMED、9 PENDING，不能宣称瞬时全清空。

单次资源快照：Bus 约 212MiB、BODY 267MiB、Scheduler 289MiB、Projector 440MiB；Site 约 3.90GiB、保留的 Supervisor 约 3.88GiB。此为 Docker 当时统计值，Site 使用 Supervisor 网络命名空间，不能由此推算独立浏览器总成本或长期资源改善。

## 迁移、备份与回滚

通过标准 `storage_release.py` 构建/发布，不在容器中临时覆盖代码。Bus 的 13 个相关 CDC 表已登记，Read 索引迁移完成。首先更新消费者，然后发布 Site 与消息生产者；后续问题修复只滚动三个相关后台角色，未重启 Supervisor 或无关交易服务。

远端私有回执目录：`/var/lib/doxagent/news-optimization-20261024/`，包含发布前运行组件、各次 deployment/build 日志与配置回执。`state.json` 和 compose 中含密钥，不提交仓库、不用于公开证据。

已完成固定读快照备份：`bus-before-snapshot.sqlite3`（6,333,575,168 字节）、`read-before-snapshot.sqlite3`（5,221,421,056 字节）、`registry-before-snapshot.sqlite3`（724,996,096 字节），均在上述目录。第一次未固定快照的尝试超时，留下的零字节文件 `bus-before.sqlite3` 不是可用备份。

回滚时按该目录 `rollback-components.private.json` 记录的**实际运行镜像**选择组件，通过标准 release 工具发布；配置按 Owner/CAS 反向迁移。备份用于离线核查或明确维护窗口下的恢复，不能在继续写入时直接覆盖数据库而丢失上线后新闻。NativeContent 原卷保留。新增表/字段为兼容性迁移，不应为代码回滚删除业务数据。

## 可复核证据

- `eval/news-production-final-status-20261024.json`：镜像、状态、任务与投影快照。
- `eval/news-production-final-resources-20261024.jsonl`：一次资源快照。
- `eval/news-production-final-version-audit-20261024.json`：最终版本有界审计原始结果。
- `eval/news-production-release-summary-20261024.json`：按来源加权汇总及正文 cohort。
- `eval/news-production-observation-reference-repair-20261024.py`、`eval/news-production-observation-repair-result-20261024.json`：有限范围引用修复与回执。

本次按用户要求完成 origin/main 同步和生产发布。后续仍需在持续自然流量下确认新消息覆盖、实际采集间隔与尾部时延，并以成熟的新文章 cohort 验证正文完整率；本回执没有将这些未满足项标为完成。
