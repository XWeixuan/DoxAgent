# Reuters 双活最终版本自然运行验收

代码 `3dcf8b34`，Scope revision 3；窗口 UTC 2026-11-06T21:42:32Z → 2026-11-06T21:51:51.993580+00:00（9.33分钟）。

本记录只读持久回执，不请求新闻网站、不调用打码Provider。所有过渡版本及未通过窗口保留在同目录，最终版本未混计旧成功。

- 真实CRAWLER成功：Main 237、Backup 147；不把CAPTURE_CONSUMED计作新访问。
- 延期：CRAWLER 27、BODY 15；逐分钟记录如下，不隐去发布初期延期。
- 当前活跃单元 41（{'QUERY': 26, 'TAIL': 15}）；目标间隔 {60: 41}。
- 当前状态 {'succeeded': 40, 'failed': 1}。
- 正式正文结果 []；为空时表示本窗没有FULL/SHORT_FULL证明，不能宣称正文已验收。
- 同一成功operation跨身份重复成功：0；不据此宣称所有不同operation的网页请求绝对不重复。
- PROBE started/finished 0/0；没有自然探测时，不宣称探测分支生产覆盖。

| Ticker | QUERY数 | 完整QUERY集合 | 当前活跃TAIL数 | 当前非成功单元数 |
|---|---:|---:|---:|---:|
| BE | 3 | 9 | 3 | 0 |
| CEG | 2 | 10 | 2 | 1 |
| COHR | 3 | 8 | 1 | 0 |
| INTC | 3 | 8 | 0 | 0 |
| IONQ | 2 | 9 | 1 | 0 |
| MU | 3 | 9 | 2 | 0 |
| NBIS | 3 | 9 | 2 | 0 |
| ONDS | 3 | 9 | 1 | 0 |
| RKLB | 3 | 8 | 2 | 0 |
| SNOW | 1 | 9 | 1 | 0 |

## 延期时间线

| UTC分钟 | 类型 | 结果 | 数量 |
|---|---|---|---:|
| 2026-11-06T21:42Z | CRAWLER | BUDGET_DEFERRED | 6 |
| 2026-11-06T21:42Z | CRAWLER | SUCCESS | 6 |
| 2026-11-06T21:43Z | CRAWLER | BUDGET_DEFERRED | 13 |
| 2026-11-06T21:43Z | CRAWLER | SUCCESS | 31 |
| 2026-11-06T21:44Z | CRAWLER | BUDGET_DEFERRED | 3 |
| 2026-11-06T21:44Z | CRAWLER | SUCCESS | 46 |
| 2026-11-06T21:45Z | CRAWLER | BUDGET_DEFERRED | 1 |
| 2026-11-06T21:45Z | CRAWLER | BUDGET_DEFERRED | 3 |
| 2026-11-06T21:45Z | CRAWLER | SUCCESS | 45 |
| 2026-11-06T21:46Z | CRAWLER | SUCCESS | 44 |
| 2026-11-06T21:47Z | CRAWLER | SUCCESS | 44 |
| 2026-11-06T21:48Z | CRAWLER | SUCCESS | 45 |
| 2026-11-06T21:49Z | BODY | BUDGET_DEFERRED | 4 |
| 2026-11-06T21:49Z | CRAWLER | SUCCESS | 44 |
| 2026-11-06T21:50Z | BODY | BUDGET_DEFERRED | 6 |
| 2026-11-06T21:50Z | CRAWLER | SUCCESS | 45 |
| 2026-11-06T21:51Z | BODY | BUDGET_DEFERRED | 5 |
| 2026-11-06T21:51Z | CRAWLER | BUDGET_DEFERRED | 1 |
| 2026-11-06T21:51Z | CRAWLER | SUCCESS | 34 |

## 实际完成间隔

间隔取cycles.finished_at，不能把dispatch_at间隔或配置60秒冒充真实完成时刻。TAIL会停用后再激活，跨不活跃期的长间隔不能直接等同漏轮询；失败回执仍单独保留。
- QUERY: 排除首2分钟后有连续完成样本的单元26个，各单元中位间隔范围59.29–61.70s，最大单次间隔66.26s。
- TAIL: 排除首2分钟后有连续完成样本的单元19个，各单元中位间隔范围56.87–65.87s，最大单次间隔116.71s。

## 覆盖边界与资源

- 新恢复事件0，新Provider提交0；旧job更新时间变化不算新增提交，不把旧费用记为本轮费用。
- 双身份实际轮询已覆盖；生产未故意阻断一个身份或注入故障，单身份接替/恢复加入由回归覆盖。短窗无新Challenge不等于长期无风控。
- 本窗未制造RETRY或购买打码，不覆盖打码解码与再次封禁分支。
- 磁盘可用51.04GiB，MemAvailable 12014240 kB，Registry WAL 14885592字节。CPU PSI为单次主机样本，不能归因为该补丁或编造CPU分位。
- 没有新增浏览器、身份、持久队列、截图或全量抓包；日志及页面历史仍由既有治理维护。本轮不是长期GC/内存泄漏验收。

完整字段和各单元完成间隔见同名JSON；实例/PID及真实预算、源码一致性见相应版本的runtime/release JSON。
