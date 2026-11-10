# 消息源第二轮故障：依赖停服、X11 残留及恢复验收

日期：2026-11-10（北京时间）。修复方案见 `dev_plan/workflow_v2/message_sources_dependency_outage_repair_plan_20261110.md`。

## 本次不是“上次统计正确所以用户看错了”

上轮 UTC 23:41—23:46 的采集回执存在，但只是该窗口。用户再次反馈时，当前生产确实已经重新失败。此次重新读取当前采集事实库、Docker 退出状态、健康记录、系统日志、API 数据读取路径，不把上轮成功回执当当前事实。

### 事故链与已知证据

| 北京时间 | 事实 | 意义 |
| --- | --- | --- |
| 07:41—07:46 | 上轮 Collector 发布后的自然采集恢复 | 不足以覆盖后续事故 |
| 08:15—08:18 | Site Access / Supervisor 健康检查反复调度/执行超时；主机后续 load average 的 5/15 分钟值超过150 | 存在严重主机压力，不能根据退出码臆断 OOM |
| 08:16 | sudo 日志记录另一维护停止 Scheduler/Worker | 本轮不启动或修改这些维护中的角色 |
| 08:19 | Site Access exit143、Supervisor exit137；两者 OOMKilled=false、HasBeenManuallyStopped=true、RestartCount=0 | 两个共享依赖被 Docker 记录为显式停止，不是网站同时触发风控 |
| 08:25 | 83 个采集 Source 中，14 个至少一个 required 单元失败 | 大量 ConnectError 的当前直接原因是内部 RPC 服务不运行 |
| 08:30 | 仅启动原有 Supervisor 和 Site Access；两者同 namespace 且 healthy | 无需清 Profile、改节点或全栈重启 |
| 08:31—08:38 | 大多数恢复，但 SDxCentral 仍 chrome_start_failed | 继续追查恢复链下一层，不能就此交付 |

没有找到直接停止 Site/Supervisor 的完整调用来源；旧 Docker events 未返回对应时间段事件，sudo 日志只明确记录了 Scheduler/Worker 的 stop。不能据此指认某个人/维护任务停止了它们，亦不能把“没有容器 OOM 标记”外推成所有历史资源问题均不存在。事实边界是：当前共享依赖已停，且 Docker 的显式停止闩锁为真。

`unless-stopped` 不会把显式停止的容器自动拉起。现有 network guard 也只在两者运行且 Supervisor 健康时修复 namespace，退出则 DEFERRED；Compose depends_on 不是运行期依赖恢复器。Collector 自身仍 healthy，因为 HTTP/RSS 等其他通路仍能工作。由此形成了“部分通路和进程活着、大批依赖该服务的源持续失败”的机制。

本轮没有增加一个与人工维护对抗的无限自动复活循环。后续若维护流程停止了这些角色，维护收尾必须按依赖顺序恢复并做真实采集验收；应用代码无法保证任意外部停服后业务仍可用。

## 确定性代码缺陷：X11 锁与 PID 复用

SDxCentral 的失败栈为 ensure → Supervisor → Chrome 启动失败，并非站点 HTTP/Challenge。实证：

- `/tmp/.X104-lock` 保留 PID369，当前 PID369 的 comm 是 ThreadPoolForeg，并非 Xvfb；X104 socket 无法连接。
- 以正式运行 UID 启动独立 Xvfb :104，立即 exit1，明确输出 `Server is already active for display 104`。试跑没有打开网站、改 Profile 或删除锁。
- `_reserve_display` 只排除内存中的 `_instances/_starting`。容器被停止后临时文件留存、PID 又被复用，它会不断选择同一个坏显示号；普通重试不能改变结果。

修复只令分配器同时跳过已有 X11 lock/socket，用 lexists 覆盖断链。不猜 PID 归属、不删除不确定锁、不清 Chrome Profile、不新增常驻组件。五行以内的实现；回归模拟活跃、启动中、旧锁、旧 socket 同时存在并验证选择真正空闲的显示号。

