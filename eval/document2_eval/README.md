# Document 2 评估规范

本目录存放专门用于 Document 2 预期单元的 eval 循环规范。它从
`eval/blackboard_eval_contract.md`、`eval/blackboard_hard_gates.yaml`、
`eval/blackboard_rubrics.yaml` 和历史 eval 记录中抽取相关内容，并把标准收窄到
`expectation_unit` 的生成、字段复核、异议修复和稳定化 promotion。

## 文档清单

- `document2_eval_contract.md`：Document 2 eval 循环的范围、输入、SOP、证据要求和接受规则。
- `document2_hard_gates.yaml`：Document 2 必须通过的硬门槛，失败时不能宣称本轮优化成功。
- `document2_rubrics.yaml`：Document 2 软评分表，用于诊断质量差异和比较 baseline/retest。
- `document2_eval_records.md`：追加式记录模板，用于记录每轮基线、修改、复测和结论。

## 使用边界

- 这套规范只用于 Document 2，不替代完整 Blackboard 初始化 eval。
- 如果测试只停在 `GenerateExpectationDetails`，只能评估 pending patches 的生成质量，不能声称已有稳定 `expectation_unit`。
- 如果测试停在 `PromoteExpectationToBeliefState`，才可以判断 Document 2 是否形成稳定预期单元。
- `known_events`、`monitoring_config`、`monitoring_policy` 属于后置文档。Document 2 评估可以检查预期单元是否给这些文档提供足够输入，但不能因为后置文档尚未生成而直接判定 Document 2 失败。
- 常见失败应优先沿着 pending patches、字段复核 objections、delegations、`can_promote_target` promotion blockers 追踪，不能简单写成“Document 2 没有生成”。

## 推荐使用顺序

1. 先读 `document2_eval_contract.md`，确认本轮是 detail-only、review、resolve 还是 promote 级别评估。
2. 按 `document2_hard_gates.yaml` 判断是否有硬门槛失败。
3. 即使硬门槛失败，也可以按 `document2_rubrics.yaml` 打诊断分，但必须标注“不接受本轮优化声明”。
4. 将 baseline、修改、retest 和结论追加到 `document2_eval_records.md`。
5. 重要代码修改另行追加到仓库根目录的 `changelog`。
