# W3 编排层重构实施与验收记录

日期：2026-10-19（上海）。依据：`dev_plan/workflow_v2/w3_orchestration_refactor_final_plan_20261019.md`。

本轮实施的是本地代码、隔离能力测试与迁移工具。未部署生产、未触发生产 Case/历史重放、未改业务数据库、未执行存量生产数据删除。交易输出仍为 `persistent-runtime.w3.v4`；新入口为 `w3-entry-v5`。

## 1. 已实现的五组改动

| 项目 | 实现位置 | 实际行为 |
| --- | --- | --- |
| 一次交付合同 | `persistent_runtime_v2/w3.py`、`codex_worker/sdk_runtime.py` | 母 Template 加载 AGENTS 与唯一 Skill；Case/运营 turn 继承。当前输入直接交付，task/schema 仅落审计文件，不再把这些路径作为新版必读入口。结构化输出 Schema 仍由 SDK 下发。 |
| Policy 内容与状态 | `persistent_runtime_v2/w3_context.py` | HIT、RELATED、有效 WATCH 关联的完整 Policy 与其使用状态内联；全体冻结 Policy/使用状态可经 MCP 自主发现。`ASSOCIATED_ONLY` 明确表示内联状态不代表全库状态。 |
| 共享研究与读取 | 新增 `w3_research_store.py`、`mcp/w3_research_server.py` | 既有 Worker 内常驻 HTTP MCP，统一 `catalog/search/read/market_view`。每次显式传 `context_ref`。共享原件、编译视图、索引及冻结状态，不再每 Case 复制 D1/D2/Policy/Reference。 |
| 后台行情 | 新增 `codex_worker/w3_market.py`，扩展 `tools/providers/ibkr_tws.py` | 固定行情按 ticker 缓存并预取；Case admission 即启动独立 fine 准备，与 SDK 并行。单连接、最多两个在途请求、30 秒刷新、非阻塞读取。交易提交/失败/期限结束后退出。 |
| SDK/Fork 与确定性预算 | `capsule_child.py`、`capsules.py`、`thread_lifecycle.py`、新增 `w3_ready.py` | SDK 真初始化后才报告就绪；全局两类 Template、Luna 2/Sol 1 READY 库存及一个 SDK 热壳。领取固定身份、后台补池、原 operation 恢复，避免正式 Case 临时启动 Data MCP/Fork。 |

普通 Case 继续 Luna/max，Policy HIT 继续 Sol/medium；本轮没有更改交易判断、OLD/WATCH/低置信路由、标签权限、Policy 消费和并发交易受理规则。AGENTS/Skill 仅适配研究、行情与分页调用方式，清除了旧 Python 行情入口与新接口的冲突。

## 2. 研究资产与冻结版本

- 原件优先引用既有校验过的 `published-contents/<sha>`，否则仅保存一份 W3 共享原件。
- `.w3-packs/v2/<source/renderer identity>/` 保存 `objects.json/index.json/manifest.json`，manifest 最后原子提交。D1 按主要章节，D2 按 Shell/Unit，Unit ID 使用既有名称。
- D2/Policy 移出 `ref`，保留所有业务字段、未知字段及缺失/null 区别。重复表使用 `columns/rows/missing`，来源与完整原件单独可读。D1 保留链接标签及业务正文，引用标记/URL 移到来源视图。
- Policy origin 仅使用可验证的既有 ref；能精确关联的 Unit 可直接读取，未关联不推断、更不限制自主研究。
- `context_ref` 对应 Case/phase 不可变 manifest；Policy 使用、历史意图、消息、事件、WATCH/PIN 等按内容哈希共享冻结快照。不同阶段各自冻结，旧 Case 不被新发布覆盖。
- 发布原文缓存通知只登记待编译项；Worker 后台构建。Runtime 公共准备每 30 秒补偿当前版本，冷的精确 Case pin 由单飞后台任务准备，重试继续原 pin，不在 stage 内全量构造或偷换版本。
- 派生索引解析失败时，精确原件仍以 `raw_document` 可读，并提供 `INDEX_UNAVAILABLE` 及错误；原件缺失/哈希不一致不伪装成可读。

### 完整性与输出预算

每次返回一份 TextContent，整个批次共用 16 KiB / 4096 Token 预算，SDK 对各工具配置 8192 Token 输出上限。页包含内容范围、complete、next_cursor、正文哈希及 END 收据。cursor 绑定 context、读取对象/view/parts 与不可变正文；可缩小 max_bytes 续读。行情续读固定首次读取快照，更新元数据不会导致中途换页内容。配置能力依据[官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)，并经同版 SDK 实测。

这保证接口可以完整读取，不强迫 Agent 读全部资料。Agent 私下扫描后只回显摘要与外层截断是两回事，测试分别保留证据。

## 3. 行情语义与资源