首次临时 start 是为了恢复业务；随后为这项已确认缺陷进行一次必要的 Supervisor/Site Access 配对更新。发布前无 OPEN 人工维护会话；保存待办索引，原持久 Profile 和既有截图卷保持，Chrome 活页不会跨重建保留。丢失页面遵守现有 instance/target fencing，不伪称原页已恢复。

## 诊断边界修复：共享 RPC 不是网站网络

SiteAccessClient 的 resolve/execute 若连接内部 owner 失败，原始 ConnectError/ConnectTimeout/PoolTimeout 以前容易与 publisher 故障混淆。现对原异常附加 `reason_code=site_access_unavailable`，沿用 Collector 已有 reason_code 持久化通道；异常类型、重试次数、GET 同 ID 断连重试、任务总预算均不改变。

网站返回的结构化 403/Challenge 结果不改类，不把它们改绿，也不把 publisher 自己的网络异常统一改成内部服务失联。发布转换期间自然出现的内部不可用已按新原因码记录，不能将该窗口算成网站拒绝。

## 已发布版本与业务边界

- 客户端修复 `9a38f508`：只发布 Collector / Enrichment，UTC 00:36:03 完成，两角色源码哈希核对一致。
- X11 修复 `05d60622`：配对发布 Supervisor / Site Access，UTC 00:41:56 完成，两角色源码哈希核对一致；新容器 `9bc69ac8eab0 / 929d1ee74af9`，实际 namespace 相同，健康检查通过。
- API、Bus 和另一会话维护中的 Scheduler/Worker 未因这两次发布更新；Profile/代理映射、Cookie 持久卷、账号配置、监测词、订阅、目标频率、24 小时采集安排、准入时效/闭市 sweep、相关性及交易规则不变。普通 recheck 仍 false。
- 聚焦回归 **57 passed、1 skipped**（包含先前40项，不累加）；跳过项为当前 Windows 账号无法创建符号链接的既有测试。Ruff 严重错误与 diff 检查通过。

## 剩余源分类

- Reuters：有真实搜索成功，也仍有单身份/队列与 Challenge 恢复问题；当前不是全部源共用的锁死，不为清零放宽保护期或强行提高并发。
- DCD energy 两个 company 入口：持久端点回执为 TimeoutError；Collector 再发布已刷新客户端，不能再把它归为旧连接池。保留端点不可用证据，不借题改监测范围或无限加重试。
- Calcalist：近期持久回执明确 `calcalist_search_http_403`，不是内部服务 ConnectError。是否仍失败以最后自然窗口为准，不伪装成功。
- SpaceNews 等偶发真实429：保留 Retry-After 与既有节奏，不通过取消限流规则制造绿色状态。

前端 API 的 collection-plane flag 与路径已核对正确，未发现本次读退休 poll 表的证据。已另在 API 容器使用其实际 binding_states 读取路径复核，与采集 owner 状态对应；没有改前端颜色或伪造成功。没有读取用户登录令牌或绕过用户鉴权来访问网页。

最终窗口结果、各 ticker 读取结果、正文和复发记录在收尾时追加。短期运行验收不等于保证长期零故障；直接 stop 调用者的审计缺口仍明确保留。


### 独立正文验收（不混入自然消息统计）

使用业务 TechnologyBodyReader，对已存在的 SDxCentral 文章进行一次只读正文提取：HTTP200、FULL、1,735字符。未创建 EnrichmentJob、未向 Message Bus 发布测试新闻、未调用相关性模型；该结果单列为 canary，不冒充窗口内自然新增文章。首次尝试因近期 capture 已被正常回收而未发请求，随后只读选取既有 live article 完成这唯一实际 BODY 请求。


## 最终十分钟自然验收

UTC 2026-11-10T00:41:57—2026-11-10T00:53:56.355006+00:00，仅最终 X11/Site Access 版本窗口；之前依赖恢复及发布转换窗口单列保留，不混入本窗口。

