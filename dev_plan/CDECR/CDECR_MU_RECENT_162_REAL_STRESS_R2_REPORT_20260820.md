# CDECR MU 近期162篇真实压力测试 R2 简报（2026-08-20）

## 结论

本轮使用与R1完全相同的162篇固定新闻语料和全新隔离Registry。首段运行在N9留下3个`FAILED_RETRYABLE`任务后安全退出；随后复用同一Registry断点续跑，未重跑162篇单文档或已成功的N9任务，最终epoch成功`FINALIZED`。

运行完整性通过：162/162文档、162/162跨文档结果完成，产生898 Mention、742 Atomic、185 Package和12条External Relation。SQLite `quick_check`与`integrity_check`均为`ok`，最终Bulk task无`RUNNING`残留。

本轮不做Gold标注。基于确定性边界代理，Atomic仍存在明显大簇与潜在误合并风险，因此只能判定“全流程与断点恢复通过”，不能判定聚合质量通过。

对比口径不是严格模型隔离A/B：R1持久化配置为`deepseek-v4-flash-0731`；R2启动时工作区当前有效配置已变为DeepSeek官方Provider的`deepseek-v4-flash`。本轮没有修改Provider或模型配置，也没有因该差异重新运行。任务账本闭合、Field错误类型和断点恢复可以直接验证修复机制，但质量、Token和模型输出稳定性的变化不能全部归因于本轮代码修复。

## 1. 输入与产物

| 指标 | R2结果 |
| --- | ---: |
| 固定语料 | 162篇 |
| 单文档成功 | **162 / 162（100%）** |
| 跨文档成功 | **162 / 162（100%）** |
| Mention | 898 |
| Mention / Evidence Schema有效率 | 100% / 100% |
| Atomic | 742 |
| Package | 185 |
| External Relation | 12 |
| Epoch | **FINALIZED** |
| SQLite检查 | `ok` / `ok` |
| R2有效M2/M3/M4 | DeepSeek官方 / `deepseek-v4-flash` |

## 2. 断点恢复

第一次进程结束时：

- 162/162单文档成功；
- 2,107个Field任务全部成功；
- N9为895个`SUCCEEDED`、3个`FAILED_RETRYABLE`；
- Atomic Apply尚未执行，Atomic/Package均为0，未用伪singleton污染结果。

同Registry恢复后：

- 898个N9任务最终全部`SUCCEEDED`；
- 887个N9任务attempt=1，11个attempt=2；
- Atomic Apply 23个chunk、N9 Late任务全部成功；
- 最终Bulk task `RUNNING=0`；
- 幂等复验新增模型调用、Mention、Atomic、Package均为0。

这证明本轮修复实现了“局部Provider失败→任务级可恢复→补齐后只Apply一次”，没有回到全量重跑或失败即伪造Atomic的旧行为。

## 3. 用时与Token

| 指标 | R1旧失败运行 | R2完整运行 |
| --- | ---: | ---: |
| 完成文档 | 144/162 | **162/162** |
| 完成跨文档结果 | 0/162 | **162/162** |
| 模型调用 | 1,707 | 1,814 |
| Input Token | 3,290,493 | **5,018,821** |
| Output Token | 1,014,380 | **1,342,513** |
| Total Token | 4,304,873 | **6,361,334** |
| Repair调用 | 374 | **131（-65.0%）** |
| 失败调用 | 401 | **11（-97.3%）** |

R2首段进程约31分28秒，恢复段约10分58秒，合计活跃运行约42分27秒；从首次启动至最终完成约56分55秒，其中包含按要求等待到45分钟检查点形成的约14分29秒人工等待。最终报告中的约10分40秒wall只覆盖恢复段，不能冒充整轮fresh wall。

R2总Token较R1增加47.8%，主要因为R2实际完成了Atomic、Parent与Package全链路，而R1在Atomic Decide前终止，不能据此判断修复导致单位成本上升。Field core+repair Token由619,048降至432,198（-30.2%）。

此外R1/R2模型版本和Provider不同，Token与时延只能作为运行实付结果比较，不能作为单变量优化收益。

R2主要Token占比：Atomic Coreference 19.61%、Grounder 15.08%、Judge 14.48%、Parent Induction 8.20%、Dreamer 7.32%、Grounder Item Repair 6.72%、Field Coreference 6.56%。

## 4. 异常与修复验证

- 最终11次失败调用：10次`invalid_json`、1次Atomic `provider_error`；均被局部repair或任务级resume吸收，没有造成文档失败。
- Field产生447条`FIELD_WIRE_NORMALIZED`审计；这些可确定性修正项没有进入LLM repair。
- Field item repair仅15次，15次均成功；R1为263次且大量repair后仍非法。
- R2未再出现`TARGET_NAMESPACE_FORBIDDEN`、`TARGET_NAMESPACE_UNKNOWN`或`LINK_CANDIDATE_UNKNOWN`失败调用。
- Provider短时故障没有触发全局从头重跑，也没有留下未闭合N9账本。

## 5. 无Gold质量代理

| 代理指标 | 结果 |
| --- | ---: |
| Atomic singleton | 649 / 742（87.47%） |
| Package singleton | 71 / 185（38.38%） |
| 最大Atomic | 78 Mention |
| 最大Package | 48 Atomic |
| size≥10 Atomic / Package | 13 / 21 |
| Atomic hard-cannot-link violation | 237 / 396,641 pair |
| Reaction进入earnings Package | 25 / 280 case |

最大Atomic为Citigroup评级/目标事实的78-Mention簇；此外存在36、28、28等大Atomic。大簇可能包含大量转载或同一行动的重复报道，但237个确定性hard-cannot-link violation说明不能将其全部解释为正确聚合。

最终`acceptance_passed=false`并非流程未完成，而是固定验收器还要求M4人工/Gold复核完成且确定性边界零违规。本轮按用户要求不新增Gold，`m4_review_status=PENDING`属于预期；但上述边界违规属于真实质量风险。

## 6. 最终判断

- **运行与恢复能力：通过。** 162篇全流程完成，局部N9失败按断点恢复，未全量重跑。
- **Field修复效果：通过。** repair调用和Token显著下降，同类非法字段错误清零。
- **成本：可接受但不能与R1直接作等阶段A/B。** 完整运行共6.361M Token。
- **聚合质量：暂不通过。** 无Gold无法给出准召；大Atomic与262项确定性边界违规需要后续单独处理。

## 7. 留存产物

- Snapshot：`.tmp/cdecr/mu_recent_news_20260820/mu_recent_news_snapshot.jsonl`
- Manifest：`.tmp/cdecr/mu_recent_news_20260820/mu_recent_news_manifest.json`
- Registry：`.tmp/cdecr/mu_recent_news_stress_20260820_r2/cdecr_mu_recent_162.sqlite3`
- Runtime report：`.tmp/cdecr/mu_recent_news_stress_20260820_r2/cdecr_mu_recent_162_report.json`
- 首段日志：`.tmp/cdecr/mu_recent_news_stress_20260820_r2/stress.stdout.log`
- 恢复日志：`.tmp/cdecr/mu_recent_news_stress_20260820_r2/resume1.stdout.log`