默认视图：五个完成交易日日线、过去 24 小时小时线、过去四小时五分钟线。SMART 与 OVERNIGHT 使用实际合约 venue；intraday 请求 epoch 时间，避免 TWS 登录时区歧义。不填造缺失 K 线，形成中 bar 与完成 bar 分离，返回覆盖、来源、缺口、观察时间、陈旧及错误信息。

`market_view` 立即返回已有部分；首次获取中标记 PREPARING，失败/停止则明确不可用。`since_version` 避免仅刷新完成时间变化就重交相同 bar；每次实际交付保留精确证据快照。较早事件可用 event_time 请求披露前 30 分钟、后 60 分钟的异步补充；补充不改变交易方向使用权限。

单连接由 Worker 持有，两个请求槽位覆盖阻塞调用的真实生命周期。取消 Case 不会在底层请求未结束时释放槽位；断线单飞重连并退避，关闭时等待已有调用结束。固定数据按完成日/小时更新；闭市不会持续重复请求同一历史行情。交易/失败停采，并由 deadline 兜底。

离线回测在私有本地端口运行同一 MCP，禁用 Web/Broker；只读数据集文件，保留已有十分钟线及日线的真实粒度和截止时间，缺少小时线明确说明。未修改本轮历史数据集或 gold。

## 4. 准备、切换与恢复

- Python/SDK 就绪时间分别记录，FORK/MCP 就绪才进入 READY。健康 READY 不再每 300 秒退役；资源安全回收后相应记录不能继续宣称 READY。新入口子 Thread 设置 `project_doc_max_bytes=0`，避免 cwd 变化自动重复注入母 Thread 已交付的 AGENTS；母 Template 正常加载，不影响压缩后必要的主动回读。
- Slot 包含 profile/template、原 run/thread/capsule、generation、时间和 POOL_HIT/POOL_MISS；同 Case/record 绑定幂等。Worker 成功绑定而 Runtime 尚未落盘时仍返回同一身份。
- 原 Fork request（包括 cutoff）持久保存；Worker 重启后绑定但未完成的 Slot 继续该原 operation。先用落盘身份收据，再做精确 cwd/parent 的分页查询；软超时保持同一个底层操作，硬超时清理 owner 后才恢复。
- 恢复用生命周期客户端常驻，初始化和 RPC 有界。若子 Thread 已存在但驻留进程已失，先 Resume 原 child 和工具，不再 Fork 第二个 child。
- 新公共版本两种 profile 至少各一份 READY 后才成为该 ticker 的新 Case 入口；准备期间冻结旧入口。旧版本按需恢复不会替换当前公共预热库存。全新部署无旧版本时要等 W3 readiness；这些等待仍计入真实 Case 时延。
- 历史意图改为批量取持久业务记录/Case，保持 stage accepted_at 与 72h 成员冻结。SQLite Case 提交删除同事务后的重复 save；不改变交易受理事务。

## 5. 生命周期与迁移命令

新增 `python -m doxagent.codex_worker.w3_resources`，命令需要显式 `--root`；示例路径应替换为目标环境真实路径。

```text
inventory --root <worker-root> --runtime-db <runtime.sqlite3> --report <report.json>
retire --root <worker-root> --report <report.json>
retire --root <worker-root> --report <report.json> --apply
restore --root <worker-root> --run-id <old-w3-run> --apply
references --root <worker-root>
gc --root <worker-root>
extend-context --root <worker-root> --case-id <case-id> --days 7 --apply
restore-archive --root <worker-root> --run-id <w3-ready-run> --apply
```

旧副本 inventory/retire 默认 dry-run，只有已知 W3 Workspace、无活动/可恢复依赖、存在哈希相同共享源的命名文件可退役。apply 同时锁住既有 Runtime/Worker 写入、重查保护、先保存恢复映射，再退出副本；输出/审计/唯一文件不删。此功能已在临时夹具验证，未在生产执行。

新版终态 Workspace 由现有受管 Thread 生命周期退出；Case 控制/输出先归档，小型 PREPARED 废弃目录直接退出。共享派生视图按活 context、最新热版本和 24 小时宽限保护，Worker 每小时执行 W3 自有派生 GC，每批最多 64 项并设 250ms 批次时间预算；唯一原件、冻结状态、决定与实际行情阅读证据不由此 GC 删除。默认 context 原地访问七天，过期后可显式延长、从精确原件重建视图；`references` 供其他 owner 审查保留源。

配置通过 `.env` 进入已有 Worker：`DOXAGENT_W3_RESEARCH_MCP_URL`、`DOXAGENT_W3_MARKET_REFRESH_SECONDS=30`、`DOXAGENT_W3_READY_LUNA=2`、`DOXAGENT_W3_READY_SOL=1`。同容器默认 MCP URL 为 `http://127.0.0.1:8791/mcp/w3-research`；现有 service env_file 可覆盖，不额外增加 Compose 固定值。tiktoken/o200k 缓存已加入镜像构建，运行时不需下载词表。

