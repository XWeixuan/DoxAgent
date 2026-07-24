# CDECR N4 Judge 协议 A/B 评估报告

日期：2026-07-23  
结论状态：**语义方向成立，但暂不满足直接放行标准**

## 1. 结论

本轮在冻结的 30 篇真实文章、229 条 Grounder drafts 上完成了旧版 N4 与重构版 N4 的真实 M4 A/B。模型为 `qwen3.7-max`，每篇每个 arm 最多一次 repair；实验未写入生产 Registry，也没有供应商、网络或密钥失败。

重构版达成了两个核心目标：

- 首轮 user payload 从 359,014 字符降至 282,064，下降 21.4%。
- 首轮输入 token 从 180,038 降至 151,917，下降 15.6%；首轮输出 token 从 38,697 降至 22,049，下降 43.0%。
- 旧版几乎不会修订字段；重构版在主实验的成功文档中产生 99 次时间修订，其中 51 次把“无事件边界但带精度”改为 `UNKNOWN`，47 次修改了事件边界，直接覆盖了冻结 Grounder 语料中最主要的执行日期泄漏和财期误用问题。
- 重构版首次识别出 1 个真正的 `DUPLICATE`，并通过封闭动作协议阻止了旧版 `REJECT + revised_mention` 这类无效组合。

但原始主实验不能判定为通过：

- 重构版首轮严格通过率仅 13/30（43.3%），旧版为 29/30（96.7%）。
- 一次 repair 后，重构版最终成功 25/30（83.3%），旧版为 30/30。
- repair 导致重构版总输入 token 升至 256,004，比旧版高 33.8%，抵消了首轮节省。
- 失败主要来自冻结 Grounder drafts 已存在但旧协议未拦截的时间语义不一致，以及模型未在一次 repair 中同时修完所有命令；这说明新校验是有价值的，但 Grounder 边界和 repair 反馈仍需收敛。

因此建议保留本轮 DTO、Prompt、短 ID、字段级修订、Evidence 合并等架构改造，但把 N4 上线门槛保持关闭，直到用当前已加固的 Grounder 重新生成无时间语义污染的 drafts，并再次达到首轮与最终结构通过标准。

## 2. 实验设计与可复现边界

| 项目 | 固定值 |
|---|---|
| 冻结语料 | `.tmp/cdecr/grounder_quality_v5/grounder_30.json` |
| 语料 SHA-256 | `5fd6c5f74e352da763b6767bfb6b39b359cfdca4dc5a5f5d1d4c94b3388bb467` |
| 规模 | 30 documents / 229 Grounder drafts |
| 来源快照 | `.tmp/cdecr/grounder_quality_v5/live_all.jsonl` |
| 模型 | `qwen3.7-max`（M4） |
| 旧版基线 revision | `e72c779b1a679b7c914bde454011a43d9f72b930` |
| 旧版 Schema | 5,651 chars |
| 重构版 Schema | 7,437 chars |
| repair | 每篇每 arm 最多一次 |
| 顺序控制 | 按文档交替 legacy/refactored 先后顺序 |
| 隔离 | 独立文件 checkpoint；不写生产 Registry |

评估脚本 `scripts/cdecr_evaluate_judge_n4_ab.py` 使用 `git show` 动态装载固定 revision 的旧 Prompt 与 Pydantic 契约，避免用手写近似 Schema 伪造基线。脚本同时校验冻结语料哈希及 30/229 数量，并按 `source_row_id` 保存每次请求、token、校验错误、原始无效输出和最终决策。

原始主实验保存在 `.tmp/cdecr/judge_n4_ab_v1_initial/`；其 `summary.json` SHA-256 为 `ed99b2aa9423b51965af970bff8ab1fe76396288f8e44de1f01212aee49dbbf4`。最小生产校验修复后的失败项补跑保存在 `.tmp/cdecr/judge_n4_ab_v1/`，两者不得混为同一结果。

复现命令：

```powershell
uv run python scripts/cdecr_evaluate_judge_n4_ab.py `
  --output-dir .tmp/cdecr/judge_n4_ab_v1 `
  --max-workers 2
```

