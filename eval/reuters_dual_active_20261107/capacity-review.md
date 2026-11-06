# Reuters 双活首轮容量检查

观察 UTC 2026-11-06T20:48:36Z 至 2026-11-06T20:53:18.011676+00:00；此阶段尚使用旧全站1500ms间隔，不能作为最终修复验收。

- Main真实CRAWLER成功44次、Backup成功81次；队列延期67次。
- 当时活跃26 QUERY + 15动态TAIL，全部目标60s；旧全站40/min无法承载全部需求。
- Main首次自然使用20:48:51UTC锚定5分钟升载。原有恢复时间未改，备用未重新进入保护期。
- 原route指针仍reuters-backup/generation970；两个Chrome实例不变，无新恢复事件。
- 同一成功operation未观察到跨身份重复成功；正式BODY新样本为0。

各ticker完整QUERY集合次数（TAIL单列于JSON）：

| Ticker | 完整集合次数 |
|---|---:|
| INTC | 2 |
| BE | 2 |
| CEG | 2 |
| COHR | 2 |
| IONQ | 3 |
| MU | 3 |
| NBIS | 4 |
| ONDS | 1 |
| RKLB | 2 |
| SNOW | 5 |
