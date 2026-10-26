# 能源、基础设施与政府量子消息源接入

日期：2026-10-27，北京时间。按「同站、同板块、同抓取方式」归并外部身份，内部保留独立入口、ticker 栏目组和失败状态。

## 配置范围

| 外部源 | 方式 / 模式 | 内部入口 | Ticker |
| --- | --- | ---: | --- |
| National Quantum Initiative | 官方新闻 HTML / By Ticker | 1 | IONQ |
| NIST News | RSS / By Distribution | 1 | IONQ |
| NRO Press Releases | HTTP HTML / By Distribution | 1 | IONQ |
| NSF News | RSS / By Distribution | 1 | IONQ |
| Data Center Dynamics / Energy Companies | HTTP HTML / By Ticker | 2 | CEG、BE 各自企业专题 |
| POWER Magazine / Energy | RSS / By Ticker | 7 | CEG 核能组、BE 分布式发电与储能组 |
| Power Engineering / Energy | RSS / By Distribution | 6 | CEG 核能组、BE 分布式发电与储能组 |
| Utility Dive | RSS / By Distribution | 1 | CEG、BE |
| World Nuclear News | RSS / By Distribution | 1 | CEG |
| ANS Nuclear Newswire | RSS / By Distribution | 1 | CEG |
| Canary Media | RSS / By Distribution | 1 | CEG、BE |
| NRC News Releases | HTTP HTML / By Distribution | 1 | CEG |
| NRC Event Notifications | HTTP HTML / By Search | 1 | CEG，匹配 Licensee |
| DOE / Nuclear Energy, EDF & Electricity | HTTP HTML / By Distribution | 4 | CEG 全部；BE 不含 NE |
| Data Center Dynamics / Infrastructure | RSS / By Distribution | 1 | CEG、BE、INTC、MU、COHR、NBIS |
| Data Center Frontier | RSS / By Distribution | 1 | CEG、BE、INTC、MU、COHR、NBIS |

合计 16 个外部源、31 个内部入口。用户已确认通用组沿用此前硬件组，不添加 SNOW。IONQ 未进入 Bus 监测状态时只注册并预留 4 个源，不创建 ticker、不推进或绕过初始化；其状态已存在时才配置对应绑定。

进一步检查初始化消费者和 `CandidateConfiguration.install()`：激活时会整体替换该 ticker 的 live 绑定，因此直接预写 live 订阅可能被正在生成的候选配置覆盖。本次按用户允许的“先不配置”处理 IONQ，避免修改正在初始化的候选产物；四个源本身已完成接入与只读验收。

POWER 的 CEG 组为 nuclear、data-centers、legal-regulatory、safety；BE 组为 data-centers、distributed-generation、energy-storage-2、legal-regulatory、hydrogen。Power Engineering 的 CEG 组为 nuclear、operations-maintenance、business；BE 组为 onsite-power、energy-storage、renewables、business。重叠栏目保留一个内部 URL，按订阅者选择采集任务/分发 roster。

## 低成本采集及正文

复用现有 TechnologyHttp、TechnologyAdapter 的 HTTP 池和条件轮询机制。新适配器只增加目录、结构解析和 ticker 入口选择；不新建服务、数据库表、浏览器、Chrome 身份或常驻页面。与现有技术源共用 HTTP 全局 4、同站 2 的物理并发及同站 0.5 秒请求间隔。BODY 进程也复用现有 HTTP 池，不与 CRAWLER 争用 Chrome。

目标间隔 60 秒，保持既有交易日历和补扫行为。内部入口通过现有 FEED 单元独立调度、提交、记录检查点及 60–900 秒失败退避；429 遵循 Retry-After。分发源的各栏目只进入实际订阅它的 ticker roster。POWER 的公共栏目在两个 ticker 下保持同一调度相位，合并在途请求，已完成响应只复用 2 秒，避免整分钟缓存拉长发现时延。无额外定时清理线程。

HTTP 200 验证页不算 RSS 成功，只有解析通过才保存 ETag/Last-Modified。网络 TLS/连接/超时错误及实测出现的空 HTTP 200，仅在原路线重试一次；HTTP 拒绝和验证页进入退避，不叠加浏览器回退链。五分钟轻量复查继续识别正文/摘要修订，沿用共享 BODY 台账和 ARTICLE_ONCE。

压力抽样另外确认 curl_cffi 0.15.0 的流式错误路径会在错误分支与任务 cleanup 回调各归还一次句柄，可能导致后续 `multi: (7)` 冲突或空 200。共享 TechnologyHttp 改为普通异步 request 加有界接收回调：句柄只从 request 的 finally 归还，解压后接收仍严格限制在 4 MiB。保留原 TLS 指纹、cookies、出口、连接池和并发容量，没有通过扩容掩盖问题。

实测选择：POWER、Power Engineering、DCD、Data Center Frontier、NRO、NSF 使用现有美国 HTTP 出口；Utility Dive 使用 DIRECT 标准库 HTTP；其他入口使用 DIRECT HTTP。Quantum.gov 的 `/feed/` 跳回首页，`/news/feed/` 返回 403，WordPress REST 返回 401，因此读取其带稳定链接、时间和新闻条目的官方 `/news/` 页面。

