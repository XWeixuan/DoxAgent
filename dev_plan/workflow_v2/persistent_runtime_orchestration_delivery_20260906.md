# Persistent Runtime V2 第二部分开发交付

日期：2026-09-06。依据：`persistent_runtime_orchestration_plan_20260906.md` 及用户确认的 10 项业务决策。

本次交付包含编排实现、SQLite 持久恢复、热更新入口、O4 触发收口、Runtime 交易输出接口、离线故障测试和运维手册。没有执行生产部署、生产监测启停或真实 broker 操作。

## 1. 已实现范围

| 开发项 | 实际落点与行为 |
|---|---|
| P0 时间、日历、兼容 | `semantic_clock.py`、`calendar.py`；ET 02:00、DST、真实交易日历、持久缓存与人工覆盖；新 Case 标记 time semantics v2 |
| P1 接管与恢复 | `journal.py`、`fencing.py`、`heartbeat.py`、Runtime repository/service；Inbox 先落盘后 ACK、逐 round 回执、短租约、迟到写拒绝、原子交易输出、过期交易隔离 |
| P2 版本和热更新 | `execution_bundle.py`、ActivatedRuntimeInputs、W3 runner；固定完整输入与 prompt/skill；manifest hash 验证和原子切换；人工 source/binding 更新复用 Bus 版本化接口 |
| P3 日结与补充维护 | `maintenance.py`、`event_branch.py`、控制库 CAS；O2 独立候选 Event Library、O3 候选 Policy、整组激活；失败保留旧 bundle；迟到输出通过持久通知触发补充维护 |
| P4 Closed Cycle | `coordinator.py`、`bus_orchestration.py`；每日一次逻辑 Poll、源隔离、highwater 接管、W1 顺序、OLD/normal 跳过 W2、普通 W3 逐项结算；02:01 实时恢复不等整批维护 |
| P5 Candidate/选择/交易 | `selection.py`、`trade_output.py`；不预消费 Policy、不伪装已执行 Trade；03:45 快照、0/1、未选永久结案、维护失败 fallback、正式释放再认领；LocalTradeSink 与禁用的 IBKR paper 接口骨架 |
| P6 运维和 O4 | `operations.py`、Runtime CLI、D3/O4 入口；查询、pause/resume、节点或 effect 重跑、迁移 dry-run/备份；只有 ticker 初始化触发 O4 |
| P7 业务内容导入 | 受控 `import-policy-set` 分配新版本并走既有激活；`import-monitoring-config` 和 `patch-source`；不直接覆写历史研究版本、Policy 消费或交易账本 |

主要新增文件位于 `src/doxagent/persistent_runtime_v2/`。第一部分的控制库、激活、CandidateConfiguration、worker idempotency 等实现继续复用；没有引入新的通用 workflow 平台。

## 2. 关键一致性处理

1. **非阻塞**：实时 Case、Sweep、维护、effects 和交易输出投递有独立容量。Source Poll 在 Bus 事件循环中后台执行。失败预算耗尽只隔离失败项；未来日期任务和其他 ticker 继续。
2. **交易恢复**：首次接纳日固定；跨日未释放交易过期。已有 Bus 旧日积压不因恢复当天创建 Case 而取得新交易有效期。分析与 Delta 可继续完成。
3. **整组依赖**：Case 固定 D1/D2/Event Library/D3 版本和 Event Library root。维护的 Event Library 是 copy-on-write 分支，O3 使用候选版本；只有全组可用才推进 Active Bundle。人工激活发生在前时，旧维护 CAS 失败并有界重建。
4. **provisional**：允许读取未吸收的跨日事实，保留原 semantic day。Case 内重新分配不与 canonical 冲突的 E# 别名。O2/O3 成功激活当日 reference view 后才切换可见性；旧日追赶任务不能误切当前日。
5. **候选和正常交易分离**：Candidate 在 O3 feed 中有独立字段；正式释放时才与 TradeRecord、intent 一起消费 Policy。最终拒绝释放不自动改选第二名。
6. **恢复回执**：成功 round 不重跑；worker 请求身份持久化。O3 候选发布后、上层 receipt 前中断，可认领已发布候选。效果终态与迟到维护通知原子落盘，后继任务建立后再确认通知。
7. **长期运行开销**：调度查询只装载活跃任务；补充维护采用持久完成通知，不在每个 tick 重新装载全部历史 Case/维护正文。
8. **人工监测修改保留**：人工替换研究产物且沿用当前监测配置时，从 live 配置生成候选，保留其人工修改；显式回滚或切换其他配置版本仍读取对应历史快照。三种路径均有离线回归。

## 3. 离线与静态验证

最终完整离线回归：**276 passed，3 warnings，118.89 秒**。警告来自现有依赖的弃用/实验性 API 提示。证据保存在 `eval/persistent_runtime_v2/orchestration_offline_20260906/pytest.txt` 和 `verification.json`；后者记录源文件 SHA-256，并确认测试期间源代码未变更。

Runtime 模块及本期新增故障测试 Ruff 检查、Runtime 目标 mypy、compileall、Runtime/Scheduler CLI help 检查通过。完整回归后只整理了三个新增测试文件的 import/格式，并单独重跑这些文件；未修改生产逻辑。

