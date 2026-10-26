# 技术消息源粒度纠正

日期：2026-10-26。消息源粒度按「同一个 site、同一美股板块、同一种抓取方式」确定。首次把各栏目拆成外部消息源不符合该定义，本次归并外部管理身份，同时保持内部独立轮询。

## 外部源与内部入口

| 外部 Source ID | 内部入口 | 订阅 |
| --- | --- | --- |
| quantum_insider | Business、National、Capital Markets | IONQ 预留 |
| register_software_cloud_security | Software、Cloud、Security | SNOW |
| venturebeat_software | Orchestration、Data、Security | SNOW |
| siliconangle_software | Big Data、Cloud、Security | SNOW |
| crn_cloud_security_datacenter | Cloud、Security、Data Center | SNOW |
| wccftech_hardware | AI、Industry、Hardware | INTC、MU、COHR、NBIS、BE |
| technews_hardware | Semiconductor、AI | INTC、MU、COHR、NBIS、BE |

其他源保留原身份。SiliconANGLE AI、VentureBeat AI 涉及软件及硬件多个板块，继续独立。Quantum Computing Report、Quantum Zeitgeist 是不同 site，分别保留。The Fly 不变。

常规外部源从 26 个归为 13 个，26 个采集入口全部保留；常规订阅从 57 个变为 34 个，另有 The Fly 9 个。IONQ 未注册，预留 3 个量子源及其 5 个入口，不启动监测。

## 调度与迁移

复用既有 `PollState.units`、`poll_runs`、`poll_captures`、distribution run 和分发流程，不新增服务或数据库表。内部 FEED 单元以入口 URL 的稳定哈希标识，分别持有实时检查点、HTTP 验证器、下一轮时间、失败退避、执行记录和采集提交。实时调度按 source + FEED 排除重复在途任务，健康栏目的结果立即进入原有分发和 BODY 流程，无需等待同源其他栏目完成。

同站仍共用 scheduler_group、HTTP 连接池、并发上限 2 及 0.5 秒请求间隔。没有增加 HTTP 并发、浏览器、身份或常驻页面。闭市补扫保留原窗口语义，不能覆盖实时 FEED 状态。

配置脚本在原有事务内建立合并源和绑定、迁移各入口的检查点，随后墓碑化旧绑定、停用被合并的旧源。Wccftech 沿用 `wccftech_hardware`，将其原 Hardware 入口的状态保留到对应内部单元。重复执行不会重复创建订阅或迁移单元。

原始消息、标准消息、文章正文结果、来源修订与执行历史均保留；旧 Source ID 仍能解析，以兼容已有正文任务。已有 URL + 语言 + public scope 的正文复用及跨源 ARTICLE_ONCE 继续生效，迁移本身不发布历史新闻。

## 验证

覆盖：全部 26 个真实入口样本解析、合并后的源与订阅数、慢内部栏目不阻塞健康栏目提交、独立 429/Retry-After 与重启后退避、同站物理请求并发上限、检查点迁移及 Wccftech 身份碰撞、旧源历史保留、迁移幂等、合并前已发布文章在合并后不重复发布。

219 项回归通过，涵盖技术源、The Fly、分发、持久运行调度、轮询指标、国防源、正文 Hub 和文章去重。测试记录为 `eval/tech_sources_20261026/grouping-regression.xml`。生产预检未发现本批来源的待处理分发任务；生产迁移验收补充于下方。