正文继续通过现有完整性、身份、收费预览检查和清洗流程。RSS 描述不冒充完整正文。常规 BODY 保留发布者精确时间升级和 304 复用；同一 DCD 文章从企业专题及全站 RSS 被发现时，共用正文下载和发布去重。

## NRC 事件拆分

官方 EN 页面已经包含每个事件的完整结构，不需要浏览器或逐事件二次下载。按照 EN 编号分开，保留 Licensee、设施/地区、通知时间、事件时间、更新时间、法规段、全部 Event Text 及后续更新。匹配只针对 Licensee 栏；另一个机构在正文中提到 Constellation 不会被误收。

CEG 的源参数 `licensees` 初始为 Constellation Energy Generation、Exelon Generation，可通过原有绑定配置维护。Constellation 现行核电持证主体可在 [NRC 的 CCEC 页面](https://www.nrc.gov/facilities-safety/facility-finder/reactors/ccec)核对。源参数匹配主体，统一监测词仍用于原有搜索配置和语言校验。

使用稳定 EN ID 作为文章身份；可追溯 URL 为实测有效的官方按日归档及 event 参数，另记录 report_date/report_url。相同事件迁移到后续日期的报告时仍共用正文身份。报告快照进入专用 Native BODY Reader，校验编号、来源和正文哈希，完整性通过才进入正常 Raw → Standard → Stream。通用的非文章 URL 跳过规则对此类已验证事件交给专用 Reader 处理，失败不会用事件首行冒充正文。事件更新写回原消息和修订记录，不重新触发已消费消息的下游。

## 配置与验证

目录：`src/doxagent/message_bus_v2/energy_catalog.py`。适配器：`energy_sources.py`。正文：`energy_body.py`。配置：`scripts/ops/configure_energy_sources.py`，默认只预览，`--apply` 才落库。

配置只处理本表的绑定，不改其他源或既有监测词。CEG 原来没有统一监测词时建立英文种子；IONQ 若已进入 Bus、但没有词表则采用已有量子种子。已有词表保持原修订。IONQ 尚未就绪时可在其初始化完成后重复执行，配置脚本幂等。

验证覆盖全部 31 个实际入口样本、分组与分发 roster、共同栏目同相位、短响应复用与单 ticker 取消隔离、错误 200 不污染基线、独立退避、同路线有限重试、原生 NRC 逐事件完整正文、Licensee 精确匹配及事件更新 ARTICLE_ONCE。正式回归、生产发布及绑定验收另补充于下方。

265 项回归通过，记录为 `eval/energy_sources_20261027/regression.xml`。修正连接句柄生命周期后，生产网络只读实测 31/31 个入口成功；每个入口各取一篇，31/31 个 BODY 样本完整，对应 29 个不同 URL。NRC 当前页没有 CEG 的 Licensee 样本，正文能力验证使用当页实际事件；CEG 的字段匹配、原生正文入库和重要更新不重复发布由合成变体的端到端测试覆盖。没有伪造生产公告、修改样本时间或回灌历史消息。只读回执为 `preproduction-acceptance.json`。

## 生产发布与订阅验收

运行提交：`12bf384c2c611eb4d9246e2577e473b96ac37c12`。已提交 origin/main、同步新加坡仓库，使用标准 release 锁与构建/发布函数，仅更新 Bus/BODY。北京时间 2026-10-27 01:13 完成切换。实际镜像 `sha256:838bb36e3b5dc4b3771f22ef42df2f3f281dab8e9c1eaea46c90b4d7ff3a839a`，两个容器均运行、零重启，六个主要实现文件哈希与该提交一致。

已注册全部 16 个源，配置并启用 28 个订阅：CEG 12、BE 8，INTC/MU/COHR/NBIS 各 2。IONQ 在配置和验收时均没有 Bus 监测状态，其四个源已注册、只读验证，但没有创建绑定或启动 ticker。再执行配置脚本无新绑定、无词表修订，幂等核验通过。

原有 257 个绑定、83 个源的数据哈希全部不变，原有 8 份词表修订和 ticker 运行状态不变。仅新增 CEG 词表 v1；CEG 所需语言为 en，BE/半导体 ticker 保持 en/ko/zh-Hant，ONDS 保持 en/he。其他组件发布记录不变，资源回收 timer 已恢复。读模型已投影全部 28 个绑定，外部列表保持本表的合并粒度。

新镜像发布后，直接使用镜像内代码重新执行只读外网验收：31/31 入口成功，31/31 BODY 样本为 FULL/SHORT_FULL（29 个不同 URL）。另有生产自然采集与接纳证据：27 个已启用内部入口最新状态全部 succeeded；核验快照包含 326 个 CAPTURED、6 个当时在途 DISPATCHED，没有 FAILED/DEFERRED。随后自然发现的 4 篇 DCD 新闻进入正常处理流程，形成 19 条 ticker 标准消息，正文全部 FULL。没有使用测试数据或历史回灌冒充自然消息。

这是一次发布后的有限窗口验收，不将抽样完整率或短窗口自然轮询表述为长期 SLA。公开回执：`eval/energy_sources_20261027/production-verification.json`、`production-acceptance.json`、`natural-intake.json`。主机审计目录为 `/var/lib/doxagent/energy-sources-20261027/`，含环境变量的发布快照与 manifest 仅留主机私有目录。
