# 五个轻量 ticker 初始化恢复与定时观测完成

北京时间 2026-11-27 10:10 终态核验：CGEM、RGNX、PRAX、XENE、SMMT 全部 SUCCEEDED / VERIFY_READY，已激活，Bus / Runtime ACK 有效。Control 均 RUNNING，initialization_failed 与 initialization_incomplete 均为 false。当前没有 RUNNING / QUEUED 初始化。

| Ticker | 已激活事件库 | 当前 ACTIVE 事件数 |
| --- | --- | --- |
| CGEM | v1 | 11 |
| RGNX | v1 | 2 |
| PRAX | v1 | 3 |
| XENE | v2 | 12 |
| SMMT | v1 | 22 |

CGEM 根因：IBKR 数据路由在所有来源处于冷却期时抛出裸 RuntimeError，越过工具失败契约，中断 D1。修复为具名异常，工具装饰器返回原有 retryable ibkr_gateway_unavailable；不伪造行情，不改模型提示词或交易执行。8 项相关测试通过。代码提交 ab39686fe，已仅对 Initialization 服务部署单模块 overlay。CGEM 在原 run 中使用原剩余重试接续 D1，原 cutoff 不变；没有另建初始化。

发布时 RGNX D1、XENE O2 使用既有 INTERRUPTED 与冻结回执恢复，均最终成功；未重建 Codex Worker，也未重跑整轮。PRAX/XENE 前序修复详见 lightweight-initialization-recovery-20261127.md。

按本聊天 timer 每次休眠 45 分钟。检查时间为 04:52、05:37、06:23、07:08、07:54、08:39、09:24、10:10。用户在休眠期间补充 SMMT 后，继续睡完剩余窗口，未提前查询。每个完整休眠窗口内均未轮询生产状态。没有创建 cron、后台自动化或常驻监控器；全部成功后停止定时观测。

状态与恢复成功不表示每个外部研究来源均可用；历史 Benzinga 401 等来源限制未被包装为研究质量全覆盖。

证据：artifacts/initialization-watch-20261127-*.txt、artifacts/cgem-router-release-20261127.log；最终标量业务核验为 artifacts/initialization-watch-20261127-final.txt。
