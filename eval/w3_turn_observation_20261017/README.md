# W3 SDK turn 观测发布与自然流量验收

初版 release：`a3713de3d9f63405587751df44af87f4e788587a`；最终生产代码 release：`3c30365a0586959f632d1a62041d2b6ccdd7ea0c`。补修的存储/ACK及结果恢复由41项相关复测和新版本自然Case的ACK后引用确认。

- `tests.txt`：222 项相关回归。
- `parser_benchmark.json`：只验证现有 34 份轨迹 fixture 的投影，不调用模型、不重放业务；最大 3.954 ms、紧凑原生投影最大 31,435 字节。真实生产端到端开销以自然回执为准。
- `build_remote.py`：提交 main 的 git archive，经标准 Dockerfile.v2 构建。
- `deploy_remote.py`：隔离镜像导入检查；旧 Scheduler/Worker 平滑退出并先替换 Worker，后替换 Scheduler/API/Projector。保留其他容器与业务数据。
- `release.json`：实际镜像、提交、时间和前后容器身份；配置私有备份不导出。
- `health.json`：四个发布服务实际 running，Worker/API healthy，日志异常行计数。
- `collect_remote.py`：生产只读采集器，沿用 doxagent-sg 与现有 run_remote.py，禁止创建消息、重放或调用券商。
- `natural_samples.json`：本次真实自然流量回执、Worker 身份和业务结果，不包含原始工具/网页正文。
- `natural_report.md`：由生产 probe report CLI 生成的业务分组、工具、逐轮上下文表；有完整样本后生成。

远端私有发布目录：`/var/lib/doxagent/w3-turn-observation-20261017`。生产独立回执目录：`/data/workspaces/w3-observation/receipts`。

采集从北京时间 2026-10-17 02:00:54 开始，至当天 10:00:54 自动停止。探针有效开关及容量上限见 `control.json`；可以随时 disable，不需要重启。已设置同会话 15 分钟一次的采集验收；达到 20 条完整、同时覆盖 HIT/非 HIT，或截止后关闭并汇总。其余时期保持安静，不人为补样本。

后续自动采集验收的 automation id 为 `w3`（应用自动化，不是业务 Thread）。导出 report 后统一保留一个结尾换行，避免生成器 print 增加空白行。
