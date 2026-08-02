# CDECR N12 / Anchor / 碎片化 / Mention 调优交付（2026-08-01）

## 总结判断

计划中的实现、离线审计和五轮 Mention-only 测试已完成；没有运行计划明确排除的完整 CDECR
端到端验收。可安全保留的实现已经落在工作区，但两个质量门未通过：

1. N12 字典协议降本明显，8-task smoke Input Token -68.67%，但 relation 一致率 75%、exact task
   25%，且全量 canary 遭遇 provider arrearage，故保持 `shadow`，`on` 继续拒绝。
2. Mention 最佳轮 P/R/F1=82.67%/69.40%/75.46%，较执行前当前版恢复，但未达到
   85%/80%；最终选择第3轮并回滚第4/5轮的 rejected review。

这不是“全部通过”的交付。当前状态适合继续代码审查和后续固定映射验收，不适合宣称新 N12
协议或 Mention 质量已经发布达标。

## 实现范围

- N12：请求内 Package 字典、request-local ID、有界 Package/Event Card、redirect 后 canonical member
  去重、shadow/canary 审计和 node-only A/B 工具。
- Anchor：父边界 identity key、泛 hint 拒绝、同源/同 issuer+period 的保守 canonicalization、Judge
  split 继承、Mention→Atomic→Package 全集合聚合、冲突集合保留和 N12 最多4 Anchor 显示。
- Mention：item-local Grounder repair 与 missing recovery 保留合法项；整篇零 candidate 才进行一次
  Dreamer recovery；统一 atomicity/字段守恒 Prompt；实验性 rejected review 已完全回滚。
- 评估：新增只读 Anchor 明细和通用 Source-occurrence 碎片化计算器，不进入运行时决策。

## 结果索引

- N12 Node A/B：`dev_plan/CDECR/CDECR_N12_NODE_AB_REPORT_20260801.md`
- Anchor 254条审计：`dev_plan/CDECR/CDECR_ANCHOR_AUDIT_20260801.md`
- Anchor 全量机器明细：`.tmp/cdecr/anchor_audit_20260801/anchor_audit.json`
- 碎片化三轮比较：`dev_plan/CDECR/CDECR_FRAGMENTATION_OFFLINE_REPORT_20260801.md`
- Mention 五轮与完整 Prompt 变更：`dev_plan/CDECR/CDECR_MENTION_ONLY_TUNING_REPORT_20260801.md`
- N12 smoke：`.tmp/cdecr/n12_node_ab_20260801_smoke/n12_ab_smoke.json`
- N12 full（含欠费降级证据）：`.tmp/cdecr/n12_node_ab_20260801_full/n12_ab_full.json`
- Mention 最终选择轮：`.tmp/cdecr/mention_tuning_20260801_round3/`

## Anchor 结论

固定 Registry 的 254 Mention 中 101 条带 hint（39.76%）。保守确定性代理得到：SUPPORTED 130、
Anchor Recall 76.15%、Precision 98.02%、missing supported 31、invalid 2。应继承的 Judge 路径
72/72 成功；两条 analyst estimate 主动丢弃 earnings hint，属于正确边界。当前 101 个 link 收敛
为15个 root，但 68个 Package 中59个仍无 Anchor，最大 earnings Package 保留12个冲突 Anchor。

这些 P/R 是定位代理，不是独立人工 Gold，不能作为 release gate。明确根因分布是 Grounder未生成
为主，另有1条 market reaction错误继承 earnings、1条 partnership hint复述 Mention；运行时修复
集中在保守 canonicalization 和层级聚合，没有强迫所有 Mention 输出 hint。

## 碎片化结论

离线工具已具备完整指标口径，但历史三轮没有保存统一的 Source occurrence 映射，所以无法诚实补齐
所有 Fragmented Cluster Rate 和 Package conditional 指标。现有可证据化趋势是：2026-07-30
Atomic/Package fragmentation 60.89%/61.05%，明显过拆；2026-07-31 降至 provisional
6.27%/1.16%，却伴随 18-member 污染 Atomic 和 51-Atomic Package，说明低 fragmentation 主要由
强聚合取得，必须结合 71.37%/85.82% Precision 解读。

## 回归

- `tests/cdecr/test_single_document.py`: 31 passed。
- `tests/cdecr/test_cross_document.py` + `test_canonical_field_resolution.py`: 47 passed。
- Package Engine、Atomic Sidecar、fragmentation：15 passed。
- 相关 12 个源码/脚本 mypy 通过；相关源码、测试和脚本 Ruff 通过；`git diff --check` 通过。

## 未闭环项

- provider 恢复后重跑同一 138-task N12 full node A/B；此前不得启用 `n12_wire_protocol=on`。
- 修订/固化与当前最小事实业务边界一致的 Source-occurrence Gold。旧 Gold 中多处把不同 PRIMARY
  metric/action 合为一条，继续针对它优化会反向诱导 compound。
- 下一轮 Mention 优化应集中解决 Dreamer 分布性 missing 与 Grounder item repair 稳定性，不能
  恢复 rejected review 或继续叠加通用扩召层。
- 下一次完整验收生成唯一 redirect-resolved mapping，补齐 Atomic/Package FCR 与 Package
  conditional fragmentation 后再判断全流程质量。

