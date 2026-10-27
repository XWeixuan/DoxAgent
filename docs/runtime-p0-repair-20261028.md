# Runtime / W3 P0 修复实施与基础验收

依据：`dev_plan/workflow_v2/runtime_case_latency_w3_timeout_root_cause_and_repair_plan_20261028.md`（含四项 Bus review 修订）。

本轮实现不改 Prompt/Skill、模型路由、交易资格、原冻结输入和既有模型重试策略；没有增加 795 秒阶段期限或跨尝试共享 600 秒限制。

## 实现

| 范围 | 交付 |
| --- | --- |
| R0 恢复与 admission | 已有 task/Effect 的恢复先于新入站，不依赖最新 ACK；当前版本安装证明与心跳 TTL 分离，旧 Case 继续依原冻结身份恢复。Bus 配置安装只有一个控制任务，心跳独立维护；在线 Repository 不隐式建库/迁移，配置 head 做 CAS。生产 Runtime 也采用进程生命周期心跳 |
| R1 短事务 | claim 只领租约，冻结在工作线程中、写锁之外进行；输入准备有可恢复标记和 CAS。调度仅读 task 标量，不展开所有排队正文；续租不读正文。移除 CASE 嵌套心跳，global state 读取不占 writer。短锁等待与显式 SQL 单元重试在 owner 外退避 |
| R2 异步隔离 | W3 物化/ReceiptWorker 的同步控制操作、Worker 调度/状态/事件及 API 读取移出 loop；按数据库划分有限写队列，物理写未完成不能因取消释放写槽。目录走校验过的索引，已知读取筛选组件与 snapshot；行情快照也离开 loop。保持缓存上限，修复并发驱逐竞态，旧索引原地补齐派生校验信息 |
| R2 / R3 SDK 期限与 owner | SDK 派发时开表，Capsule 在原 SDK 任务中执行 timeout；父进程监督同一 deadline。真实完成事实先于可选 usage/probe 工作保存，晚到回执按实际完成时点接回。外层等待超时保留原 phase Future，后续接回，不盲目新派发；Effect 到期按 lease_until，token CAS 拒绝旧 owner |
| R3 / R4 已付费结果 | W1/W2 hot lane 各自 copy_context，父 owner 等两 lane 收束。Provider 派发/完成采用原 attempt 不可变身份，在 SQL turn 索引前原子保存完成凭证；SQL 失败只接回结果。未知派发显式保留，不自动发第二次请求；业务提交继续受租约和控制 fence 约束 |
| R5 时延与状态 | Case 明细展示接纳至决定/结束、已记录调用区间并集和未覆盖时间，失败/重试不覆盖；W3 缺失 SDK 时长显示未记录。保留事务 operation、锁等待/hold/commit，loop lag 标量及有界异步诊断；Scheduler 有小型健康快照 |
| R6 发布 | 使用已提交的源代码构建，逐组件设置 release revision 并核对源码摘要；按原 Case/job 清单恢复，不重置全部失败计数，不新建历史 Case |

## 基础验收

- 核心 Runtime/W3/MCP/激活回归分批完成；其中核心集合 124 项通过，Worker 资源/取消集合 21 项通过，后续专项继续覆盖改动后的路径。集合存在重叠，不相加宣称测试总数。
- 新增机制测试覆盖锁外冻结、准备中断接回、ACK 过期/覆盖、按库写隔离及取消、已付费结果复用、未知派发、SDK 完成早于回执、原 Future 接回与 hot lane lease context。
- 前端 schema 生成、TypeScript 检查、生产构建通过；1262/1559px，正常与后置失败分支共四项浏览器测试通过。
- `test_server_resource_envelope` 因本轮开始前已有的未提交 Compose 改动缺少 memswap_limit 而失败；该配置未纳入本轮提交与发布。其余 Worker 集合 21 项通过。
- 只做基础发布验收，不将 mock、低峰或短窗口称为生产 p95 <5 秒已达标；不保证 SDK p95 <100 秒。

## 发布与恢复记录

发布前只读清单：`eval/runtime_p0_repair_20261028/preflight.json`，UTC 2026-10-27 19:38:11：282 条活动 CASE task、19 条活动 Effect、39 条未完成 Case；Worker 无 running job，仅一条 queued 的 O3 integration。数量是全局存量，不能当作需要重跑的 Case 数。

正式发布与源码校验结果将在本节补充。当前段落不构成生产已发布声明。

证据目录：`eval/runtime_p0_repair_20261028/`。保留其他会话的脏改动；没有人工派发历史消息、调用券商或手工重写业务状态。
