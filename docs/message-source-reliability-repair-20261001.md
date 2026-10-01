# 六个消息源间歇性故障：定位、修复与生产验收

日期：2026-10-01。以下时间除特别说明外均为 UTC（北京时间加 8 小时）。

## 结论与边界

本次不是六个网站分别不可用。五个浏览器采集入口共享 Site Access，生产完成请求在同一时段停滞；HTTP resolve、旧 readyz、External Chrome CDP 却仍然响应。发现并修复了跨 Identity 队头阻塞、取消泄漏、无界清理和请求预算不一致。恢复后还定位到消息总线同步调度热路径负担，优化重复日历/源定义查询，避免把内部 RPC 连接失败一律归咎于网站。

六个源均已重新取得真实列表或有效 Feed，并在生产自然轮询中出现新的成功记录。正文测试另外确认 TrendForce 两入口与 Reuters 可提取全文，Barron's 有新的正文完成消息。DIGITIMES 英文列表恢复，但当前身份访问新订阅文章仍返回订阅权限门槛，需要人工检查登录/会员权限；不能把列表成功或分发文章 `READY` 当作全文成功。

**证据限制：没有取得最初挂起任务的调用栈，所以不宣称已证明首次触发的具体一行代码。** 下面区分实际观察、确定性复现和仍需人工/长期验证的事项。

## 1. 浏览器源共同停滞

### 生产观察

- TrendForce News、Press Releases、DIGITIMES 英文的最后成功时间均为 `04:20:02`；Barron's 多 ticker 的最后成功也在 `04:18–04:20`。
- Reuters MU 后续一次轮询耗时 `1,814,951 ms`，错误 `search_query_failed / ReadTimeout`；Barron's 多次延迟约 `600,000 ms`。
- Site Access 后续仍响应 resolve/readyz；浏览器 ACCESS_RESULT 最后在 `04:43` 出现约 10 秒的 `site_queue_timeout`，之后没有完成相应浏览器请求。HTTP_PUBLIC 请求仍能完成。
- Supervisor 中 Dow Jones、DIGITIMES Chrome 的 CDP 可响应，目标页没有异常膨胀；容器没有 OOM，实际 PID 数低于限制，`pids.events max=0`。不是内存/PID 配额耗尽导致 Chrome 全部消失。
- 更新时旧 Site Access 进入 shutdown 后仍等不到活动连接收尾，最终由 Compose 的退出期限处理旧访问服务。Supervisor 与其中 External Chrome 没有被重建。

### 可复现缺陷与处理

| 缺陷 | 后果 | 修复 |
| --- | --- | --- |
| JointBudget 先选全局最高优先级等待者，再检查其 Identity 是否空闲 | 一个忙 Identity 的等待者能挡住另一个空闲 Identity | 仅在当前满足 Site/Identity 容量及节奏约束的等待者中排序；不提高同一 Identity 并发 |
| External Runtime 创建页面时只捕获 Exception | CancelledError 绕过 semaphore 归还，页面额度泄漏 | 异常及取消路径都归还额度；补取消后再次申请的回归 |
| gate/page 的 finally 清理没有独立期限 | 业务超时并不保证清理结束，permit/page slot 可能长期占用 | gate 清理 3 秒、页面关闭 5 秒；必要时只用 CDP 关闭本任务拥有的 target，随后归还额度，不关共享 Chrome |
| 访问预算主要覆盖 attempt，客户端默认等 185 秒 | 前置工作、排队、重试和清理叠加成多分钟等待 | 全服务调用覆盖请求预算；入队后重新计算剩余预算；RPC 总限时为请求预算加 15 秒且不因重试刷新 |
| readyz 只验证 driver 等基础状态 | API 看起来 healthy，浏览器任务却已经停滞 | 增加 admin-only `/v1/diagnostics/access`，输出任务等待链、年龄、预算、队列/Identity 活动数；滞留任务使 readyz 降级 |

在安装于生产的旧预算类上，隔离执行的复现结果为 `independent_free_identity_blocked=True`，不改变服务现场。新回归验证空闲 Identity 可正常进入，而共享 Identity 的串行约束不被绕过。Condition 等待改用当前任务的 `asyncio.timeout`，减少额外子任务与锁回收路径；没有将未经复现的 Python 运行时竞态当作已证实根因。

## 2. Reuters 错误必须拆层

`search_query_failed` 是每个搜索问题的聚合错误码，不能直接解释成 Reuters 站点失败：

