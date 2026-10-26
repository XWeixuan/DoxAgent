# W3 启动与持续运行鲁棒性修复实施记录

日期：2026-10-26。依据：`dev_plan/workflow_v2/w3_startup_robustness_audit_and_optimization_plan_20261026.md`。

## 交付范围

完成方案 A—D 的轻量修复，覆盖准入等待、事件流、物理资源收尾、job 终结、dispatcher 自恢复及公平退役。没有扩大 SDK 预热池、冷启动并发、握手并发或内存阈值，没有增加常驻服务或模型调用。Prompt/Skill、交易资格、原 Case/slot/operation/child 身份合同保持现有规则。

本轮为本地实现与故障验证，**未部署生产，未执行生产存量清理或历史 Case 重跑**。原方案要求正式上线另行授权，因此不把本轮测试表述为生产验收。

## 实现与行为边界

| 方案 | 实现 | 关键边界 |
| --- | --- | --- |
| A：准入取消 | `startup_admission.py` 用同任务 `asyncio.timeout` 包住初次取锁与等待；移除 `wait_for(Condition.wait())` 的独立子任务 | 等待者只能撤销自己的队列项；owner 在任何可取消 await 前同步释放自己的许可。保留优先级、FIFO、绑定提优、压力谓词与 cold=1 |
| A：事件流 | `jobs.events` 同任务等待，锁内只记心跳状态，退出通知锁后 yield | SSE 的慢消费者、背压和重复取消不会持有全局状态通知锁；序号及续读合同不变 |
| B：单一收尾 | 新增 `cleanup.py`，每个 Capsule/helper 只有一份 cleanup task，close/release/reap 的重复调用复用它 | 等待者重复取消不丢弃物理收尾；收尾失败优先报告隔离错误，成功后再传播调用者取消 |
| B：有限关闭 | Capsule/helper 优雅发送与退出共享最多 1s；随后执行现有身份校验 TERM/KILL，总关闭预算最多 15s | 错误、断管或发送 drain 卡住仍进入强制终止；只有确认原 owner 消失才 CLOSED。SDK helper 保留 SDK 清空引用前的原 Popen，禁止对不归它所有的进程组发信号 |
| B：批量与隔离 | 每项独立关闭，一项失败继续处理后续；批次取消时恢复未执行的 busy 预留 | 未确认退出的单项保留 identity、PID、错误和 QUARANTINED，30s 后由原 reaper/dispatcher 复核，helper 不依赖新请求来解除已退出 owner。未收束的物理冷启动继续阻止第二次物理冷启动 |
| B：job 终结 | 单一 settlement task 保存待提交结果，落盘错误按 1/2/5/10s 退避，仅重试收尾 | 不重跑交易 turn；终结未确认期间不调度同一 job。SQL 已成功、镜像或事件失败时不再次消耗基础设施重试代数 |
| B：ACK 竞态 | 终态确认压缩请求后，凭 SETTLED 释放原调度占位；物理隔离只保留原资源查找 key | 未确认物理退出时仍隔离；原资源退出后即使请求已压缩也能释放占位，不保留第二份 prompt |
| C：调度监督 | `start()` 识别已结束 pump；恢复读取或 tick 异常后在现有 pump 内按 1/2/5/10s 退避重试 | 不需要新 HTTP 请求唤醒；进程恢复完成前不放行。可选观测 GC 在原有小时周期内单独运行，失败不杀死 dispatcher |
| C：可用性诊断 | `/v1/resources`、`/v1/w3/readiness`、`/healthz` 增加 dispatcher 状态 | healthz 仍为存活检查；readiness 同时要求 dispatcher 已恢复和原 READY profile 条件。空池、普通压力或模型耗时不会触发自动重启 |
| D：公平退役 | `w3_ready.py` 每轮最多 4 条，使用游标；待确认行冷却 60s，不再永久挡住后续行 | 先存覆盖式小清单，再按原身份核验。确定未派发、无活 owner 的空库存结束原 operation；未知 child 不当作不存在，不重新 Fork |
| D：原 child 接回 | `retiring_child` 核对原 run、已注册母线程、child、回执或精确 cwd 状态索引，再补生命周期归属并删除 | 活跃/排队 job、归属冲突、非唯一结果均保留；Case 决策、交易意图和审计证据不重跑、不删除 |

实施中另修复一个已复现的取消竞态：`wait_for(runtime.start())` 在启动完成与取消同时发生时可能吞掉取消，随后进入无期限的业务 run 等待。启动/执行改为同任务超时，并在启动交接处检查取消与 Worker 关闭状态，防止关闭时继续进入 turn。该处理不改变正常 turn 的预算。

## 性能、内存与本轮取舍

未发现 A—D 中必须通过显著增加服务器资源才能完成的修复，采用下列轻量实现；不引入昂贵兜底：

