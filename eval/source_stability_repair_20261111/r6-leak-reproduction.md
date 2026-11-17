# R6 准入许可泄漏复现

旧代码：164883cc2。仅本地asyncio确定性回归，不请求网站。

- test_shared_child_survives_callers_closed_network_timer_without_leaking：父network_timeout先退出、共享子任务后获准，报 RuntimeError: Cannot change state of finished Timeout；许可仍为0。
- test_granted_permits_return_even_if_wait_boundary_raises：注入获准后退出等待边界异常，许可仍为0。

两项在修复前均失败；af5b47b48的319 passed / 1 skipped组合回归包含两项通过结果。修复不增加并发、轮询请求或诊断写入。

这是本轮R2–R5的计时器改造引入的兼容回归，不是将历史故障倒推归因于新代码。旧版本预约活锁另有相反锁序/多竞争者复现。
