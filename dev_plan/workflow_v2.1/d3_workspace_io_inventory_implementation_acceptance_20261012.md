# D3 内部通信恢复与 inventory 减负：实现与验收

日期：2026-10-12。依据 `d3_workspace_io_inventory_repair_plan_20261012.md` 实施，仅修改本地代码与本次验收文件；未连接远端、未启动模型任务、未提交或推送 Git、未部署。已有其他会话的 BE 运行记录与未跟踪工件保留。

## 1. 已实现的恢复边界

新增 `src/doxagent/workflows/codex_document3/workspace_io_v21.py`，作为 D3 v2.1 独立 workspace 适配器。HTTP view 复用原连接及 capability，但每次只执行一次底层传输，避免叠加共享 GET 三次重试与 D3 自身预算。D1/D2/v2.0 的原接口默认行为保留。

- 仅实际 `httpx.TransportError` 和 HTTP 502/503/504 进入恢复；沿异常 cause 判断，不依赖错误文本或笼统 retryable 标志。401/403/404/409/500、非法路径、内容完整性错误直接暴露。
- 每个逻辑 I/O 操作有 900 秒总窗口，退避为 2/5/10/20 秒，随后每次 30 秒；请求超时与等待受剩余窗口约束。活动窗口、次数、耗时和最后失败写入原 run JSON；健康文件操作不逐次写数据库，不增加表。
- 取消直接传播。窗口耗尽抛出 `InfrastructureRecoveryExhausted`，正式 durable 层记录 `manual_resume_required`；进程重启沿用原 deadline，不能通过重启自动取得新的 15 分钟。
- PUT 响应不明时先读回同路径确认同值；同路径可变写用锁贯穿确认与重试，防止旧重试覆盖后续写入。不可变路径冲突仍由原 store 拒绝。
- snapshot/fork/publish 复用原确定性身份与幂等语义，不另造研究任务、release 或版本。

`runner_v21.py` 持久化 PREPARE → WAIT_WORKER → COLLECT_OUTPUTS → COMMIT_OUTPUTS → COMPLETE：终态 Worker Job 先落库，再收集输出；验收文件集合及其内容先冻结为 `pending_acceptance`，然后写快照与宿主产物。崩溃后先恢复 I/O 阶段；有 pending_acceptance 时直接提交，不再次选择文件或运行研究。请求结果未知时沿原 request/idempotency 查询；真正研究失败且没有可用产物时，才沿原模型 attempt 规则继续。

末轮审查额外补齐了方案要求的两个外层边界：

1. `ticker_initialization/invocation.py` 在正式 durable 的前置 snapshot 之前绑定本次 D3 run；`substeps.py` 不再对已由 D3 管理的通信或研究预算再套一轮通用重试。权限/完整性错误也不会借外层机制重入。
2. 初始化与 Maintain 在读取/准备材料前持久化 INPUT_PREPARE。Maintain 读取基准 run 的产物时，通信恢复记录归属本次维护 run；中断后占位状态可继续准备，不能误作已有完整 prepared。

这些是原方案恢复边界的具体落地，没有扩展到模型、调度并发或业务提示词改造。

## 2. inventory 与索引减负

Worker 增加受原 service auth、run capability、路径限制及 DiskBudget 保护的 `POST /v1/workspaces/{run_id}/inventory-query`。`paths` 只接受精确文件，`prefixes` 只接受明确目录；单批上限分别为 512/32，客户端分批合并去重。空集合返回空结果，缺失文件不伪造 metadata，所有返回文件仍实际计算 SHA。没有 hash 缓存、虚拟文件系统或远端全目录 fallback。

D3 的前后 inventory 仅查询本次 frozen 文件与声明的输出文件/目录，不再扫描整个 context、历史 attempt、旧发布或相邻 wave。仍检查全部有效输入的 hash；输出 GET 后再次比较查询所得 hash，拒绝收集期间发生的内容变化。失败 Job 不能把同 hash 的旧输出当作新增成果；JPEG 等二进制不经文本接口验收。

共享 context_index 增加 `detail=source`，默认仍为 full。新 D3 任务固定使用 `d3-source-v1`：顶层材料、完整正文、reports、章节及分页 reader 保留，取消材料内部对象递归生成的 units/records 快捷副本。空快捷 catalog 如实返回空，不冒充深层索引。没有删正文、缩减参考材料或修改 prompt/skill。