当前 83 个有 required 单元的 Source，79 个全部当前单元成功。以下保留逐源状态，不把瞬时恢复冒充长期保证。

| 本轮初始失败 Source | 当前成功 / 当前单元 | 当前剩余错误 |
| --- | --- | --- |
| barrons_ticker_news | 20/20 | 无 |
| ctee_semiconductor | 2/2 | 无 |
| dcd_energy_ticker | 0/2 | publisher_endpoint_backoff |
| digitimes_semiconductors | 5/5 | 无 |
| globenewswire_search | 27/27 | 无 |
| globenewswire_semiconductors_rss | 5/5 | 无 |
| investing_ticker_news | 10/10 | 无 |
| investorshub_ticker_news | 10/10 | 无 |
| reuters_site_search | 16/31 | site_queue_timeout |
| sdxcentral_news | 5/5 | 无 |
| spacenews_rss | 1/1 | 无 |
| trendforce_news | 5/5 | 无 |
| trendforce_press_releases | 5/5 | 无 |
| yahoo_finance_news | 30/30 | 无 |

最终仍异常：dcd_energy_ticker: publisher_endpoint_backoff; dcd_infrastructure_rss: publisher_endpoint_backoff; reuters_site_search: site_queue_timeout; calcalist_search: RuntimeError。

业务 BODY 回执（不含上面的独立 canary）：[{"final_site_id": "generic", "outcome": "FULL", "reason": null, "n": 3, "last": "2026-11-10T00:47:52.435659+00:00"}, {"final_site_id": "generic", "outcome": "PARTIAL", "reason": "incomplete_extract", "n": 1, "last": "2026-11-10T00:43:11.872373+00:00"}, {"final_site_id": "generic", "outcome": "UNAVAILABLE", "reason": "empty_extract", "n": 6, "last": "2026-11-10T00:50:32.346597+00:00"}, {"final_site_id": "generic", "outcome": "UNAVAILABLE", "reason": "render_required", "n": 1, "last": "2026-11-10T00:49:30.224221+00:00"}, {"final_site_id": "generic", "outcome": "UNAVAILABLE", "reason": "transport_timeout", "n": 1, "last": "2026-11-10T00:42:02.493813+00:00"}, {"final_site_id": "yahoo_finance", "outcome": "FULL", "reason": null, "n": 2, "last": "2026-11-10T00:47:17.732502+00:00"}]。

完整去重 run、API 实时读取结果和分段证据见 `eval/message_sources_repair_20261110/second-acceptance-summary.json`；发布回执为 `dependency-release.json`、`x11-release.json`。不计之前旧成功，不将 CAPTURED run 视为新文章或发布数量。


## 验收未通过项：共享主机资源压力

最终窗口结束附近，SSH/只读检查明显变慢；Safety Controller 00:53:49 的实测为 PRESSURE，host_available=907,292,672 字节（约865MiB），memory.current=30,075,035,648 字节，memory PSI full avg10=52.45、some=53.2，oom_events=0。另一维护此时已重新启动 Scheduler/Worker；这是时间相关证据，不据此把完整根因归为某个具体函数。

这解释了服务本身恢复后仍可能出现跨源 Timeout/前端查询迟滞。数据事实是多数源在该窗口仍工作，终态79/83 Source全当前单元成功；不能把这个结论扩大为整机长期稳定已经通过。内部 RPC 错误分类和 X11 启动缺陷已修复并发布；共享主机的资源峰值、直接停服调用来源尚未完全闭环。已向用户确认并行 Runtime 维护状态，本轮未为消息源验收擅自停止或改变其他维护的模型/交易并发。

最终判定：多数消息源恢复与确定性内部缺陷修复成立，**整机持续稳定性未通过**；资源压力相关超时仍是明确剩余风险。需要与并行 Runtime 维护协调后复验，不用修改前端状态、清 Profile 或重复重启来掩盖。
