# Overview 双列布局与错误降级修订

日期：2026-11-26。状态：已发布并完成生产回读验证；产品源码 `0135854037c96b25cbcdd7be91af922024abc606`。

## 两项修订

1. `frontend/v2/src/styles.css`：交易模式与运行模式复用同一个双列规则，不新增组件或标签；交易模式的第三项放第二行。
2. 删除单消息源 `partial/failed` → ticker `DEGRADED/SOURCE_POLL_FAILURE` 的整个聚合规则。保留激活/消费者 ACK 等运行状态依据，以及无激活且初始化失败的 BLOCKED。来源页面继续显示来源自己的真实故障。

修改 `src/doxagent/v2_read/health.py` 与 `projectors.py`：健康投影仅由 Control、ACK、Bus 生命周期触发，只读这三个小集合；源轮询不再触发 ticker 健康计算，不再扫描 ticker 所有绑定和全局来源定义。没有新增轮询、框架、表或常驻资源。

## CEG 原因

实际 Read 投影中，`CEG:reuters_site_search` 的旧 `native:poll_states` 为 `partial`，最后尝试是 **2026-10-27T11:03:48Z**，连续失败数为 0；旧规则仍将它解释为整个 ticker 降级。它不是当前采集链路的最新状态。

当前 acquisition 台账显示 Reuters 在 2026-11-26T06:58、06:59Z 的已完成采集为 CAPTURED，无失败 reason；随后调度为 SCHEDULE/DISABLED。此次没有修改来源开关、原始失败记录、交易权限、初始化或 ticker 模式，也不据此宣称全部来源始终正常。

## 本地验证

- 后端相关回归 **21 passed**：`artifacts/ui-health-refinement-tests-20261126.log`。验证源事件不读健康快照、不产生 ticker 健康记录，消费者 ACK 正常/缺失与真正初始化阻塞仍分别表达。
- 前端 **78 passed / 17 files**；schema/typecheck/build 通过。日志：`artifacts/ui-health-refinement-vitest-20261126.log`、`ui-health-refinement-build-20261126.log`。
- 桌面浏览器 **8 passed**，1262/1559px；实际检查两个单选组均为双列，原轻量页面/链路与 Overview 行为保留。`artifacts/ui-health-refinement-browser-20261126.log`。

## 发布结果

已使用既有锁定发布 Owner 的小覆盖层，只替换 API/Projector 的上述两个源码文件和 Web 静态产物。

| 角色 | 实际镜像 |
|---|---|
| API | `sha256:a102e3fb25821893e0fd8877b44508cc152bece7ddc769f9d07fb894e2e34f16` |
| Projector | `sha256:e237e7d2174ff201e5b3bb2099bfab91e8335af8380b7768b3afe15ca3c07ee7` |
| Web | `sha256:04df0ab1052e004a92b52c1d3310b4f542bfe88f4402d194f63d82d3b5fa1d9d` |

- 三角色正常运行、重启数均为 0；API/Web healthy。两后端文件以及 41 个 Web 静态文件全部与构建产物哈希一致，实际镜像与发布台账一致，原有效环境配置保留。
- 与上次发布收据比对，其余 13 个角色的容器 ID 和镜像均未变化，包括 Runtime Scheduler、Worker、交易执行器及采集角色。
- 对仍有 SOURCE_POLL_FAILURE 的当前 ticker 只做一次派生投影刷新，实际仅命中 CEG；复用现有 `health.project` 与 `ReadStore.ingest`、现行 ACK/Bus 事实，结果为 **NORMAL，health_reasons=[]**。没有伪造 NORMAL，没有改原始采集状态、启停 ticker 或修改交易配置。
- 通过已连接 DoxAgent MCP 的正常认证 Overview 回读，CEG 为 RUNNING、LIVE_TRADING、NORMAL，健康原因为空。真实读端仍标记 STALE，未将既有投影新鲜度问题包装为已修复。
- 无新增模型请求、测试订单、初始化或运行模式转换。

证据：[角色与产物核验](../artifacts/ui-health-refinement-verified-20261126.json)、[一次性投影刷新](../artifacts/ui-health-refinement-refresh-20261126.json)、[认证 Overview 回读](../artifacts/ui-health-refinement-ceg-api-20261126.json)。回滚镜像及私有有效环境沿用 `/var/lib/doxagent/releases/dynamic-ownership-0135854037c9` 的现有发布记录。没有把临时发布辅助脚本加入产品执行链。

## 后续纠正：隐藏多余的“正常”标签

上一轮保留了生命周期健康为 NORMAL 时的附加标签，导致 CEG 显示一个用户未要求的“正常”。已对所有 ticker 统一隐藏 NORMAL/UNKNOWN 健康标签，只显示运行模式及 DEGRADED/BLOCKED 异常；不针对 CEG 增加特殊分支，也不改后端状态。原组件一处显示条件调整，没有新增标签或组件。

前端 78 项、1262/1559px 浏览器 8 项通过，构建通过；新增断言确认普通/轻量正常行只有运行模式标签，“正常”为 0 个，而初始化阻塞仍可见。日志：`artifacts/overview-normal-label-{build,vitest,browser}-20261126.log`。

已仅发布 Web，产品 `abc14080d780ec5ce791e0812d395e92d4af190e`；镜像 `sha256:1c524d1e9d23a128dc2432c34ba9f19e30d5ee458024213214f8ca61d32776db`。Web healthy、restart=0，41 个静态文件摘要匹配，其余容器 ID/镜像未变，没有修改后端健康数据。见[核验收据](../artifacts/overview-normal-label-verified-20261126.json)。