profile 参与新 immutable 索引身份。已有 request 的旧在途任务缺省使用 legacy-full，旧冻结请求及路径不改；新任务才使用 source，旧终态继续按快照 replay。

publish 新参数 `release_only=False` 保持共享默认；D3 显式传 True，仅返回当前 release 的四个产物与 manifest。原四文件正文/hash、manifest、版本 reservation 和发布提交校验保留。

## 3. 故障与兼容验收

新增 `tests/test_d3_workspace_io_v21.py`：42 项故障注入、范围及兼容测试，不发模型请求、不等真实 15 分钟。覆盖输入查询/PUT、输出查询/GET、快照与宿主写入、终态 replay、丢响应、进程中断、原请求幂等、deadline 跨进程保留、取消、错误分类、并行 owner 状态合并、旧任务 profile、Maintain 前置读恢复及索引 reader。

关键断言包括成功 Job 不增加、request/idempotency 与 attempt 不变、pending commit 不重新 inventory、相邻文件不 hash、缺失/篡改输入拒绝、发布响应丢失仍只有一个 release，以及 formal durable 不放大预算。末轮另验应用自身 TimeoutError 不会被误判为总窗口耗尽、实际窗口到期会取消在途请求。

| 验收范围 | 结果 |
|---|---|
| 新增 workspace I/O 与索引测试 | 42 passed |
| workspace transport + D3 repair + publication | 37 passed |
| D3 v2.1 orchestration | 34 passed |
| D3 v2.1 maintenance | 8 passed |
| context_index + codex_runtime_v2 + worker_resource_budget | 98 passed |
| 正式内部 rerun + 初始化 substeps/recovery + transport 定向组合 | 通过；与上述组合有重叠，不重复计总数 |

开发期间测试暴露并已修正：直接 helper 未绑定恢复 scope、旧 fake 缺少 snapshot 能力适配、正式 durable 外层重试放大预算、Maintain 读取基准前归属错误。reader 新测试最初用过短正文触发原有 report catalog 长度条件，已改为真实长正文 fixture 验证；未为通过断言改变共享 reader 规则。

修改文件的 Ruff 与 compileall、Git diff 空白检查通过。`orchestrator_v21.py` 全文件 Ruff 仍有 4 处修改前已存在的 E501 长行；本轮新增/修改行无该问题，未为格式清理扩展业务差异。测试仅有第三方 opentelemetry 弃用警告。

## 4. 离线规模对比

原始结果：`d3_workspace_io_inventory_validation_20261012.json`。使用同机、等规模合成材料：250 个 unit × 每个 4 个嵌套 value，完整 UTF-8 正文；没有重跑 INTC。完整正文重组及 hash 校验通过。

| 指标 | full | source |
|---|---:|---:|
| 派生文件/需保护文件数 | 11,354 | 257 |
| 派生文件实际 hash 字节 | 49,104,958 | 7,817,740 |
| 索引构建毫秒 | 431.18 | 74.38 |
| 本地 inventory 毫秒 | 111,073.74 | 3,739.51 |
| 按 512 路径估算 HTTP 查询批次 | 23 | 1 |

文件数减少 **97.74%**，超过方案 50% 目标。批次为基于路径数的计算值；规模样本实际使用本地 store 查询。毫秒值是本机单样本对比，不能承诺生产耗时或把历史 span 之和算作节省。hash 字节仅统计派生文件，原材料正文仍完整保留。

## 5. 恢复与交付边界

短时故障自动恢复；900 秒耗尽后，服务恢复并由操作人员决定继续时，可对原数据库实例执行 `StateV21(db_path).reset_io_recovery(original_run_id)`，然后以新进程/Runner 实例进入原有同 run 恢复入口。该显式方法仅清空活动 I/O 窗口，不改任务、request、Job、attempt 或发布版本；不要启动新研究 run，不要改成功任务状态。在同一存活适配器内手工清库不会清除其已加载窗口，因此必须使用新实例。

本轮验收证明本地实现与模拟恢复边界，尚未在生产或正式模型端到端运行。后续如部署，必须同时更新编排 client 与 Worker 实际 `/app/src`，使新增 inventory-query 契约一致；不能只换挂载路径而保留旧 MCP/Worker 镜像源码。

模型设置、owner/wave 调度、并发设置、D1/D2/v2.0 默认、prompt/skill、Atlas 与研究业务合同均未改动。远端生产及正在进行的初始化任务不受本轮影响。