测试入口覆盖 Runtime、W3、初始化兼容、D3、O4、Message Bus、Runtime Scheduler 和 Event Library。所有这类测试使用 `--offline` 和临时数据库。

新增故障测试文件：

- `tests/test_runtime_orchestration_foundation.py`：02:00/DST/holiday、lease fencing、失败预算、不可变 bundle。
- `tests/test_runtime_orchestration_execution.py`：原子输出/跨日过期、Candidate 不预消费、round 恢复、节假日调度、真实新旧 prompt 和跨日事实 pin。
- `tests/test_runtime_orchestration_recovery.py`：过期 worker 拒写、O2 成功/O3 失败、激活后中断认领、0/1 和过期选择、旧维护不能覆盖新激活。
- `tests/test_runtime_orchestration_bus.py`：休市日一次 Poll、慢源不阻塞事件循环、Sweep 等待 Inbox highwater、旧日积压不得交易。
- `tests/test_runtime_orchestration_process.py`：子进程在 W1 完成/W2 未完成时强制终止，重新构建 service 后保留 W1 turn IDs，只续 W2；迁移 dry-run 不写库。

针对关键状态机的测试调用真实 service/repository/coordinator；模型和 worker 用离线替身。没有把 O2/O3/O4/W3 的真实验收混入测试。

## 4. W1/W2 真实测试

真实测试仅限现有固定 MU 语料的 W1/W2，包含需要时的 W1 R3。使用当前配置的 Bailian `qwen3.8-flash`、medium，最大并发 5。未运行普通 W3、Weekend W3、O2/O3 或 O4 的真实节点。

| Run | 结果 |
|---|---|
| `mu-w1w2-orchestration-probe-20260906-01` | 单例 001：3 个成功回合，最终 novelty/Policy/route 符合预期；先验证 provider 可用 |
| `mu-w1w2-orchestration-real-20260906-01` | 25 条均结束处理；24/25 有完整严格输出；其中最终路由匹配 21/24 |
| `mu-w1w2-orchestration-provisional-20260906-01` | 修正 provisional 输入语义后单独复测 024：3 个成功回合、0 retry；OLD/normal、引用 E186/E187、归档路由符合预期 |

全量 run 保留原始结果，没有用专项复测覆盖其中失败记录，也没有将专项结果拼成“25/25 全量通过”。它发生在 provisional 输入语义修正之前；修正后的版本只对受影响的 024 做了定向真实复测。

全量实测的主要未通过项：

- **006**：W2 R1 三次返回无效 JSON，重试耗尽；其余 24 条继续完成。不是整个 workflow 无限阻塞，也不能把该 Case 的外层 `COMPLETED` 字段误当作严格输出通过。
- **022**：Policy 触发判断与 gold 不一致，存在 false activation 风险。
- **023**：novelty 置信度与 gold 不一致，导致路由偏差。
- **024**：原输入未充分说明 provisional 在当前消息之前已知；已补充语义和原 semantic day，专项复测通过。
- W1 R3 全量有 3 项输出仍需独立语义复核；技术调用成功不等同逐事实人工质量通过。

全量已返回 usage：83 turns、5 retry turns、952,476 input tokens、59,057 cached input tokens、111,337 output tokens。只使用 provider 返回的统计，不推算失败调用的隐藏用量。

证据位于：

```text
eval/persistent_runtime_v2/w1_w2_mu_future_v1/runs/
  mu-w1w2-orchestration-probe-20260906-01/
  mu-w1w2-orchestration-real-20260906-01/
  mu-w1w2-orchestration-provisional-20260906-01/
```

各目录保留 `run_manifest.json`、`metrics.json`、`predictions.jsonl` 和逐 Case 结果。评估脚本新增显式 `--prompt-root` override：只改变声明的 prompt 版本，研究输入和 gold 继续验证原固定 hash；新 manifest 记录实际 prompt hashes。同一 run ID 不能混用不同 prompt 版本。

**结论：编排技术验收与模型业务验收分开。真实 W1/W2 的业务结论仍是 PARTIAL，不能以本次编排交付宣称模型质量或真实交易业务已经全部验收通过。**

## 5. 本期边界

- Runtime 输出是本期交易执行终点。IBKR paper 仅接口骨架，没有账户连接、合约解析、仓位换算、风控、订单提交/撤单或成交回报接入。
- 完整历史 Source backfill 继续延期；没有假称停机后可以恢复上游从未返回的数据。
- 代码升级依赖持久目录和进程重启；prompt/skill、绑定配置和受控业务版本支持热更新。旧 binary 任意降级不受支持。
- legacy 未完成 Case 若缺少可验证输入，仅保留并记录恢复缺口，不能发明历史 prompt/head；新增编排的 Case/Sweep/维护具备固定输入和回执恢复。
- 没有清理仍可能被 Case/Candidate/gap 引用的 artifact；生产部署应管理长期存储容量。
- source 全局更新与 ticker binding 更新的作用域明确区分；`patch-source` 会影响共享 source 的所有 binding。

启动、升级、备份、查询、热更新、失败恢复和具体命令见 `persistent_runtime_orchestration_operations.md`。重要变更已追加到根目录 `changelog`；当前改动保留在工作区，未代替用户提交或部署第一部分已有改动。
