# 消息源再次集中失败：共享依赖停服与状态机制修复

日期：2026-11-10，北京时间。此报告补充并修正上轮验收边界：07:41—07:46 的回执确有恢复，但不能说明此后持续可用；用户反馈时已发生另一轮共享依赖事故。

## 已确认机制

1. 08:25 新基线：83 个有 required 单元的 Source 中，14 个至少一个单元失败；核心错误转为 ConnectError，而非上轮的代理 TLS 35。Site Access 与 Chrome Supervisor 都已在 08:19 退出，Collector/Bus/Enrichment 仍在运行。它们是多个爬虫、部分 RSS 通路和 BODY 的公共依赖，因此大量不同网站同时不可用。
2. Docker State：Site Access exit 143、Supervisor exit 137，两者 OOMKilled=false、RestartCount=0；Docker 私有运行状态两者 HasBeenManuallyStopped=true，restart=unless-stopped。因此不能根据 exit137 就声称 Chrome OOM；Docker 显式停止闩锁令它们在压力缓解后也不自动恢复。
3. 08:15—08:18 两者 healthcheck 多次调度/执行超时；同窗主机负载曾超过150，另一个维护任务在08:16发起停止 Scheduler/Worker。现有审计日志没有保留直接停止 Site/Supervisor 的完整调用来源，Docker events 旧事件已不在返回窗口；不把停服调用者写成已查明。当前资源安全状态 NORMAL、可用内存约26GiB，具备单独恢复消息源依赖的条件；不启动维护中停下的 Scheduler/Worker。
4. 既有 network guard 只在两容器运行且 Supervisor healthy 时修复 namespace；任一退出则 DEFERRED，timer 不会恢复显式停止的容器。Compose depends_on 只负责启动顺序，不是业务依赖自动恢复器。这解释了“Collector healthy、消息源仍集体失败”。保留人工停服语义，不引入一个会反复与维护操作对抗的自动重启循环。
5. SiteAccessClient 把内部 RPC 无法连接直接抛为原始 ConnectError/ConnectTimeout，使网站错误与共享服务失联混淆。采集错误持久化已有 reason_code 通道，可直接复用，避免新增状态机。
6. API 的 collection-plane flag、数据库路径已核对正确，当前不是读到退休 poll 表；本轮不凭用户看到失败就先修改前端颜色。

## 精益执行项

### P0：恢复现有依赖，不重建浏览器身份

- 保存容器退出时间/显式停止标志及健康失败证据。
- 对现有 Supervisor 容器执行 start，待其健康，再 start 原 Site Access 容器；不改镜像、卷、节点、Profile、账号、注册策略或消息采集频率。
- 核对实际 net namespace 与 HostConfig owner；必要时仅复用现有 network guard 重绑 Site Access。原 Profile 保留；已随停服丢失的活页不可能靠 start 恢复，不伪称现场还在。
- 确认现有出口与真实自然采集恢复，不手动改绿，不补发已过时文章，普通 recheck 保持关闭。

### P1：准确归因内部依赖故障

- 在 SiteAccessClient 的 resolve/execute HTTP 边界，对内部 RPC 的 ConnectError/ConnectTimeout/PoolTimeout 给原异常加 `reason_code='site_access_unavailable'` 后原样抛出；不改变异常类型、重试次数、任务预算和失败/成功判定。
- 网站真实 403/Challenge/解析失败与服务返回的结构化 AccessResult 保持原样；不能把 publisher 自己的网络错误改成共享依赖错误。
- 定向回归覆盖 resolve/execute 三种连接类失败、正常恢复、既有 GET 断连重试及原异常身份；运行现有 SiteAccessClient 测试。
- 仅发布需要这项客户端改动的 Collector 与 Enrichment；不重建 Supervisor。保留另一会话的 Runtime 维护状态与代码。

## 验收与收尾

- 分别核对依赖进程/namespace、前端实际 API 绑定状态、完整自然采集窗口和 BODY 结果；不以 /readyz 单点或一个 ticker 成功替代业务验收。
- 至少覆盖数轮及十分钟自然窗口，中间允许瞬时失败；终态与窗口内历史分别列出，Reuters 等真实 Challenge 可以保留。
- 查当前仍失败项的持久 endpoint/网络回执，优先修明确内部故障；不为清零提高上游允许年龄、放宽订阅限制、降低目标频率或增加高频探测。
- 更新 changelog、main 提交与必要角色精确版本发布，记录重启前后 ID、镜像、Profile 保留及仍待人工事项。明确此前完整审计缺口，不虚构“永不复发”。