仅重跑失败项：

```powershell
uv run python scripts/cdecr_evaluate_judge_n4_ab.py `
  --output-dir .tmp/cdecr/judge_n4_ab_v1 `
  --max-workers 2 `
  --retry-failed
```

## 3. 原始主实验结果

| 指标 | Legacy | Refactored | 变化 |
|---|---:|---:|---:|
| 文档最终成功 | 30/30 | 25/30 | -5 |
| 首轮严格通过 | 29/30（96.7%） | 13/30（43.3%） | -53.4 pp |
| repair 后恢复 | 1 | 12 | +11 |
| 最终失败 | 0 | 5 | +5 |
| 首轮输入 token | 180,038 | 151,917 | -15.6% |
| 首轮输出 token | 38,697 | 22,049 | -43.0% |
| 含 repair 总输入 token | 191,360 | 256,004 | +33.8% |
| 含 repair 总输出 token | 41,324 | 32,831 | -20.6% |
| user payload chars | 359,014 | 282,064 | -21.4% |
| 平均 reason 长度 | 169.1 | 114.4 | -32.3% |

输出动作统计只包含最终成功文档，因此两个 arm 的分母不同：

| 动作 | Legacy（30 篇） | Refactored（25 篇） |
|---|---:|---:|
| ACCEPT | 210 | 166 |
| REJECT | 5 | 7 |
| SPLIT | 6 | 8 |
| DUPLICATE | 0 | 1 |
| MERGE_AS_ATTRIBUTE | 8 | 6 |

重构版输出字段修订：

| 字段 | 次数 |
|---|---:|
| `time` | 99 |
| `source_claim` | 10 |
| `assertion_state` | 4 |
| `predicate` | 3 |
| `event_family` | 1 |
| `open_attributes` | 1 |

Legacy 只有 1 次 `time` 修订，而且附着在 `REJECT` 决策上；旧运行时会丢弃该修订，实际不能修复 Mention。

## 4. 最小校验修复后的敏感性结果

真实输出暴露了三项应独立于模型质量修复的生产校验问题：

1. `EventTime` 的 naive/aware datetime 比较会抛出原生 `TypeError`，而不是稳定的领域校验错误。
2. 时间语义校验放在嵌套 DTO parse 阶段时，repair 只能看到第一个局部错误，无法一次获得整批错误清单。
3. 模型常返回 `changes: {}` 或略超 240 字符的审计 reason；两者不改变业务语义，适合在解析前做确定性规范化并留审计计数。

完成上述最小修复后，只对原始 5 个失败文档执行一次失败项补跑：

| 指标 | Refactored sensitivity |
|---|---:|
| 最终成功 | 28/30（93.3%） |
| 首轮严格通过 | 13/30（43.3%，不变） |
| repair 后恢复 | 15 |
| 最终失败 | 2 |
| 含 repair 总输入 token | 257,772 |
| 含 repair 总输出 token | 39,174 |
| 最终已转换决策 | 206 |

剩余两项失败均不是 provider 故障：

- `6ccb4979...`：首轮两个 `SPLIT` 各只返回一个 replacement，repair 后仍遗漏 `ACCEPT d4` 的时间精度修复。
- `c762d097...`：首轮未完整覆盖输入 ID；repair 后 8 个 `ACCEPT` 和 `SPLIT d13` 的两个 replacement 仍保留“无边界但非 UNKNOWN 精度”。

没有继续注入针对这两篇的第二轮定制提示，因为那会改变“一次通用 repair”的 A/B 条件并美化结果。

## 5. 语义质量审计

### 5.1 时间与字段修订

重构版主实验 99 次 `time` 修订中：

- 51 次为无事件边界时改为 `UNKNOWN`；
- 47 次修改 `event_start` / `event_end`；
- 1 次为其他时间对象修订。

这与冻结 Grounder 质量报告中执行日期泄漏是最高优先级缺陷的结论一致。典型修订包括把错误注入的 2026-07-22/23 删除或改回文档可支持的 2026-06-24/25，以及不再用事件时间精度承载财务期间。

### 5.2 REJECT

人工检查了两个 arm 的全部 REJECT：

| Arm | REJECT 数 | 明显错误拒绝 | 错误拒绝率 |
|---|---:|---:|---:|
| Legacy | 5 | 2 | 40.0% |
| Refactored | 7 | 2 | 28.6% |

两个 arm 的错误拒绝都来自“事件本身有证据，但 draft 时间错误”。正确动作应是 `ACCEPT + time changes`，而不是因 Grounder 的时间污染把受支持事件整体拒绝。重构 Prompt 已明确允许字段修订，但模型在少数案例仍把校验缺陷当作 eventhood 缺陷。

重构版新增正确拒绝包括：行业泛化评论、作者持仓免责声明、媒体出镜上下文、专有股票排名信号等。旧版则把其中若干保留为独立 Mention。

### 5.3 SPLIT 与 DUPLICATE

重构版主实验产生 8 个 `SPLIT`，敏感性结果为 10 个；覆盖不同指数、不同公司、不同动作、财务结果与 guidance、不同指标和不同期间。所有成功落地的 split 均满足至少两个完整 replacement 及精确 Evidence 的结构要求。

重构版识别出 1 个正确 `DUPLICATE`：同一美国上市计划的美元估值与韩元等值表述。运行时现在会把补充 Evidence 合并到保留 Mention，而不是静默丢弃重复 draft 的互补证据。

### 5.4 MERGE_AS_ATTRIBUTE

该动作仍是主要语义风险。人工审阅显示：

- 明确合理：协议的锁定金额、上市募集金额/用途、声明上下文等。
- 可疑或错误：把不同财务指标合并、把独立供应协议并入宽泛合作、把独立市场价值/涨幅状态互相合并、把设施投资事实并入未来 capex guidance。

重构契约已经禁止 merge 指向 `SPLIT/REJECT/DUPLICATE/MERGE`，但“属性还是独立事件”的判定仍需要更具体的指标、动作和时间差异负例。不能仅凭结构通过率宣称属性归并质量已达标。

## 6. 根因与已实施加固

本轮真实评估促成以下代码加固：

- Grounder 输出和 Judge 输入都执行同一 `validate_event_time_semantics`，把非法时间对象阻断在最早可修复边界。
- Judge 对所有 `ACCEPT`、`SPLIT` replacement、`MERGE_AS_ATTRIBUTE` attribute 及 keep target 错误做整批聚合，repair 可一次看到完整错误清单。
- `changes: null/{}` 规范化为省略，并记录 `accepted.changes.omitted_empty`。
- 超过 240 字符的 reason 确定性截断，并记录 `reason.truncated_to_240`。
- `EventTime` 对 naive/aware timezone 不一致给出稳定 Pydantic 校验错误。
- 评估器保留每次无效输出、校验错误、token、延迟和 request ID，支持断点续跑与失败项补跑。

这些修复提高了敏感性结果，但不改变原始主实验结论。

## 7. 放行建议

当前建议为 **保留实现、暂缓 N4 质量门放行**。下一轮应：

1. 用当前 Grounder 时间语义校验重新生成同一 30 篇文章的 drafts，而不是继续让 N4 为旧脏输入承担全部修复。
2. 在新的冻结输出上重复同条件 A/B；禁止增加第二次 repair 或文档专用提示。
3. 增加针对 `MERGE_AS_ATTRIBUTE` 的负例：不同 metric、不同 action、不同 subject、actual 与 guidance、原因与结果均不得归并。
4. 增加“受支持事件 + draft 字段错误必须修订，不能 REJECT”的明确反例。
5. 放行阈值建议：
   - 首轮 Schema + 语义校验通过率不低于 95%；
   - 一次 repair 后最终通过率 100%；
   - 全量 REJECT 人审无“有证据事件仅因可修字段错误而拒绝”；
   - MERGE 人审无跨 metric/action/time 的错误归并；
   - 含 repair 的总输入 token 仍低于 Legacy。

在达到这些阈值前，重构版可以作为实验协议继续迭代，但不应被描述为已经完成生产质量验收。
