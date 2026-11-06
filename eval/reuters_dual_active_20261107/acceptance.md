# Reuters 双活最终版本自然运行验收

代码 `5c95dfdf`，Scope revision 3；窗口 UTC 2026-11-06T21:16:17Z → 2026-11-06T21:23:01.925505+00:00（6.75分钟）。

本记录只读持久回执，不请求新闻网站、不调用打码Provider。所有过渡版本及未通过窗口保留在同目录，最终版本未混计旧成功。

- 真实CRAWLER成功：Main 188、Backup 110；不把CAPTURE_CONSUMED计作新访问。
- 延期：CRAWLER 4、BODY 0；逐分钟记录如下，不隐去发布初期延期。
- 当前活跃单元 43（{'QUERY': 26, 'TAIL': 17}）；目标间隔 {60: 43}。
- 当前状态 {'succeeded': 43}。
- 正式正文结果 []；为空时表示本窗没有FULL/SHORT_FULL证明，不能宣称正文已验收。
- 同一成功operation跨身份重复成功：0；不据此宣称所有不同operation的网页请求绝对不重复。
- PROBE started/finished 0/0；没有自然探测时，不宣称探测分支生产覆盖。

| Ticker | QUERY数 | 完整QUERY集合 | 当前活跃TAIL数 | 当前非成功单元数 |
|---|---:|---:|---:|---:|
| BE | 3 | 7 | 3 | 0 |
| CEG | 2 | 7 | 2 | 0 |
| COHR | 3 | 6 | 1 | 0 |
| INTC | 3 | 6 | 0 | 0 |
| IONQ | 2 | 6 | 2 | 0 |
| MU | 3 | 7 | 2 | 0 |
| NBIS | 3 | 6 | 3 | 0 |
| ONDS | 3 | 6 | 1 | 0 |
| RKLB | 3 | 6 | 2 | 0 |
| SNOW | 1 | 6 | 1 | 0 |

## 延期时间线

| UTC分钟 | 类型 | 结果 | 数量 |
|---|---|---|---:|
| 2026-11-06T21:16Z | CRAWLER | BUDGET_DEFERRED | 4 |
| 2026-11-06T21:16Z | CRAWLER | SUCCESS | 21 |
| 2026-11-06T21:17Z | CRAWLER | SUCCESS | 50 |
| 2026-11-06T21:18Z | CRAWLER | SUCCESS | 47 |
| 2026-11-06T21:19Z | CRAWLER | SUCCESS | 45 |
| 2026-11-06T21:20Z | CRAWLER | SUCCESS | 43 |
| 2026-11-06T21:21Z | CRAWLER | SUCCESS | 48 |
| 2026-11-06T21:22Z | CRAWLER | SUCCESS | 44 |

## 实际完成间隔

间隔取cycles.finished_at，不能把dispatch_at间隔或配置60秒冒充真实完成时刻。TAIL会停用后再激活，跨不活跃期的长间隔不能直接等同漏轮询；失败回执仍单独保留。
- QUERY: 排除首2分钟后有连续完成样本的单元26个，各单元中位间隔范围58.38–63.81s，最大单次间隔67.98s。
- TAIL: 排除首2分钟后有连续完成样本的单元19个，各单元中位间隔范围35.28–103.56s，最大单次间隔103.56s。

## 覆盖边界与资源

- 新恢复事件0，新Provider提交0；旧job更新时间变化不算新增提交，不把旧费用记为本轮费用。
- 双身份实际轮询已覆盖；生产未故意阻断一个身份或注入故障，单身份接替/恢复加入由回归覆盖。短窗无新Challenge不等于长期无风控。
- 本窗未制造RETRY或购买打码，不覆盖打码解码与再次封禁分支。
- 磁盘可用55.36GiB，MemAvailable 16295544 kB，Registry WAL 14885592字节。CPU PSI为单次主机样本，不能归因为该补丁或编造CPU分位。
- 没有新增浏览器、身份、持久队列、截图或全量抓包；日志及页面历史仍由既有治理维护。本轮不是长期GC/内存泄漏验收。

完整字段和各单元完成间隔见同名JSON；实例/PID及真实预算见runtime-final.json；源码一致性见production-release.json。

## 追加TAIL规划诊断

该窗口CEG的103.56s间隔经逐条SCHEDULE回执追溯，确有频繁ENABLED/DISABLED，不应仅解释为正常动态尾页。Collector.binding_state排除了停用TAIL，丢掉done/head_at完成证据，旧HEAD.tail_pending使其反复复活。tail-trace.json保留完整脱敏时间线；补丁仅保留当前仍活跃查询的已完成TAIL检查点用于规划，不执行停用单元。Collector发布及发布后自然时间线单独保存，不能把本窗口当作该后续修复已验收。