1. 故障期的 `ReadTimeout` 与共同 Site Access 停滞相吻合。
2. 第一阶段恢复后，仍观察到 `ConnectTimeout`；相应请求没有进入 Site Access，属于内部 RPC 建连层，而非 Reuters HTTP 拒绝或 challenge。
3. 消息总线主线程接近一核满载。15 秒进程外采样及线程栈显示，主线程大量执行同步 SQLite 读取、源定义查询和重复交易日日历判断。采样不读取 locals、令牌或正文。工具工作方式参考 [py-spy 官方说明](https://github.com/benfred/py-spy)。
4. 同一个调度轮对每个 binding 都判断同一个语义日/前一日，并进行数据库访问；源定义也逐 binding 重新打开数据库。现在同轮共享日历结果、批量读取源定义，下轮仍重新检查 override；日历不可用时仍按各 ticker 的已有 schedule 降级。

已确认这是应当优化的同步热路径，能够拖慢网络事件循环；**未把每一次历史 ConnectTimeout 都认定为同一原因**。另外，多 ticker × 多搜索词 × 分页共享单并发入口时，一轮用时可能超过 60 秒。60 秒是目标调度间隔，不是容量足够的保证。本次未盲目提高高风控身份并发、删搜索词或改变实时/闭市窗口。

## 3. HuggingNews 的独立链路

- 生产 Feed 是 `https://huggingnews.com/feed.xml`，使用原有固定代理 `18083`。
- 实测该代理取得 `200`、有效 Atom 和 50 个条目；直连三次为 `403`，因此没有改成直连。
- 过去一天共享采集有 1,472 个 DONE、12 个未完成历史 PENDING 记录。它们是失败/释放留下的 run，不能仅凭 PENDING 断言历史 run 会自动重试；实时下一轮采用新的 slot。
- 没有保留出错瞬间的底层 TLS/DNS/代理异常详情，不能声称已分辨每次 ConnectError 的具体网络触发点。
- SharedFeedAdapter 对 ConnectError、ConnectTimeout、ReadError、RemoteProtocolError 仅重试一次，连接/池等待 5 秒，所有尝试共用 30 秒总时限；不对 403 反复重试，不把失败 Feed 当作成功。

## 4. 第一阶段生产验收

不是只检查容器健康或 HTTP 200，而是经过实际列表解析和生产轮询：

| 来源 | 独立请求实测 | 生产自然轮询恢复记录 |
| --- | --- | --- |
| TrendForce News | 200，8 条，约 1.58 秒 | MU/INTC/BE 新成功；观察到 8 个列表文章，2 篇新文章正文 2,078/3,418 字符 |
| TrendForce Press Releases | 200，5 条，约 1.12 秒 | MU/INTC/BE 新成功；独立正文 6,293 字符 |
| DIGITIMES Semiconductors | 200，11 条，约 3.05 秒 | MU/INTC/BE 新成功；7 篇新文章经过正文流程，但当前均为订阅权限失败后的降级，不能算全文恢复 |
| Barron's ticker news | 手工请求曾因生产同 Identity 正在采集而约 10 秒正常退让 | BE/INTC/MU/RKLB 均新成功；MU 新文 `micron-stock-price-gross-margin-e9d62ae0` 入库并有正文 |
| Reuters Site Search | Micron 搜索取得 20 条，约 4.79 秒 | 四 ticker 均有新成功；MU/BE/INTC 有新 Raw 入库；独立正文 4,309 字符 |
| HuggingNews Atom | 200，有效 Atom，50 条 | MU/INTC/BE 新成功，50 个条目被共享采集观察 |

独立 TrendForce News 正文测试另得到 3,526 字符。上述返回列表包含历史条目，不意味着它们全部发布进实时总线；原有窗口、正文、相关性与分发规则继续生效。

DIGITIMES 实页检查：`digitimes-nl-1` 返回 `AUTH_REQUIRED / subscription_required`，文章 `#content` 仅约 352 字符，没有完整 articleBody。Registry 的旧 `auth_state=VALID` 不能替代当前实页权限验证。请使用登录维护工具打开该身份，重新验证登录和英文会员权限。此次没有删除或复制 Cookie，也没有绕过订阅门槛。

## 5. 发布、测试与保留事项

追加热路径更新后 `11:17`（北京时间 19:17）复验：

- 六个来源所有已订阅 ticker 的当前轮询状态均为 `succeeded`，`last_error_code=null`、连续失败为 0，成功时间已刷新至 `11:15–11:17`。
- Reuters RKLB/MU/BE/INTC 最新轮询延迟分别约 9.4/33.8/31.7/29.8 秒；追加更新后查询窗口没有新的 search_query_failed 记录。
- Message Bus 单次 `docker stats` CPU 采样为 21%，此前约 80–100%。这是短时验收样本，不是全天负载保证。
- Barron's 四 ticker 最近采集仍成功，整轮延迟约 59–71 秒；它的真实卡片等待与共用 Identity 排队仍有成本。本次没有把成功记录的长延迟隐藏为“瞬时采集”。

- 访问层修复提交：`ced70346`；调度热路径优化提交：`392bd994`。
- Site Access：`doxagent-site-access:source-repair-ced70346`。
- Message Bus：`doxagent-v2:source-repair-392bd994`，按 **Dockerfile.v2** 构建；验证 `ibapi 10.49.2` 可导入，Playwright 锁定版本保持不变。
- 正文补全独立容器保留原镜像 `doxagent-v2:enrich-retry-d7b9026b`，以免覆盖其已有专用补丁；服务端访问预算/清理修复同样保护其调用。
- 仅更新 Site Access 和 Message Bus；热路径追加更新只重建 Message Bus。未重启 API、交易服务、Chrome Supervisor。
- 保留远端原部署目录的其他未提交改动，使用干净 build checkout；Compose override 原文件有部署前备份。
- 前后 Chrome 实例一致：Dow Jones `0854db06758b49a68cdce663ce78e922`、DIGITIMES NL `dd5c53cd998f456b93a109ee23c9b637`、DIGITIMES direct `b62de727ea1c4585b40b843130326472`。
- 访问相关测试集先后为 67 passed/1 skipped、扩充取消回归后的相关集为 53 passed/1 skipped；追加调度/消息总线集为 45 passed。集合有重叠，不将数量相加。
- Ruff 与 diff whitespace 检查通过。两处旧编排测试的 fake scheduler 缺少已存在的 distribution_worker 属性，补齐空值夹具，不改变生产业务逻辑。
- 本轮没有全天稳定性测试，也没有自动补发故障期历史新闻。恢复不能保证未知风控或网络故障不再发生；新增诊断与有限等待使再次故障能被隔离和定位。
