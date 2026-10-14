# W3 -32600 生产故障排查与修复（2026-10-14）

## 结论与根因

本次错误的原始内容是 `START_THREAD: JSON-RPC error -32600: thread ... already has an active writer`。W3 创建 Case 时，独立的生命周期 App Server 执行 `thread/fork` 后继续持有新 Thread 的 writer；实际执行研判的 Capsule 使用另一个 App Server，无法 resume 同一 Thread。这是跨进程 Thread 所有权交接遗漏，与模型选择、输出 schema 或提示词内容无关。

远端实际 SDK 双客户端实验确认：客户端 A fork 后，客户端 B resume 必现 -32600；关闭 A 后，B 对同一子 Thread 立即 resume 成功。实验只创建专用探针子 Thread，不发起模型研判或交易。修复后同样实验通过，回执见 `eval/w3_writer_repair_20261014/fixed_sdk_probe.json`。

原测试只覆盖同一 SDK 进程内 fork/resume，未覆盖生产实际使用的双进程交接。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)说明取消最后一个订阅存在闲置卸载等待，因此不能用 unsubscribe 代替立即释放 writer。

## 修复实现

修改 `src/doxagent/codex_worker/thread_lifecycle.py` 的 `AppServerThreads`：

- `thread/fork` 请求结束后在 finally 中关闭生命周期客户端，释放 writer；即使响应丢失也执行释放。已持久化的子 Thread 保留，可按原有机制检索恢复。
- 生命周期请求与客户端关闭共用同一把锁，防止并发请求正在使用的客户端被另一个 Fork 关闭。锁只覆盖生命周期 RPC，不串行化模型任务。
- 下一次生命周期 RPC 按需重建客户端；关闭时先清除缓存引用。

新增 `tests/test_w3_writer_handoff.py` 覆盖正常 Fork、响应丢失、并发生命周期请求。W3 writer/lifecycle/case/model/single-skill/v4 共 32 项回归通过，Ruff 通过。

本地同文件之前已有回测所需 config 参数改动；此次生产文件基于实际线上原版抽取最小修复，未包含该回测改动。

## 生产部署

北京时间 2026-10-14 05:52:39 Worker 重建完成。以实际线上 `doxagent-v2:release-8a8f60f2` 为基础构建 `doxagent-v2:w3-writer-fix-20261014`，仅替换生命周期模块并重建 codex-worker，其他服务不变；未部署工作区尚未完成的回测代码。

线上文件 SHA256：

- 原版：`8a10d9f38869f00e54c98449945d784cc0e3362d2e0f7d38c068268fa7562174`
- 修复版：`72b403c0aecfa3783b10425a640c21e3a059786522b86f80546d2cbbb5974683`

已经核对运行容器文件 hash 与修复版一致。生产 compose `/home/ubuntu/doxagent-release-8a8f60f2/deployment/compose.json` 中仅 codex-worker.image 更新为修复镜像，防止再次普通 up 回退。原 compose 安全备份在 `/home/ubuntu/doxagent-w3-writer-repair-20261014/compose.before-writer-fix.json`（0600，仅远端保留）。镜像构建文件与修复模块同目录保留。

后续完整 git push/build 应包含本次源代码修复；本轮未提交或推送 git。

## 历史任务恢复与验收

准确圈定 14 个受影响 Case / 14 个 FAILED route_to_w3 Effect，累计 42 次 active-writer 失败，清单见 `eval/w3_writer_repair_20261014/affected_before.json`。未将全部历史 W3 FAILED 都归因于本次事故。

先使用现有 `resume-node --execution <原 Effect ID>` 验证一个，再使用相同恢复操作恢复其余 13 个，审计原因 `W3_FORK_WRITER_HANDOFF_FIXED_20261014`。保留原 Case、冻结输入和业务身份，不重新跑 W1/W2，不删除历史 Thread 或篡改阶段结果。具体子 Thread 分配与重试沿用现有注册表及幂等代次规则。

截至北京时间 06:02:47，2 个 Case 决策及后续操作均 COMMITTED，相关 Effect COMPLETED；其余 12 个原决策 Effect RUNNING，包含排队与执行。新创建 Worker 任务无 -32600。部分任务出现基础设施自动重试，尚不能宣称 14 个全部恢复完成。旧 FAILED / FAILED_RETRYABLE UI 状态可能在新阶段提交前保留，不能仅凭旧错误字符串判断修复再次失败。

最新逐 Case 状态保存在 `eval/w3_writer_repair_20261014/recovery_status.json`；恢复入队回执见同目录 `recovery_enqueued.json`。

## 恢复中发现的第二个问题：Data MCP 冷启动超时

释放 writer 后，部分真实任务进入下一步，出现 `-32603 required MCP servers failed to initialize: data: timed out handshaking ... 20s`，并耗尽两次基础设施重试。远端独立 import 测量为 27.889 秒；`-X importtime` 采样总计 38.268 秒，其中 tools.factory 累计 28.822 秒。整机 load average 约 17；Worker 容器无 CPU quota，未发生容器 CPU throttling。测量证明现有 20 秒握手预算低于当时仅导入模块所需时间。

`src/doxagent/codex_worker/sdk_runtime.py` 中仅 W3 的 Data MCP startup_timeout_sec 调整为 90 秒，其他节点仍为 20 秒；required 校验、工具权限及工具调用超时不变。已有 W3 权限测试增加启动预算与 required 断言，5 项测试通过（一个依赖弃用 warning）。此处不进行全工具注册表架构改造。

最终 Worker 镜像为 `doxagent-v2:w3-writer-mcp-fix-20261014`，仍基于实际线上 release，仅包含上述两个模块修复。SDK 模块 SHA256 为 `9534b42b06499e7e4add78cf336c4495060f3b137680371ffb6e8150d7022aec`。base compose 镜像引用同步更新，compose SHA256 `17f8fa0555fa3de6507dee67c2abf0ba34e82fe9d6776767904ee30e464407bc`。

仅对原 14 Case 范围内、因此 MCP 握手超时已 FAILED 的 Effect 追加 resume，审计原因 `W3_DATA_MCP_COLD_START_BUDGET_FIXED_20261014`，回执保存在 `recovery_mcp_enqueued.json`。其他正在运行/待重试任务由现有队列继续处理。

## 最后核验快照

北京时间 2026-10-14 06:13:46：14 个原决策 Effect 中 COMPLETED 2、RUNNING 12，无 FAILED Effect；2 个已完成 Case 的 post_decision_operations 也 COMMITTED/COMPLETED。第二版 Worker 于 06:10:37 启动且 healthy，两份线上模块 hash 均与预期相符。

第二版启动后的 Worker 回执：2 个 RUNNING（已取得 Thread/Turn 并进入模型执行），3 个 STARTING，1 个 succeeded；未见新的 writer 冲突或 MCP 初始化失败。尚未全部完成的 12 个 Case 继续由生产持久化队列处理，本次不宣称它们已获得最终业务结果。

部署重建窗口另有一个原受影响 Effect 因 WorkerUnavailable 耗尽重试，按原 ID `effect_a927b78548e141caa8f66cb7dad4d37b` 恢复；其审计回执原因使用同轮 `W3_DATA_MCP_COLD_START_BUDGET_FIXED_20261014`，不应将它归入前述 6 个 MCP 握手失败 Effect。其后快照确认 RUNNING。

只读核验脚本：`.venv/Scripts/python.exe eval/w3_writer_repair_20261014/check_recovery.py`。该脚本只读取两个远端 SQLite 并更新本地状态证据，不发起任务恢复。
