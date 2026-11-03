# Massive Distribution 测试接入与启动记录

日期：2026-11-03。依据[开发方案](massive-distribution-newswire-development-plan-20261103.md)实施，五个平台已在生产环境启动**隔离测试**，不会向任何 ticker 正式分发。测试继续自然积累数据，本报告不将短时启动核验当作三日业务验收。

## 已落地范围

| 外部源 | 内部端点 | 获取方式 |
|---|---|---|
| businesswire_massive | All | 官方 RSS；单请求上限 16 MiB |
| prnewswire_massive | All、General Business | 官方 RSS，两端点共用平台调度 |
| globenewswire_massive | All、Public Companies | 官方 RSS，复用已验证 HTTP 出口 |
| access_newswire_massive | 企业新闻室 | 轻量 HTTP HTML；不用博客 RSS 冒充企业稿源 |
| tmx_newsfile_massive | Last25Stories | 官方 RSS；正文优先现有 Reader |

配置目标：INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS、IONQ。30 秒为调度目标，每个平台一项外部源、共七项物理端点，每端点一次采集供十个测试目标使用。不是每个 ticker 重新下载一份。

新增第四类型 `massive_distribution`，默认生产源行为兼容。测试目标由源配置生成内存绑定，不写真实订阅，不修改默认订阅组、监测词或交易时段。使用当前监测词 L2 正则，禁止 Decision Model。

## 复用既有链路和隔离

复用 Collector → CaptureBridge → LiveQueue → BODY → L2 → Raw/Standard 构建，最后在正式入库/发布前写同一个 Bus SQLite 的 `test_publications`。没有新增采集进程、测试消息总线或独立常驻调度器。

Capture 冻结 delivery_mode，测试文章/输入/别名/正文所有者使用独立作用域，避免同 URL 的测试任务抢占正式发布资格；已完成正文可沿既有缓存只读复用。同文多 feed 通过既有文章去重合并，跨 URL 的完整正文重复复用既有 fingerprint，记 TEST_DUPLICATE。保持 ARTICLE_ONCE：更新原文章及修订，不向正式下游重新触发。

测试标题/摘要不能代替完整正文：只有 FULL/SHORT_FULL 可以继续有效正则判断及测试发布，正文失败记录 NO_PUBLIC_BODY，不能冒充 IRRELEVANT。使用既有清洗、完整性判断和标准化逻辑。导出包含正常 title/body、来源、ticker、发布时间/精度和词库版本。

测试 HTTP 并发上限 2，BODY 在既有总容量内最多 1 个且正式任务优先。没有新 Chrome/profile，测试 BODY 不走浏览器；走现有 HTTP/Reader。对同平台最近三次连续正文不可用增加五分钟 pending 冷却，不伪造失败尝试，不停止列表发现。Business Wire 的实际受阻触发了此保护，其他平台仍继续取得正文。

清理由既有维护周期执行：默认保留 14 天，小批清理终态测试文章和结果、释放 Native 引用，不清理在途及正式文章，不另建 daemon。

## 实测、回归和发布

七个端点在生产网络的真实 canary 均采集成功；BW 实际解析 4121 项，PRN/GN 各端点 20 项，ACCESS 首页 20 项，Newsfile 25 项。ACCESS 两个正文样本分别 FULL 2139/6466 字符。早期代理 HTTPX 403 和 curl 解压兼容问题通过实际对照定位，改用已有 curl HTTP 客户端并消除二次解压；未通过切换 Chrome 身份解决。

回归记录：

- Collection/分发/跨源去重/发布方正文四组：74 passed。
- 正文管线与新鲜度：73 passed。
- Massive 专项：10 passed，包括同 URL 两种到达顺序、测试/正式末端隔离、重放 artifact 引用、跨 URL 同文、十目标七物理单元、304/非法 XML、ACCESS 时间、无浏览器回退、BODY 冷却和窗口交集风险。
- 最后补齐观测落盘后，Collector + Massive 联合回归：31 passed（包含上述部分专项）。
- 前端 wire schema 重新生成，TypeScript typecheck、生产 Web 构建通过；修改文件 Ruff E9/F 与 diff 检查通过。

这些是不同运行组，不能简单相加当作不重叠的独立案例。长时间高峰负载、三日真实相关性/覆盖评估仍需自然测试数据，不宣布已经完成。

产品版本：基础接入 91824dd8；正文冷却 1b2391db；幂等引用/词库版本 42000d1e；完整正文重复 7e63af94；来源窗口观测 1627d9b4；采集台账持久化 d6cf95ee。最终组件身份详见证据 release-identity-final.json。标准发布工具构建已提交源码，没有热补丁；旧资源回收因既有资源压力被发布工具延后，发布本身成功，未删除身份/卷。

## 运行快照

截止 **2026-11-03T14:34:29.936950+00:00**（北京时间 22:34 左右），这是启动阶段累计、非完整交易日口径。