- 退役只使用原回执及 `useStateDbOnly=true` 的精确 cwd 查询，状态查询预算 10s、单条核验与删除外层预算 15s。不扫描 rollout 正文，不追加新的全历史研究资产读取；查不到则保留不确定状态，后续公平扫描继续推进。
- 关闭 task 仅在资源关闭时创建，一份 owner 对应一份；job 收尾也只保留一份。失败的 SDK Popen 使用异步 poll 等待，不把无法退出的 Popen.wait 放在线程池里持续占用线程。
- 不增加热进程数、内存额度或正常轮询频率。隔离复核只在有异常 owner 时执行，间隔至少 30s；不每轮扫描 `/proc` 或全库正文。现有强制终止时的身份扫描保持原机制。
- 新诊断为少量内存标量和小 owner JSON 覆盖写；resources 只计算现有队列/池和最多 8 个 owner 摘要。退役清单最多 4 条覆盖存储，不留工具正文和 stderr，不新增常开全量探针。
- `retirement.pending` 是候选总量，`scanned_unowned_creating`、`scanned_needs_confirmation` 是本批统计，不假装代表全库已完成身份核验。全量实际处理随游标推进。

本地健康路径微基准（每种情况 5 次，Windows/Python 3.11，未运行 SDK）：

| 项目 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 单路 2,000 次准入，中位耗时 | 10.975ms | 11.051ms |
| 20 路竞争、共 500 次准入，中位耗时 | 14.162ms | 47.951ms |
| 同批竞争的 tracemalloc 峰值 | 97,728B | 87,184B |
| 回收后本次追踪仍保留内存 | 591B | 591B |

竞争基准有约 34ms 的累计增加，折合每次准入约 0.068ms；本轮没有将其掩盖为零开销，但这一绝对量级与实际每次物理启动的秒级工作相比不构成显著服务器负担。此结果仅用于排除本地合成路径的大额开销，不能外推为生产 RSS、SDK 启动或端到端时延结论。可重跑脚本和原始 JSON 包含基线 revision、源码 SHA 与各次结果。

## 验证

新增 `tests/test_w3_robustness.py` 的故障覆盖：18 并发等待者重复取消（2 次/4 次，各 20 轮）、初次取锁超时、SSE 心跳暂停与取消、关闭发送卡死、多个关闭者重叠取消、冷启动失败期间保留许可、有限关闭隔离及再次核验、批量回收首项失败与批次取消、dispatcher 初次恢复/tick/可选 GC 故障、已结束 pump 重建、终结写入失败/ACK/重试代数竞态、公平退役与原 child 回执归属冲突。

真实进程验证使用本地临时目录和两个私有 Python 子进程：一份不读取关闭协议的进程通过生产 Capsule.close/terminate_owned 路径退出，另一份独立 sibling 保持存活，最后由测试结束它自己的 sibling。没有调用模型、券商或生产业务库。SDK helper 测试另覆盖 close 清空 `_proc` 后仍使用原进程身份收束，以及 SDK_READY 前后隔离对冷启动准入的不同影响。

完整关联回归 **106 passed**，耗时 119.54s，仅 1 条既存 OpenTelemetry 依赖弃用 warning，见 `eval/w3_robustness_audit_20261026/implementation-final-tests.xml` 和对应 `.log`。最后补充 helper 隔离自动解除测试后，再运行整个新增故障集与原生命周期集，**28 passed**，耗时 13.05s，见 `implementation-fault-tests.xml` 和对应 `.log`。两组去重共 **107 项**，新增故障测试共 26 项。未把重复执行次数计入通过项目数。

完整命令（PowerShell，关闭无关自动加载插件）：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv/Scripts/python.exe -u -m pytest -p asyncio tests/test_w3_robustness.py tests/test_w3_startup_admission.py tests/test_w3_deterministic_repair.py tests/test_w3_ready_v5.py tests/test_w3_writer_handoff.py tests/test_worker_resource_budget.py tests/test_w3_market_v5.py tests/test_w3_resources_v5.py tests/test_w3_boot_protocol.py tests/test_w3_thread_lifecycle.py tests/test_terminal_session_retention.py -q --tb=short --maxfail=5 --junitxml=eval/w3_robustness_audit_20261026/implementation-final-tests.xml
```

Ruff 检查所有本轮修改的源码和新增测试通过；`git diff --check` 验证补丁空白通过。没有用历史成功样本替代取消故障验证，也没有用一条自然 Case 推断生产可靠性。

## 上线后验收边界

正式发布需依原方案单独进行：先核对发布源码/镜像，再检查 dispatcher READY、cold/helper 队列、清理隔离与原 READY profile；利用自然 Case 核对交易与后处理落库，同时保留全部失败和 pending。`GET /v1/w3/retirement/preview` 是带服务认证的只读入口，不会启动 SDK 或清理存量。

正式部署后原有维护周期会开始按本次修复处理可确认的无主退役库存；未知归属仍保留，不通过扩大删除范围消除 pending。没有新增可远程任意删除 child 的接口。若物理 owner 仍无法在预算内退出，将保持显式隔离；本轮不以清空许可、增加启动次数或重启整个 Worker 绕过这一事实。