## 6. 已完成的验收与证据

证据目录：`eval/w3_orchestration_implementation_20261019/`，包括脚本、失败尝试和成功回执；未用成功结果覆盖首次失败。

1. 无模型回归：扩大集合已通过 **265 项**，涵盖新旧 W3、Policy、模型选择、SDK/Worker、IBKR、离线回测、分页及资源恢复；JUnit 为 `regression.xml`。最后配置/合同/GC 增量复核 **64 项通过**，见 `incremental.xml`（与前者重叠，不合计为 329 个独立测试）。Ruff/compile/lock 检查通过。
2. 真实 SDK/MCP：Luna/max 六页合成 Unit 全部正文哈希与外层 END 收据通过；Sol/medium 的 40k Token 单行中文 Unit 经实际 Code Mode 读取，范围连续、所有页正文哈希和外层收据通过，未观察到外层截断。最后一次有 14 次 MCP 读取、13 个唯一分页范围（包含一次重复读取），不将模型自报页数当校验依据。`sdk_visibility_receipt.json` 可逐项核对。
3. 保留的中间实验：首轮因工具 approval 默认策略没有真正调用 MCP，已修正只读工具 approval；另一次模型将后半页私下汇总，20 次工具读取只有十份外层收据，该次不算全文可见性通过。增加强约束 QA 后取得上述完整通过证据。
4. 真实零模型 Capsule/Fork/死亡恢复：最新 `capsule_fork_smoke_20261018T175707Z.json` 中，SDK_READY 前导入与初始化共约 1.93–1.95s，发生在预热；热 Fork 到工具就绪 Luna 328ms/Sol 250ms。进程退出后收据接回约 78/47ms；再从热 SDK 壳 Resume 到工具就绪约 172/125ms。仅隔离合成 Thread，没有发生产模型任务。
5. 远端只读 IBKR：夜盘、盘前、盘中三个历史时点分别取得五条完成日线、23 条小时线、47/45/47 条完成五分钟线；请求组约 7.147s（首次连接）、2.702s、2.019s，已移出交易 stage 等待路径。覆盖缺口如实保留。首次实测暴露 resolve_stock 返回列表的问题，已修复，失败记录仍保留。
6. 20 Case 并发夹具验证共享 fixed、最大两请求、非阻塞 PREPARING、取消/退出；验证市场快照跨刷新续读、旧源回收与恢复、Worker 重启绑定接回，以及本地回测使用新 MCP 且不调用券商。

7. `capsule_fork_smoke_20261018T180247Z.json` 增加故障注入：Luna 的同一个真实 Fork operation 人为延迟 11 秒，越过软超时后正常接回、底层只 Fork 一次；随后两 profile 都直接 kill 本轮自有 Capsule 再接回原 child，未新建 child。该故障样本完整保留，不计作正常路径速度结果。
8. `contract_inheritance_receipt.json` 审核实际不同 cwd 的 Fork：母 Thread 各交付一次 AGENTS/Skill，子 Thread 新增次数均为零。SDK 使用 delta history，第一次 QA 只扫描 child 文件而误报零；沿 `forked_from_id` 核验继承链后各为一次，原失败回执保留。最终复核同时保证 attempt/cutoff 变化不触发 Resume，而 sandbox/有效 MCP 配置变化会改变 runtime fingerprint；禁用 stdio 工具的旧 attempt env 不再混入新版 fingerprint。

本机 C 盘在扩大测试时耗尽，产生过 ENOSPC/SQLite 写入失败；该次没有计作代码验收。仅迁移本轮已结束的隔离临时目录至 D 盘，随后在 D 盘重跑取得通过结果，其他会话文件未清理。

## 7. 上线后必须补的真实结论

当前闭市，历史时点的 readonly 行情测试不能证明连续开市 30 秒更新下的 Gateway 稳定性；仍需观察真实 session 切换、错误码、并发和恢复。同样，隔离 Fork 的毫秒级结果不是生产 Effect→commit 时效。

部署新镜像与 Scheduler/Worker 后，先检查 release、两 profile 的 `/v1/w3/readiness`、入口 ACTIVE、POOL_MISS、context/manifest 及首批自然流量工具可见性。保留旧 Case 原版本，存量清理最后做，并先单独审核 inventory。

自然流量按 release/entry/case_type/ticker/意图分组，保留失败、partial、重试和空池等待；统计 Effect→commit 以及扣除全部交易 SDK 区间并集后的非 SDK p50/p90/p95。**非 SDK p95<5s、全链路 p95≤100s 尚未在生产验收**；不能以本地模拟、历史行情或单次热 Fork 代替。现有 opt-in turn probe 与逐 response context/token/工具体积记录继续保留。