| 来源 | FULL | PARTIAL | UNAVAILABLE | 等待正文 | 测试发布（article×ticker） |
|---|---:|---:|---:|---:|---:|
| access_newswire_massive | 16 | 0 | 0 | 0 | 7 |
| businesswire_massive | 0 | 0 | 104 | 14 | 0 |
| globenewswire_massive | 71 | 1 | 0 | 0 | 65 |
| prnewswire_massive | 37 | 18 | 1 | 1 | 22 |
| tmx_newsfile_massive | 8 | 0 | 0 | 0 | 6 |

正文数量是独立文章，不是 article×ticker；测试发布数是目标维度，不可直接相除计算文章命中率。等待正文及等待正则/提交另计，不把 pending 当作阴性或失败。Business Wire 的大量初始失败包含冷却上线前积累，不是每轮都重新下载同一批失败正文。PRN 的部分正文仍 PARTIAL，严格不发布为完整消息。

独立读回确认：五源 enabled；七端点各十目标；有连续 CAPTURED 物理轮询。`cycles` 最近 5000 行是混合来源的有界窗口，物理 run 与十倍目标回执分开统计，不能按其行数推全时段频率。30 秒是配置周期，实际间隔/尾延迟从调度 due/dispatched 和物理请求回执计算，不宣称每次都准时。

正式 raw_messages=0、standard_messages=0、测试源真实订阅=0、测试模型决策=0。原有 313 真实绑定、30 份词库修订、101 个其他源定义、1 个默认 profile 的全量有序哈希与启用前完全一致，见 production-before.json / production-startup.json / production-final.json。七个相关角色 running/restart0，有 health 定义的 API/Collector/Web 均 healthy。

实际导出两个 FULL 样本确认标准消息字段齐全：PRN→MU 8611 字符、GN→CEG 7668 字符。它们同时显示当前词库对轮椅服务、市场报告的宽词命中噪声，不能当作真实相关性验收；本轮未擅自调词。

资源瞬时快照：Bus 135.1 MiB、Collector 389.8 MiB、BODY 199.5 MiB；既有 Site Access 3.504 GiB。它们承担所有源，且包含重启后状态，**不是新增测试的资源增量测量**；CPU 瞬时值亦不能证明无性能影响。HTTP/BODY 并发约束、正式任务优先和无新增浏览器是本轮实际采用的成本边界。

最终读回（2026-11-03T14:40:18.512268+00:00）：隔离测试发布 **113** 条，四项零泄漏/零模型检查、既有配置哈希及七角色镜像身份再次通过；七个端点都已在实际 cycle 中读到新增观测字段。GN All 最新一轮列表 20 项、窗口交集 19、delta 1，端点耗时 3.621 秒，BW/GN Public/PRN Business 有自然 304；这些只证明实际运行及缓存/增量路径，不代表长期覆盖验收。

| 角色 | 当前产品 commit | 状态 |
|---|---|---|
| v2-message-bus | 7e63af94 | running / restart 0 |
| v2-content-enrichment | 7e63af94 | running / restart 0 |
| v2-message-collector | d6cf95ee | running / restart 0 / healthy |
| v2-projector | 91824dd8 | running / restart 0 |
| v2-api | 91824dd8 | running / restart 0 / healthy |
| v2-scheduler | 91824dd8 | running / restart 0 |
| v2-web | 91824dd8 | running / restart 0 / healthy |

采集窗口元数据仅记录在第四类型的既有 cycle 中；正常生产源的台账形状不变。代码检查期间发现并修复一次观测导入遗漏后重新运行 31 项联合回归，错误版本未发布。

## 日常查看与评估边界

在远端执行（只读）：

```sh
sudo docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery status
sudo docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery export --limit 50
sudo docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery export --source globenewswire_massive --limit 50
```

启用脚本 `scripts/ops/enable_massive_tests.py` 幂等注册五源，重复执行结果 unchanged；不创建真实订阅。暂停用既有源 enabled 控制，不改变测试模式，不将积压晋升 production。当前五源保持启用，继续积累。

- PRN/GN 双候选都保留，尚未替用户强行决定唯一端点。
- Newsfile/PRN/GN 短 feed 不能证明全平台无漏稿；ACCESS 当前只有首屏 20 项，没有实现翻页追赶。所有覆盖均 PARTIAL。既有采集回执新增响应大小、端点耗时（request_seconds 包含本地并发等待）、列表起止时间和无交集风险（风险不是已证实漏抓），供后续窗口评估。
- BW RSS 发现成功，但公开正文仍不可用，来源正文验收未通过；不能将其 RSS 摘要判成 FULL，也没有用昂贵浏览器批量兜底。
- 14 天详情留存，需要更长期评估时先用 CLI/同库离线查询导出。发布日期→first_seen、BODY/正则等待、来源重复贡献等可由既有文章/来源/投递/物理请求台账计算，未新建汇总仓库。
- 后续按方案连续三日、覆盖至少两个完整交易日再评估平台覆盖、正文质量、时效和人工相关度；本轮完成的是开发与测试启动，不自动正式订阅或进入下游。

证据目录：[eval/massive-development-20261103](../eval/massive-development-20261103/)。保留 canary、正文样本结果、配置前后快照、组件身份、测试输出及发布样本摘要；原始大 XML/HTML 和完整公开正文保留在本地研究目录，未大包纳入产品提交。
