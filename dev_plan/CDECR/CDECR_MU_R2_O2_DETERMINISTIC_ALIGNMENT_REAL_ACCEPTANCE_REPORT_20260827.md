# MU R2 O2 Deterministic Alignment 真实验收报告

## 结论

本轮按 `O2_EVENT_LIBRARY_CONTEXT_AND_DETERMINISTIC_ALIGNMENT_REPAIR_PLAN_20260826.md`
完成确定性程序、Schema、编排与 Prompt 注入对齐，并复用既有 MU R2 FINALIZED Registry
执行隔离的新 O2 初始化验收。

最终结果：

- 不重跑 CDECR；
- Frozen Runtime Snapshot、742 Delta、185 Runtime Package 与旧验收输入保持不变；
- 新 Event Library 从 base version 0 原子发布 V1；
- Revision Bundle validator：`PASS`；
- 独立 semantic validation：`PASS`，0 个残留 issue；
- 零模型幂等回放仍为 V1，模型调用数为 0；
- 原 MU R2 Registry SHA-256 前后不变。

## 冻结输入

| 项目 | 结果 |
|---|---:|
| Source Message | 162 |
| FINALIZED epoch | `bulk-epoch:e4cd5fcfc266936294162495` |
| Frozen Snapshot | `runtime-snapshot:31200a6c357222c577313653` |
| Snapshot SHA-256 | `0c40ed89c63b021487c4acfa1b4d569eea2d035f7f8a0af0416baf331e1ecc12` |
| Atomic / Delta | 742 / 742 |
| Runtime Package | 185 |
| Delta batch | `delta:1e167a3f17b09f81d5d3d544` |
| Base Event Library version | 0 |

源 Registry：

```text
.tmp/cdecr/mu_recent_news_stress_20260820_r2/cdecr_mu_recent_162.sqlite3
```

运行前后 SHA-256：

```text
15529adb8c2bbbbcaea41fb4352b051b1247e0919edfe22e8059c04e78493be3
```

## 本轮确定性修复

1. Attempt seeding 收敛为一套实现，`skill.md` 确定性组合 Foundation 与当前 Stage，
   task 同时记录源文件 hash 和组合 hash。
2. `task.json` 固化 `content_input_order`、Frozen as-of、Bundle identity、必读文件、
   Event Detail 白名单和 Reference Review policy/outcome branches。
3. Runner 严格校验 stage、base、当前 attempt Bundle 路径、Delta coverage 与
   `validation=NOT_RUN`；不可信结果标记 run failed，Published 不变。
4. Frozen View 物化完整 Bundle/中间产物 Schema、Schema index、字段说明和通用正例。
5. Event Detail 不再全量上传；Candidate Map 阶段物理不可读，后续 attempt 只复制白名单 Detail。
6. D1 C1/C3/C5 正文按 Published ArtifactRef 的 run/path/size/SHA-256 校验后复制到
   O2 Frozen View，并参与 Frozen identity。
7. Validator/Importer/Repository 按 Schema 支持 `ACTIVE/MERGED/SUPPRESSED`、同 Bundle
   `T#` retirement、redirect/lifecycle/relationship 无环校验和同事务稳定 ID 分配。
8. Reference Review 的 10/30/7 时钟、reviewed-at、mode/reason/changed/next 全部使用
   Frozen as-of，由确定性程序生成或校正；review-only 无变化不创建 V+1。
9. Published 排序和 Reference Review 共用 occurrence anchor：月末、季末、年末、区间末，
   `UNKNOWN` 排在有时间 Event 之后。
10. 结构校验与 semantic report 分离；全零 importance/reference、UNKNOWN 比例、重复 title、
    超大 Event、缺失关系和时间字段规范由独立语义门处理。空 important 集合不再返回 recall 1.0。

## 真实 O2 运行

- run id：`o2-mu-r2-deterministic-alignment-20260827-real-01`
- 模型：`gpt-5.6-luna`
- reasoning effort：`max`
- 同一 thread：`01a03f71-3ed7-7be1-a2f7-7cae7f68edff`
- 成功阶段：1 Survey + 8 Wave + 1 Global retry + 1 Bundle Repair
- 失败阶段：首次 Global Reconciliation 达到 1800 秒 Worker timeout；没有重跑前置阶段。
- 恢复：沿同一 run/thread/checkpoint 创建
  `o2-global-reconciliation-retry-001`，单 Turn timeout 提高至 3600 秒。

远端 workspace 共存在 12 个 immutable attempt input（包含首次超时的 Global attempt）。
逐个复核均满足：

- 包含 `# Foundation Contract`；
- 包含 `# Current Stage Contract`；
- `skill.md` 实际 SHA-256 等于 task 的 `combined_skill_sha256`。

首次可解析 Global Bundle 被确定性语义门拒绝，原因只有：

- 11 个 `SUBJECT_TIME_MUST_USE_SAME`；
- `UNKNOWN_OCCURRENCE_RATIO_HIGH`。

`o2-repair-001` 仅对这些 validator 指定问题进行修复。Global retry 和 repair 的
`O2RunResult` 均报告精确 coverage：742 total、580 resolved、162 pending，且
stage/base/path/`NOT_RUN` 全部符合当前 attempt 契约。

## 发布结果

| 项目 | 结果 |
|---|---:|
| Published version | V1 |
| Event | 185 |
| Fact | 518 |
| Resolved Delta | 580 |
| Pending Delta | 162 |
| Drop Invalid | 0 |
| Duplicate Fact residual | 0 |
| Important Event | 135 |
| Reference Event | 80 |
| Reference important recall | 0.5925925926 |
| UNKNOWN occurrence | 0 |
| Structural validator | PASS |
| Semantic issue count | 0 |
| Semantic release gate | PASS |

`PARTIAL_PUBLISHED` 表示 742 个 Delta 中有 162 个被明确保留为 `KEEP_PENDING`；它不表示
Bundle 结构或事务失败。Bundle validator 与 semantic gate 均为 PASS。

## 幂等与不可变性

同一 run 再次执行 `--resume-existing`：

- Published before = V1；
- Published after = V1；
- model jobs = 0；
- Event/Fact = 185/518；
- source Registry unchanged = true。

旧 MU 真实验收目录、旧 Published V1、旧 Bundle 和源 Registry 均未覆盖。本次结果位于：

```text
.tmp/o2-mu-r2-deterministic-alignment-20260827-real-01/
```

关键证据：

- `resumed_acceptance_report.json`：真实 checkpoint 续跑、repair 和发布结果；
- `idempotency_report.json`：零模型幂等回放；
- `published/event_library_v1.json`：完整 Published V1；
- `published/known_event_index_v1.md`：按 occurrence anchor 排序的 Known Event Index；
- `published/reference_view_agent_v1.md`：Published Reference View。

## 验证

```text
Ruff: PASS
strict mypy: PASS (20 source files)
focused tests: 37 passed
real structural validator: PASS
real semantic validation: PASS
zero-model idempotency: PASS
```

本次 MU 冻结 Registry 验收按指定可行链路没有注入 D1。D1 正文提升链路通过独立集成测试
验证：C1/C3/C5 正文缺失、未 Published、size 不符或 SHA-256 不符会在 O2 启动前失败；
正文完整时会复制到 Frozen View 并记录本地 hash。该边界没有用不相关的 MU D1 产物伪造
真实输入。
