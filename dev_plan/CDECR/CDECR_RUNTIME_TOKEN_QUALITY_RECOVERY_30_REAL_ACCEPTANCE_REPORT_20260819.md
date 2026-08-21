# CDECR Runtime Token / Quality Recovery：30篇真实验收报告

## 1. 结论

方案代码已经完整落地并通过本地回归，但真实30篇验收 **FAIL**，当前版本不能进入MU300。

本轮保留了两次全新Registry：

- R1完成全流程，但百炼内容审查拒绝2篇，只完成28/30；运行中暴露Field repair错误分类问题。
- 修复该问题后的R2完成29/30单文档和Field/N9/Atomic，随后Parent阶段长时间无进展，按异常熔断中止；R2没有产生Package。

因此不存在一份可以宣称“最终代码30/30通过”的正式结果。R1可用于完整链路和方向性质量评估，R2可用于验证Field repair修复；两者都不能替代正式30/30发布验收。

## 2. 实施范围

已完成：

1. Field planned Prepare改为有界并发，candidate dimensions改为任务局部显式输入，blocker审计延迟到稳定Apply。
2. Field初始输出与单项repair共用本地validator；repair固定使用请求内`t1`，每次真实repair均写一条`model_calls`及一条结果审计。
3. provider失败仍只影响对应任务；本地非法输出只对该项fallback，不伪装成provider失败。
4. N9 overlap packing默认关闭，保留开关和旧实现。
5. 删除方案指定的Parent Induction边界句，并升版Bulk graph、Field policy、Parent checkpoint，阻止旧产物误复用。
6. PRIMARY normalization继续shadow，增加原draft、拟议结果、repair结果的hash-only关联与短字段差异码，不改变Mention输出。
7. R2运行中发现同一external identity重复创建可能触发`ImmutableRecordConflict`；最终代码已改为Apply前复用已存在external identity，并增加回归。

配置保持百炼、`deepseek-v4-flash-0731`、M2/M3/M4 effort=`none/low/high`、JSON Object、strict关闭；测试语料、Manifest和并发配置未改变。

## 3. 本地验证

- `tests/cdecr`：319 passed，3 skipped。
- Ruff：通过。
- strict mypy（`src/cdecr`）：通过。
- `git diff --check`：通过；只有既有LF/CRLF提示。

## 4. 真实运行产物

| 产物 | 路径 | 状态 |
| --- | --- | --- |
| R1 Registry | `.tmp/cdecr/mu30_runtime_token_quality_recovery_20260819_r1.sqlite3` | FINALIZED，28/30 |
| R1 runtime report | `.tmp/cdecr/mu30_runtime_token_quality_recovery_20260819_r1_report.json` | 完整 |
| R1 Package Gold | `.tmp/cdecr/mu30_runtime_token_quality_recovery_20260819_r1_package_gold_eval.json` | 本地评估完成 |
| R2 Registry | `.tmp/cdecr/mu30_runtime_token_quality_recovery_20260819_r2.sqlite3` | PARENT_RESOLVE中止，29/30 |
| 上轮基线 | `.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2.sqlite3` | FINALIZED，30/30 |

## 5. 完整性与失败根因

### 5.1 R1

- 文档/Event：28/30。
- Mention：230；active Atomic：164；Package：32。
- 两篇均在Grounder被百炼以`provider_datainspectionfailed`拒绝。
- 另有1次Grounder item repair `invalid_json`，已局部化。
- Field有11个task被错误标为失败：本地错误码`TARGET_NAMESPACE_FORBIDDEN`被通用provider分类误读为HTTP forbidden。该代码问题已在R2前修复。

### 5.2 R2

- 单文档：29/30；1篇再次遭`provider_datainspectionfailed`，说明外部内容审查可复现但并非固定命中两篇。
- Field：488成功、1失败。repair的本地错误分类已修复：90次真实repair对应90条`model_calls`和90条结果审计，75次非法结果均按项fallback，0次被误标为provider失败。
- 唯一Field失败为Apply时`ImmutableRecordConflict`；最终代码已改为先复用现有external identity，并通过专项回归，但该修复之后没有第三次真实30篇重跑。
- Parent已完成15个主请求和2个repair，之后长时间无任何新调用或状态进展；为避免无限等待，R2在`PARENT_RESOLVE`熔断中止。

结论：30/30完整性Gate失败；不能把R1的28篇或R2的29篇外推为30篇结果。

## 6. 效能与Token

### 6.1 R1与上轮方向性对比

