# MU R2 → 当前 O2 Prompt/Skill → Event Library V1 真实验收

> 日期：2026-08-26（Asia/Shanghai）  
> 结论：确定性链路和原子发布通过；当前 O2 输出的语义质量不通过。

## 1. 验收范围

本轮没有重新运行 CDECR，也没有调用 DeepSeek。输入为 2026-08-20 已 FINALIZED 的 MU 近期 162 篇 R2 SQLite：

```text
.tmp/cdecr/mu_recent_news_stress_20260820_r2/cdecr_mu_recent_162.sqlite3
```

链路为：

```text
旧 R2 SQLite（只读校验，源文件不变）
→ 隔离副本
→ FrozenRuntimeSnapshot
→ 742 Delta + 185 Runtime Package
→ 新 Event Library，base version = 0
→ 当前工作区 O2 Prompt / Internal Skill
→ Revision Bundle validator
→ Importer
→ 原子发布 V1
→ 零模型幂等复验
```

源 SQLite `quick_check=ok`，且运行前后 SHA-256 均为：

```text
15529adb8c2bbbbcaea41fb4352b051b1247e0919edfe22e8059c04e78493be3
```

## 2. 冻结输入

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

实际使用的 10 个 O2 Prompt/skill 逐文件指纹保存在：

```text
.tmp/o2-mu-r2-current-prompts-20260826-real-01/inputs/prompt_manifest.json
```

其 canonical manifest SHA-256 为：

```text
552f1863716aae19ed481483de4ebefc49a46b244977561b378ab9662d49b56e
```

## 3. 真实模型执行

- 模型：`gpt-5.6-luna`
- reasoning effort：`max`
- thread：`01a039f3-4103-73d2-a909-c6850008a0ec`
- 成功 turn：10 / 10
- 组成：1 Survey + 8 Package-aware Wave + 1 Global Reconciliation
- retry：0
- repair：0
- 首个 job 创建：`2026-08-25T17:23:58.295561Z`
- 最后 job 完成：`2026-08-25T18:34:05.008639Z`
- 模型阶段 wall：约 70 分 7 秒

| Token | 数量 |
|---|---:|
| Input | 1,319,537 |
| Cached input | 1,304,064 |
| Output | 6,500 |
| 其中 reasoning output | 5,919 |
| Total | 1,326,037 |

## 4. Validator、Importer 与发布

Delta 覆盖严格闭合：

| 处置 | 数量 |
|---|---:|
| 被 Fact 消费 | 591 |
| `KEEP_PENDING` | 149 |
| `DROP_INVALID` | 2 |
| 总计 / 唯一 Delta | 742 / 742 |
| 跨处置重叠 | 0 |

模型生成的 151 条 residual 使用了兼容 alias `disposition`，而冻结契约字段是 `resolution`。Importer 确定性归一化后留下 1 条 `RESIDUAL_WIRE_NORMALIZED` warning，因此首次 Validator 为 `PARTIAL`，但没有 Bundle-level blocker，最终原子发布为 `PARTIAL_PUBLISHED`。

发布结果：

| 项目 | V1 |
|---|---:|
| Active Event | 177 |
| Active Fact | 546 |
| Pending Delta | 149（20.08%） |
| Dropped Delta | 2 |
| Known Index Event coverage | 100% |
| Event Detail Fact coverage | 100% |
| Event Library SQLite quick/integrity check | `ok` / `ok` |

## 5. 语义质量结论

本轮不能只按“成功发布”判为质量通过。Published V1 暴露出以下明确问题：

1. 177 个 Event 全部为 `is_important=false`。
2. 177 个 Event 全部为 `include_in_reference_view=false`，两个 Reference View 只有 40 bytes 的空视图头。
3. 108 / 177（61.02%）Event 的 `occurrence_time_precision=UNKNOWN`。
4. 存在 1 组重复标题：`KeyBanc raises Micron price target on July 14, 2026`，对应 E113 和 E151。
5. 最大 Event 含 38 个 Fact，另有 25、15、13 Fact 的大 Event；这不自动证明错误合并，但需要 occurrence-boundary 人工复核。
6. 模型仍输出旧 residual alias，说明当前 Prompt/skill 没有稳定约束最终 wire 字段。

因此本轮判断是：

- 冻结 Runtime→Delta→O2→Validator→Importer→原子发布：**通过**。
- Delta 全覆盖、无重复处置：**通过**。
- 失败恢复需求：本轮没有触发。
- Reference View 与重要性选择：**不通过**。
- occurrence time 完整性：**不通过**。
- 整体 O2 语义质量：**不通过，不能作为当前 Prompt 的发布批准证据**。

## 6. 幂等复验

使用同一 run、同一 Snapshot、同一 Prompt manifest 和同一 Event Library 复验：

- 模型调用：0
- 发布前版本：V1
- 发布后版本：V1
- Event / Fact：177 / 546，保持不变
- Validator：PASS
- 源 R2 SQLite：SHA-256 保持不变

这证明 Published 后重放不会创建 V2，也不会重复调用模型。

## 7. 留存产物

```text
.tmp/o2-mu-r2-current-prompts-20260826-real-01/
├─ acceptance_report.json
├─ idempotency_report.json
├─ event-library.sqlite3
├─ inputs/
│  ├─ frozen_runtime_snapshot.json
│  ├─ prompt_manifest.json
│  └─ r2-registry-copy.sqlite3
├─ o2-local/o2-mu-r2-current-prompts-20260826-real-01/
└─ published/
   ├─ event_library_v1.json
   ├─ event_library_v1.md
   ├─ known_event_index_v1.md
   ├─ reference_view_agent_v1.md
   └─ reference_view_human_v1.md
```

主要发布 JSON SHA-256：

```text
c8aee65870e64872556ed1f276cb2cb4e9bc11f4e877ab3d5e859b9f3bda36f9
```
