# Overview 双列布局与错误降级修订

日期：2026-11-26。状态：本地验证完成，发布结果在下文补充。

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

待补充固定版本、三角色镜像核验和错误派生状态刷新结果。计划使用既有锁定发布 Owner 的小覆盖层，只替换 API/Projector 的上述两个源码文件和 Web 静态产物，不重启 Runtime Scheduler、Worker、交易执行器或采集服务。旧错误健康状态按现行 ACK/Bus 事实一次性重投影，不伪造 NORMAL、不改原始采集台账。