| 指标 | 上轮30篇 | R1 28篇 | 判断 |
| --- | ---: | ---: | --- |
| 首轮wall | 1,557,222 ms / 25.95 min | 806,869 ms / 13.45 min | 明显恢复，但样本少2篇 |
| 可审计总Token | 1,737,351（且漏记96次Field repair） | 1,577,202（含76次Field repair） | 记账已闭环，但28篇仍超过1.42M门槛 |
| 每成功文档Token | 57,912 | 56,329 | 约-2.73% |
| Field artifact wall | 1,031.9 s | 388.5 s | -62.4%，但仍高于360 s门槛 |
| Field planned Prepare | 859.6 s | 285.5 s | -66.8% |
| Field Decide | 19.1 s | 21.7 s | 基本稳定 |
| Field Apply | 59.8 s | 34.3 s | 改善 |

R1 Prepare telemetry证明线程真实重叠：425 groups、100 workers、`prepare_max_active=100`。并发不是“只配置未生效”。但P95 Prepare为217.6秒，表明SQLite读取和冷启动embedding仍形成长尾；完整Field wall 388.5秒仍未过360秒Gate。

### 6.2 R2截止态

- 截止熔断前501次调用，input 1,151,028、output 317,687，总Token 1,468,715；尚未执行Package，不能与完整运行作总成本A/B。
- Field artifact wall约546.6秒；Prepare 369.2秒、Decide 94.8秒、Apply 40.1秒。
- 489 groups、100 workers、`prepare_max_active=100`，但冷启动长尾仍明显。

### 6.3 repair记账

| 运行 | 真实repair请求 | `model_calls` | repair结果审计 | fallback |
| --- | ---: | ---: | ---: | ---: |
| R1 | 76 | 76 | 76 | 54 |
| R2 | 90 | 90 | 90 | 75 |

“repair请求数=`model_calls`”已达到；Token不再漏记。R2中大量repair仍非法，主要错误为Pydantic validation、target namespace forbidden/unknown，说明wire修复虽然可审计，但模型遵约率仍偏低，75个fallback存在Field质量风险。

## 7. PRIMARY shadow结论

R1记录38条初始shadow，其中20条为静态safe candidate；29条有repair outcome：

| outcome | 数量 |
| --- | ---: |
| `EQUIVALENT_TO_REPAIR` | 1 |
| `CRITICAL_FIELD_DIFFERENCE` | 11 |
| `REPAIR_SPLIT_OR_MULTI_OUTPUT` / partial | 2 |
| `REPAIR_FAILED` | 1 |
| `NOT_SAFE_CANDIDATE` | 14 |

R2截止态也只有2条`EQUIVALENT_TO_REPAIR`，另有5条critical difference。结论明确：PRIMARY规则只能继续shadow，绝不能投入Apply；新增outcome审计成功证明了此前“静态safe”远不等于“与真实repair语义等价”。

## 8. Package方向性质量

R1在68/164个高置信Atomic对齐子集上的Package Pair指标：

- Precision 68.17%；Recall 40.49%；F1 50.80%。
- Micron earnings被拆为7个组件`[25,4,3,3,3,1,1]`，missed links=465。
- 出现严重跨父事件误合：一个Package同时包含Micron财报、IDC/Counterpoint Apple展望、Siri兼容和Tim Cook memory表述。

上轮报告为89.80% / 32.12% / 47.31%，但对齐集合和成功文档数不同，不能做严格pp A/B。方向上Recall/F1上升而Precision大幅下降，符合Parent边界放宽后的过合并风险，已经满足方案中“Precision出现明确且不可接受的系统性下降”的回滚审查条件。当前未自动回滚；发布前应单独恢复或重新改写这条Parent边界，而不是保留现状进入MU300。

## 9. Gate与发布判断

| Gate | 结果 |
| --- | --- |
| 30/30 | 失败：R1 28/30，R2 29/30 |
| Field wall≤360 s | 失败：R1约388.5 s；R2约546.6 s |
| 全流程wall≤18.6 min | R1方向性通过13.45 min，但只有28篇；R2未完成 |
| Total Token≤1.42M | 失败：R1 28篇已1.577M；R2未完成已1.469M |
| repair调用/审计完整 | 通过 |
| N9 packing关闭 | 通过 |
| Parent Repartition=0 | 通过 |
| PRIMARY仅shadow | 通过，且证据明确不应Apply |
| Package质量不恶化 | 失败：严重误合与Precision回归 |
| 可进入MU300 | 否 |

最终判断：本轮修复恢复了大部分Field钟墙并补齐repair成本审计，但尚未达到效能门槛；外部内容审查导致30/30无法完成，R2又出现Parent长尾；Package边界放宽造成不可接受的过合并风险。进入下一轮真实验收前至少需要：

1. 保留本轮Field并发和repair审计修复；
2. 保留N9 packing关闭；
3. 针对Parent删除句执行窄回滚/重新评估；
4. 处理或明确百炼`provider_datainspectionfailed`的文档级容灾口径；
5. 继续优化Field Prepare的SQLite/embedding长尾；
6. 修复Parent请求长尾/超时熔断后，再用全新Registry做一次真正30/30测试。

